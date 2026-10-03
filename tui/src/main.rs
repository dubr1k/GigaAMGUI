use std::{
    io,
    time::{Duration, Instant},
};

use crossterm::event::{self, Event, KeyEventKind, MouseButton, MouseEventKind};
use ratatui::{backend::CrosstermBackend, Terminal};
use ratatui_image::picker::{Picker, ProtocolType};
use serde_json::json;

mod action;
mod app;
mod batch;
mod cli;
mod commands;
mod connection;
mod i18n;
mod input;
mod keys;
mod lifecycle;
mod options;
mod pets;
mod providers;
mod queue;
mod requests;
mod results;
mod runtime;
mod session;
mod setting;
mod settings;
mod settings_page;
mod signals;
mod terminal_guard;
#[cfg(test)]
mod test_support;
mod theme;
mod theme_catalog;
mod ui;
mod worker;
mod worker_session;

use action::Action;
use app::{dispatch, App};
use cli::{apply_data_dir_argument, run_headless, strip_data_dir, utf8_args, HEADLESS_USAGE};
use i18n::strip_lang;
use keys::handle_key;
use runtime::WorkerRuntime;
use settings::{apply_settings, load_settings};
use theme::strip_theme;
use ui::draw;

fn main() -> io::Result<()> {
    let args = match utf8_args(std::env::args_os().skip(1)) {
        Ok(args) => args,
        Err(message) => {
            eprintln!("gigaam: {message}");
            std::process::exit(2);
        }
    };
    apply_data_dir_argument(&args)?;
    let (argv, lang_override) = strip_lang(strip_data_dir(args))
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidInput, error))?;
    let (argv, theme_override) =
        strip_theme(argv).map_err(|error| io::Error::new(io::ErrorKind::InvalidInput, error))?;
    match argv.first().map(String::as_str) {
        Some("--version" | "-V") => {
            println!("gigaam-tui {}", env!("CARGO_PKG_VERSION"));
            return Ok(());
        }
        Some("transcribe" | "llm") => {
            // Ctrl+C in the shell no longer reaches the worker (its own group);
            // the signal stops it through the same orderly path.
            signals::install();
            let code = run_headless(&argv)?;
            signals::reraise();
            std::process::exit(code);
        }
        Some("--help" | "-h") => {
            println!("{HEADLESS_USAGE}");
            return Ok(());
        }
        _ => {}
    }
    signals::install();
    // Everything that owns the terminal or the worker is dropped inside, before
    // a termination signal is re-raised.
    let result = run_interactive(lang_override, theme_override);
    signals::reraise();
    result
}

fn run_interactive(
    lang_override: Option<i18n::Lang>,
    theme_override: Option<theme::Theme>,
) -> io::Result<()> {
    let mut app = App::default();
    apply_settings(&mut app, load_settings(), lang_override);
    if let Some(theme) = theme_override {
        app.theme = theme; // this run only; a later save keeps it, like --lang
    }
    let mut worker = WorkerRuntime::new(&mut app);
    terminal_guard::install_panic_hook();
    let mut terminal_guard = terminal_guard::TerminalGuard::enter(app.mouse_enabled)?;
    let mut mouse_captured = app.mouse_enabled;
    let mut terminal = Terminal::new(CrosstermBackend::new(io::stdout()))?;
    app.pet_picker = Picker::from_query_stdio()
        .ok()
        .filter(|picker| picker.protocol_type() != ProtocolType::Halfblocks);
    app.pet_protocol = app.pet_picker.as_ref().map(Picker::protocol_type);
    if app.pet_enabled {
        if let Err(error) = app.refresh_pet_image() {
            app.status = error;
            app.log(app.status.clone());
        }
    }
    let mut last_pet_frame = Instant::now();
    let mut input_changed_at: Option<Instant> = None;
    while !app.exit_requested && !signals::requested() {
        worker.tick(&mut app);
        // `/mouse on|off` flips the flag; capture follows it here so that the
        // command needs no handle to the terminal.
        if app.mouse_enabled != mouse_captured {
            terminal_guard.set_mouse(app.mouse_enabled)?;
            mouse_captured = app.mouse_enabled;
        }
        if let Some((provider, path)) = app.llm_tool_check_requested.take() {
            let payload = json!({"type": "llm_tool_check", "provider": provider, "path": path});
            worker.deliver(&mut app, vec![payload]);
        }
        // Animated image frames are safe for Kitty after explicitly deleting the
        // prior layer. Other protocols remain stable rather than leaving pixels.
        if app.pet_enabled
            && app
                .pet_protocol
                .is_some_and(|protocol| protocol != ProtocolType::Halfblocks)
            && last_pet_frame.elapsed() >= Duration::from_millis(1_300)
        {
            app.pet_frame = app.pet_frame.wrapping_add(1);
            if let Err(error) = app.refresh_pet_image() {
                app.pet_enabled = false;
                app.pet_image = None;
                app.status = error;
                app.log(app.status.clone());
            }
            last_pet_frame = Instant::now();
        }
        terminal.draw(|frame| draw(frame, &mut app))?;
        if !event::poll(Duration::from_millis(80))? {
            if input_changed_at
                .is_some_and(|changed| changed.elapsed() >= Duration::from_millis(350))
            {
                input_changed_at = None;
                commands::queue_complete_input(&mut app);
            }
            continue;
        }
        match event::read()? {
            Event::Paste(text) if !app.running() => {
                commands::paste_input(&mut app, &text);
                input_changed_at = Some(Instant::now());
            }
            Event::Resize(_, _) => {
                // A resize is also a recovery point for terminal image protocols:
                // clear stale Kitty layers and force ratatui to recalculate its grid.
                terminal.autoresize()?;
                terminal.clear()?;
                if app.pet_enabled {
                    if let Err(error) = app.refresh_pet_image() {
                        app.pet_enabled = false;
                        app.pet_image = None;
                        app.status = error;
                    }
                }
            }
            Event::Mouse(mouse) => {
                let action = match mouse.kind {
                    MouseEventKind::Down(MouseButton::Left) => {
                        app.hits.hit(mouse.column, mouse.row)
                    }
                    MouseEventKind::ScrollUp => app
                        .hits
                        .hit_scroll(mouse.column, mouse.row)
                        .map(|area| Action::Scroll(area, -3)),
                    MouseEventKind::ScrollDown => app
                        .hits
                        .hit_scroll(mouse.column, mouse.row)
                        .map(|area| Action::Scroll(area, 3)),
                    _ => None,
                };
                if let Some(action) = action {
                    let commands = dispatch(&mut app, action);
                    worker.deliver(&mut app, commands);
                }
            }
            Event::Key(key) if key.kind == KeyEventKind::Press => {
                input_changed_at = Some(Instant::now());
                let commands = handle_key(&mut app, key);
                worker.deliver(&mut app, commands);
            }
            _ => {}
        }
    }
    Ok(())
}

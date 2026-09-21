use std::{
    io,
    process::{Child, ChildStdin},
    sync::mpsc::{Receiver, TryRecvError},
    time::{Duration, Instant},
};

use crossterm::{
    event::{
        self, DisableMouseCapture, EnableMouseCapture, Event, KeyCode, KeyEventKind, MouseButton,
        MouseEventKind,
    },
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::{backend::CrosstermBackend, Terminal};
use ratatui_image::picker::{Picker, ProtocolType};
use serde_json::{json, Value};

mod app;
mod commands;
mod headless;
mod i18n;
mod keys;
mod pets;
mod settings;
mod ui;
mod worker;

use app::{dispatch, esc_is_cancel, reset_after_worker_restart, App};
use headless::{apply_data_dir_argument, run_headless, strip_data_dir, HEADLESS_USAGE};
use i18n::strip_lang;
use keys::handle_key;
use settings::{apply_settings, load_settings};
use ui::{draw, Action, ButtonId};
use worker::{llm_start_payload, send, spawn_worker};

/// The worker process and its two channels; `None` once it could not be started.
type Worker = Option<(Child, ChildStdin, Receiver<Value>)>;

/// Sends the commands `dispatch` returned; a dead worker is reported, not fatal.
fn deliver(app: &mut App, worker: &mut Worker, commands: Vec<Value>) {
    for command in commands {
        let result = match worker.as_mut() {
            Some((_, stdin, _)) => send(stdin, command),
            None => Err(io::Error::other("worker is not running")),
        };
        if let Err(error) = result {
            app.worker_down = true;
            app.status = format!("Worker unavailable: {error}");
            app.log(app.status.clone());
        }
    }
}

/// Starts the worker and asks it for the LLM tool registry; a failure leaves the UI
/// usable and the next-step hint says how to repair the install.
fn start_worker(app: &mut App) -> Worker {
    match spawn_worker() {
        Ok((child, mut stdin, events)) => {
            let _ = send(
                &mut stdin,
                json!({"type": "llm_tools", "overrides": app.llm_tool_paths}),
            );
            app.worker_down = false;
            Some((child, stdin, events))
        }
        Err(error) => {
            app.worker_down = true;
            app.status = format!("Worker failed to start: {error}");
            app.log(app.status.clone());
            None
        }
    }
}

fn main() -> io::Result<()> {
    apply_data_dir_argument()?;
    let (argv, lang_override) = strip_lang(strip_data_dir(std::env::args().skip(1).collect()))
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidInput, error))?;
    match argv.first().map(String::as_str) {
        Some("transcribe" | "llm") => {
            let code = run_headless(&argv)?;
            std::process::exit(code);
        }
        Some("--help" | "-h") => {
            println!("{HEADLESS_USAGE}");
            return Ok(());
        }
        _ => {}
    }
    let mut app = App::default();
    apply_settings(&mut app, load_settings(), lang_override);
    let mut worker = start_worker(&mut app);
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen)?;
    if app.mouse_enabled {
        execute!(stdout, EnableMouseCapture)?;
    }
    let mut mouse_captured = app.mouse_enabled;
    let mut terminal = Terminal::new(CrosstermBackend::new(stdout))?;
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
    while !app.exit_requested {
        if let Some((_, _, events)) = worker.as_ref() {
            loop {
                match events.try_recv() {
                    Ok(message) => app.handle_message(message),
                    Err(TryRecvError::Empty) => break,
                    Err(TryRecvError::Disconnected) => {
                        // Nothing in flight survives the worker: clear the run flags
                        // so the idle-only keys work again, and keep the hint.
                        if !app.worker_down {
                            app.worker_down = true;
                            reset_after_worker_restart(&mut app);
                            app.status = "Worker exited".into();
                            app.log(app.status.clone());
                        }
                        break;
                    }
                }
            }
        }
        // `/llm-run` and other commands request a run through `llm_requested`; the
        // `L` key and the button go through `dispatch`, which returns the payload.
        if app.llm_requested {
            app.llm_requested = false;
            let payload = llm_start_payload(&app);
            deliver(&mut app, &mut worker, vec![payload]);
        }
        // `/mouse on|off` flips the flag; capture follows it here so that the
        // command needs no handle to the terminal.
        if app.mouse_enabled != mouse_captured {
            if app.mouse_enabled {
                execute!(terminal.backend_mut(), EnableMouseCapture)?;
            } else {
                execute!(terminal.backend_mut(), DisableMouseCapture)?;
            }
            mouse_captured = app.mouse_enabled;
        }
        if let Some((provider, path)) = app.llm_tool_check_requested.take() {
            let payload = json!({"type": "llm_tool_check", "provider": provider, "path": path});
            deliver(&mut app, &mut worker, vec![payload]);
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
            continue;
        }
        match event::read()? {
            Event::Paste(text) if !app.running => {
                app.input.push_str(text.trim());
                app.selected_command = 0;
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
                    deliver(&mut app, &mut worker, commands);
                }
            }
            Event::Key(key) if key.kind == KeyEventKind::Press => {
                // The second Esc within 700 ms of the first during a run kills the
                // worker and starts a fresh one: the only key that needs the child.
                if key.code == KeyCode::Esc && esc_is_cancel(&app) {
                    if app.last_exit_request.is_some_and(|(trigger, at)| {
                        trigger == "cancel" && at.elapsed() <= Duration::from_millis(700)
                    }) {
                        if let Some((mut child, _, _)) = worker.take() {
                            let _ = child.kill();
                        }
                        worker = start_worker(&mut app);
                        reset_after_worker_restart(&mut app);
                        app.status = if worker.is_some() {
                            "Worker restarted, run cancelled".into()
                        } else {
                            format!("Cancelled, but worker restart failed: {}", app.status)
                        };
                        app.log(app.status.clone());
                        app.last_exit_request = None;
                    } else {
                        // The first Esc is the graceful cancel the hint promises
                        // (finish the current file); the second kills the worker.
                        let commands = dispatch(&mut app, Action::Button(ButtonId::Stop));
                        deliver(&mut app, &mut worker, commands);
                        app.last_exit_request = Some(("cancel", Instant::now()));
                        app.status = if app.llm_running {
                            "Press Esc again to kill the worker".into()
                        } else {
                            "Press Esc again to cancel transcription".into()
                        };
                    }
                } else {
                    let commands = handle_key(&mut app, key);
                    deliver(&mut app, &mut worker, commands);
                }
            }
            _ => {}
        }
    }
    disable_raw_mode()?;
    if mouse_captured {
        execute!(terminal.backend_mut(), DisableMouseCapture)?;
    }
    execute!(terminal.backend_mut(), LeaveAlternateScreen)?;
    terminal.show_cursor()?;
    if let Some((mut child, _, _)) = worker {
        let _ = child.kill();
    }
    Ok(())
}

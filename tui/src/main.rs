use std::{
    io,
    time::{Duration, Instant},
};

use crossterm::{
    event::{self, Event, KeyCode, KeyEventKind, KeyModifiers},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::{backend::CrosstermBackend, Terminal};
use ratatui_image::picker::{Picker, ProtocolType};
use serde_json::json;

mod app;
mod commands;
mod headless;
mod i18n;
mod pets;
mod settings;
mod ui;
mod worker;

use app::{
    esc_should_soft_cancel, llm_can_run, llm_input_files, request_llm, reset_after_worker_restart,
    App,
};
use commands::{
    accept_command_suggestion, apply_command_menu, backend_is_supported, command_menu_options,
    command_suggestions, complete_path, is_command, open_command_menu, queue_paths,
    remove_selected_file, run_command, COMMANDS, MODEL_OPTIONS,
};
use headless::{apply_data_dir_argument, run_headless, strip_data_dir, HEADLESS_USAGE};
use i18n::{strip_lang, Lang};
use settings::{load_settings, save_app_settings};
use ui::draw;
use worker::{llm_settings_payload, send, spawn_worker, start_payload};

fn request_exit(
    app: &mut App,
    quit: &mut bool,
    last_exit_request: &mut Option<(&'static str, Instant)>,
    trigger: &'static str,
    label: &str,
) {
    if last_exit_request.is_some_and(|(last_trigger, at)| {
        last_trigger == trigger && at.elapsed() <= Duration::from_millis(700)
    }) {
        *quit = true;
    } else {
        *last_exit_request = Some((trigger, Instant::now()));
        app.status = format!("Press {label} again to exit");
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
    let (mut child, mut worker, mut events) = spawn_worker()?;
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;
    let mut app = App::default();
    let settings = load_settings();
    app.lang = lang_override
        .or_else(|| Lang::parse(&settings.language))
        .unwrap_or(Lang::Ru);
    app.pet_enabled = settings.pet_enabled;
    if backend_is_supported(&settings.backend) {
        app.backend = settings.backend;
    }
    if matches!(
        settings.onnx_provider.as_str(),
        "auto" | "cpu" | "cuda" | "tensorrt" | "coreml" | "directml"
    ) {
        app.onnx_provider = settings.onnx_provider;
    }
    if matches!(
        settings.diarization_backend.as_str(),
        "pyannote" | "onnx" | "sortformer"
    ) {
        app.diarization_backend = settings.diarization_backend;
    }
    if MODEL_OPTIONS.iter().any(|(id, _)| *id == settings.model) {
        app.model = settings.model;
    }
    if matches!(
        settings.audio_preprocessing_mode.as_str(),
        "auto" | "off" | "light" | "denoise"
    ) {
        app.audio_preprocessing_mode = settings.audio_preprocessing_mode;
    }
    app.llm_provider = settings.llm_provider;
    app.llm_api_url = settings.llm_api_url;
    app.llm_api_key = settings.llm_api_key;
    app.llm_model = settings.llm_model;
    app.llm_temperature = settings.llm_temperature;
    app.llm_internal_providers = settings.llm_internal_providers;
    app.llm_extra_args = settings.llm_extra_args;
    app.llm_tool_paths = settings.llm_tool_paths;
    app.llm_allow_tools = settings.llm_allow_tools;
    let _ = send(
        &mut worker,
        json!({"type": "llm_tools", "overrides": app.llm_tool_paths}),
    );
    app.subtitle_sentence_split = settings.subtitle_sentence_split;
    app.subtitle_max_lines = settings.subtitle_max_lines.clamp(1, 4);
    app.subtitle_max_width = settings.subtitle_max_width.clamp(20, 100);
    if !settings.formats.is_empty() {
        app.formats = settings.formats;
    }
    app.diarization = settings.diarization;
    app.num_speakers = settings.num_speakers;
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
    let mut quit = false;
    let mut last_exit_request: Option<(&'static str, Instant)> = None;
    let mut last_pet_frame = Instant::now();
    while !quit {
        while let Ok(message) = events.try_recv() {
            app.handle_message(message);
        }
        if app.llm_requested {
            app.llm_requested = false;
            let settings = llm_settings_payload(&app);
            if let Err(error) = send(
                &mut worker,
                json!({"type":"llm_start", "files":llm_input_files(&app), "modes":app.llm_modes, "prompt":app.llm_prompt, "settings":settings, "output_dir":app.output_dir}),
            ) {
                app.status = format!("Worker unavailable: {error}");
            }
        }
        if let Some((provider, path)) = app.llm_tool_check_requested.take() {
            let _ = send(
                &mut worker,
                json!({"type": "llm_tool_check", "provider": provider, "path": path}),
            );
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
        if app.exit_requested {
            quit = true;
            continue;
        }
        terminal.draw(|frame| draw(frame, &mut app))?;
        if event::poll(Duration::from_millis(80))? {
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
                Event::Key(key) => {
                    if key.kind != KeyEventKind::Press {
                        continue;
                    }
                    match key.code {
                        KeyCode::Char('c')
                            if !app.running && key.modifiers.contains(KeyModifiers::CONTROL) =>
                        {
                            request_exit(
                                &mut app,
                                &mut quit,
                                &mut last_exit_request,
                                "ctrl-c",
                                "Ctrl+C",
                            );
                        }
                        KeyCode::Char('q') if !app.running && app.input.is_empty() => quit = true,
                        KeyCode::Char('L') if !app.running && app.input.is_empty() => {
                            request_llm(&mut app)
                        }
                        KeyCode::Char('l')
                            if !app.running && app.input.is_empty() && llm_can_run(&app) =>
                        {
                            request_llm(&mut app)
                        }
                        KeyCode::Char('l') if app.input.is_empty() => {
                            app.show_logs = !app.show_logs
                        }
                        KeyCode::Char('r')
                            if !app.running
                                && app.input.is_empty()
                                && !app.llm_results.is_empty() =>
                        {
                            app.show_llm_result = !app.show_llm_result;
                        }
                        KeyCode::Char('d') if !app.running && app.input.is_empty() => {
                            app.diarization = !app.diarization;
                            app.log(format!(
                                "Diarization {}",
                                if app.diarization { "on" } else { "off" }
                            ));
                            save_app_settings(&mut app);
                        }
                        KeyCode::Char('f') if !app.running && app.input.is_empty() => {
                            app.formats = if app.formats.len() == 1 {
                                vec!["txt".into(), "srt".into()]
                            } else {
                                vec!["txt".into()]
                            };
                            app.log(format!("Formats: {}", app.formats.join(", ")));
                            save_app_settings(&mut app);
                        }
                        KeyCode::Char('s')
                            if !app.running && app.input.is_empty() && !app.files.is_empty() =>
                        {
                            if let Err(error) = send(&mut worker, start_payload(&app)) {
                                app.log(format!("Worker unavailable: {error}"));
                            }
                        }
                        KeyCode::Esc if esc_should_soft_cancel(&app) => {
                            app.llm_cancel_requested = true;
                            app.status = "Cancelling LLM… Esc again to kill the worker".into();
                            if let Err(error) = send(&mut worker, json!({"type": "llm_cancel"})) {
                                app.status = format!("Worker unavailable: {error}");
                            }
                        }
                        KeyCode::Esc if app.running => {
                            if last_exit_request.is_some_and(|(trigger, at)| {
                                trigger == "cancel" && at.elapsed() <= Duration::from_millis(700)
                            }) {
                                let _ = child.kill();
                                match spawn_worker() {
                                    Ok((new_child, new_worker, new_events)) => {
                                        child = new_child;
                                        worker = new_worker;
                                        events = new_events;
                                        let _ = send(
                                            &mut worker,
                                            json!({"type": "llm_tools", "overrides": app.llm_tool_paths}),
                                        );
                                        reset_after_worker_restart(&mut app);
                                        app.status = "Worker restarted, run cancelled".into();
                                        app.log(app.status.clone());
                                    }
                                    Err(error) => {
                                        app.status =
                                            format!("Cancelled, but worker restart failed: {error}")
                                    }
                                }
                                last_exit_request = None;
                            } else {
                                last_exit_request = Some(("cancel", Instant::now()));
                                app.status = if app.llm_running {
                                    "Press Esc again to kill the worker".into()
                                } else {
                                    "Press Esc again to cancel transcription".into()
                                };
                            }
                        }
                        KeyCode::Esc if app.command_menu.is_some() => {
                            app.command_menu = None;
                            app.input.clear();
                            app.status = "Settings menu closed".into();
                        }
                        KeyCode::Esc if app.input.is_empty() => {
                            request_exit(&mut app, &mut quit, &mut last_exit_request, "esc", "Esc");
                        }
                        KeyCode::Esc => {
                            app.input.clear();
                            app.status = "Input cleared".into();
                        }
                        KeyCode::Char(digit)
                            if !app.running
                                && app.command_menu.is_some()
                                && digit.is_ascii_digit() =>
                        {
                            let count = command_menu_options(&app).len();
                            let index = if digit == '0' {
                                count.saturating_sub(1)
                            } else {
                                digit.to_digit(10).unwrap_or_default().saturating_sub(1) as usize
                            };
                            if index < count {
                                app.command_menu_index = index;
                                apply_command_menu(&mut app);
                            }
                        }
                        KeyCode::Char(' ') if !app.running && app.command_menu.is_some() => {
                            apply_command_menu(&mut app);
                        }
                        KeyCode::Enter if !app.running && app.command_menu.is_some() => {
                            apply_command_menu(&mut app);
                        }
                        KeyCode::Enter if !app.running => {
                            let raw = app.input.trim().to_string();
                            let suggestions = command_suggestions(&raw);
                            let has_argument = raw.split_whitespace().count() > 1;
                            if !suggestions.is_empty()
                                && (!has_argument || !raw.contains(' '))
                                && !COMMANDS.iter().any(|(name, _)| *name == raw)
                            {
                                let index = app.selected_command.min(suggestions.len() - 1);
                                accept_command_suggestion(&mut app, suggestions[index].0);
                            } else if open_command_menu(&mut app, &raw) {
                            } else if is_command(&raw) {
                                run_command(&mut app);
                            } else if !raw.is_empty() {
                                queue_paths(&mut app, &raw);
                            }
                        }
                        KeyCode::Tab if !app.running => {
                            let suggestions = command_suggestions(&app.input);
                            if !suggestions.is_empty() {
                                let index = app.selected_command.min(suggestions.len() - 1);
                                app.input = format!("{} ", suggestions[index].0);
                                app.selected_command = 0;
                            } else if let Some(path) = complete_path(&app.input) {
                                app.input = path;
                            }
                        }
                        KeyCode::Up if !app.running && app.command_menu.is_some() => {
                            let count = command_menu_options(&app).len();
                            app.command_menu_index = (app.command_menu_index + count - 1) % count;
                        }
                        KeyCode::Down if !app.running && app.command_menu.is_some() => {
                            let count = command_menu_options(&app).len();
                            app.command_menu_index = (app.command_menu_index + 1) % count;
                        }
                        KeyCode::Up
                            if !app.running && !command_suggestions(&app.input).is_empty() =>
                        {
                            let count = command_suggestions(&app.input).len();
                            app.selected_command = (app.selected_command + count - 1) % count;
                        }
                        KeyCode::Down
                            if !app.running && !command_suggestions(&app.input).is_empty() =>
                        {
                            let count = command_suggestions(&app.input).len();
                            app.selected_command = (app.selected_command + 1) % count;
                        }
                        KeyCode::Up if !app.running && app.input.is_empty() => {
                            if key.modifiers.contains(KeyModifiers::CONTROL) {
                                if let Some(index) = app.selected_file.filter(|index| *index > 0) {
                                    app.files.swap(index, index - 1);
                                    app.selected_file = Some(index - 1);
                                }
                            } else if !app.files.is_empty() {
                                app.selected_file =
                                    Some(app.selected_file.unwrap_or(0).saturating_sub(1));
                            }
                        }
                        KeyCode::Down if !app.running && app.input.is_empty() => {
                            if key.modifiers.contains(KeyModifiers::CONTROL) {
                                if let Some(index) = app
                                    .selected_file
                                    .filter(|index| *index + 1 < app.files.len())
                                {
                                    app.files.swap(index, index + 1);
                                    app.selected_file = Some(index + 1);
                                }
                            } else if !app.files.is_empty() {
                                app.selected_file = Some(
                                    (app.selected_file.unwrap_or(0) + 1).min(app.files.len() - 1),
                                );
                            }
                        }
                        KeyCode::Delete if !app.running && app.input.is_empty() => {
                            remove_selected_file(&mut app)
                        }
                        KeyCode::Backspace if !app.running && app.input.is_empty() => {
                            remove_selected_file(&mut app)
                        }
                        KeyCode::Backspace if !app.running => {
                            app.input.pop();
                        }
                        KeyCode::Char(c) if !app.running => {
                            app.command_menu = None;
                            app.input.push(c);
                            app.selected_command = 0;
                        }
                        _ => {}
                    }
                }
                _ => {}
            }
        }
    }
    disable_raw_mode()?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen)?;
    terminal.show_cursor()?;
    let _ = child.kill();
    Ok(())
}

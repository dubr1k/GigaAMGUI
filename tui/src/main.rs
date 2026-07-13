use std::{
    fs,
    io::{self, BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStdin, Command, Stdio},
    sync::mpsc::{self, Receiver},
    time::{Duration, Instant},
};

use crossterm::{
    event::{self, Event, KeyCode, KeyEventKind, KeyModifiers},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::{
    backend::CrosstermBackend,
    layout::{Constraint, Direction, Layout, Margin},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Gauge, Paragraph, Wrap},
    Terminal,
};
use serde_json::{json, Value};

struct App {
    input: String,
    files: Vec<String>,
    logs: Vec<String>,
    status: String,
    current_file: Option<String>,
    file_index: usize,
    total_files: usize,
    stage: String,
    progress: f64,
    processed_seconds: Option<f64>,
    total_seconds: Option<f64>,
    running: bool,
    cancelled: bool,
    diarization: bool,
    num_speakers: Option<u32>,
    formats: Vec<String>,
    output_dir: Option<String>,
    backend: String,
    show_logs: bool,
    result_files: Vec<String>,
    selected_file: Option<usize>,
    selected_command: usize,
    command_menu: Option<String>,
    command_menu_index: usize,
    exit_requested: bool,
}

impl Default for App {
    fn default() -> Self {
        Self {
            input: String::new(),
            files: Vec::new(),
            logs: vec!["Ready. Paste a media path and press Enter.".into()],
            status: "Ready".into(),
            current_file: None,
            file_index: 0,
            total_files: 0,
            stage: "preparing".into(),
            progress: 0.0,
            processed_seconds: None,
            total_seconds: None,
            running: false,
            cancelled: false,
            diarization: false,
            num_speakers: None,
            formats: vec!["txt".into()],
            output_dir: None,
            backend: "auto".into(),
            show_logs: true,
            result_files: Vec::new(),
            selected_file: None,
            selected_command: 0,
            command_menu: None,
            command_menu_index: 0,
            exit_requested: false,
        }
    }
}

impl App {
    fn log(&mut self, line: impl Into<String>) {
        self.logs.push(line.into());
        if self.logs.len() > 200 {
            self.logs.remove(0);
        }
    }

    fn handle_message(&mut self, value: Value) {
        let kind = value["type"].as_str().unwrap_or("error");
        match kind {
            "started" => {
                self.running = true;
                self.cancelled = false;
                self.total_files = value["total_files"].as_u64().unwrap_or(0) as usize;
                self.status = format!(
                    "Recognition running · {}",
                    value["backend"].as_str().unwrap_or("auto")
                );
            }
            "log" => self.log(value["message"].as_str().unwrap_or("").to_string()),
            "file_started" => {
                self.current_file = value["file"].as_str().map(str::to_owned);
                self.file_index = value["file_index"].as_u64().unwrap_or(0) as usize;
                self.progress = 0.0;
                self.status = "Recognition running…".into();
            }
            "progress" => {
                self.stage = value["stage"].as_str().unwrap_or("preparing").to_string();
                self.progress = value["file_progress"]
                    .as_f64()
                    .unwrap_or(0.0)
                    .clamp(0.0, 1.0);
                self.processed_seconds = value["processed_seconds"].as_f64();
                self.total_seconds = value["total_seconds"].as_f64();
                if let Some(message) = value["message"].as_str() {
                    self.status = message.to_string();
                }
            }
            "file_completed" => {
                if value["result"]["success"].as_bool().unwrap_or(false) {
                    self.log(format!(
                        "✓ {}",
                        short_name(value["file"].as_str().unwrap_or(""))
                    ));
                    if let Some(saved) = value["result"]["saved_files"].as_array() {
                        self.result_files
                            .extend(saved.iter().filter_map(|p| p.as_str().map(str::to_owned)));
                    }
                } else {
                    self.log(format!(
                        "× {}",
                        short_name(value["file"].as_str().unwrap_or(""))
                    ));
                }
            }
            "cancelling" => {
                self.cancelled = true;
                self.status = value["message"].as_str().unwrap_or("Cancelling…").into();
            }
            "completed" => {
                self.running = false;
                self.cancelled = value["cancelled"].as_bool().unwrap_or(false);
                self.status = if self.cancelled {
                    "Cancelled".into()
                } else if value["success"].as_bool().unwrap_or(false) {
                    "Completed".into()
                } else {
                    "Completed with errors".into()
                };
                self.log(self.status.clone());
            }
            "error" => {
                self.status = value["message"].as_str().unwrap_or("Worker error").into();
                self.log(format!("Error: {}", self.status));
            }
            _ => {}
        }
    }
}

fn short_name(path: &str) -> String {
    path.rsplit(['/', '\\']).next().unwrap_or(path).to_string()
}
fn normalize_path(raw: &str) -> Result<String, String> {
    let mut text = raw.trim().trim_matches(['\'', '"']).trim().to_string();
    if let Some(path) = text.strip_prefix("file://") {
        text = path.replace("%20", " ");
    }
    if let Some(path) = text.strip_prefix("~/") {
        let home = std::env::var("HOME").map_err(|_| "HOME is not set".to_string())?;
        text = format!("{home}/{path}");
    }
    let path = fs::canonicalize(&text).map_err(|_| format!("File does not exist: {text}"))?;
    if !path.is_file() {
        return Err(format!("Not a file: {}", path.display()));
    }
    Ok(path.to_string_lossy().into_owned())
}

fn complete_path(raw: &str) -> Option<String> {
    let text = raw.trim().trim_matches(['\'', '"']);
    if text.is_empty() || text.starts_with('/') && is_command(text) {
        return None;
    }
    let expanded = if let Some(path) = text.strip_prefix("~/") {
        format!("{}/{}", std::env::var("HOME").ok()?, path)
    } else {
        text.to_string()
    };
    let path = Path::new(&expanded);
    let (parent, prefix) = if expanded.ends_with('/') {
        (PathBuf::from(&expanded), "")
    } else {
        let parent = path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
            .unwrap_or_else(|| Path::new("."));
        (parent.to_path_buf(), path.file_name()?.to_str()?)
    };
    let mut matches: Vec<PathBuf> = fs::read_dir(parent)
        .ok()?
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| {
            path.file_name()
                .and_then(|name| name.to_str())
                .is_some_and(|name| name.starts_with(prefix))
        })
        .collect();
    matches.sort();
    let candidate = matches.first()?;
    let mut completed = candidate.to_string_lossy().into_owned();
    if candidate.is_dir() {
        completed.push('/');
    }
    Some(completed)
}

const COMMANDS: [(&str, &str); 9] = [
    ("/output", "set the results directory"),
    ("/backend", "auto, pytorch, or mlx"),
    ("/formats", "output formats, e.g. txt,srt"),
    ("/diarize", "turn speaker diarization on or off"),
    ("/speakers", "auto or a fixed speaker count"),
    ("/remove", "remove a file from the queue by number"),
    ("/clear", "clear the queue and result list"),
    ("/settings", "show current processing settings"),
    ("/exit", "exit the terminal UI"),
];

fn command_suggestions(input: &str) -> Vec<(&'static str, &'static str)> {
    let command = input
        .trim_start()
        .split_whitespace()
        .next()
        .unwrap_or_default();
    if !command.starts_with('/') {
        return Vec::new();
    }
    COMMANDS
        .into_iter()
        .filter(|(name, _)| name.starts_with(command))
        .collect()
}

fn is_command(input: &str) -> bool {
    !command_suggestions(input).is_empty()
}

fn command_menu_options(app: &App) -> Vec<String> {
    match app.command_menu.as_deref() {
        Some("/backend") => vec!["auto", "pytorch", "mlx"]
            .into_iter()
            .map(str::to_owned)
            .collect(),
        Some("/diarize") => vec!["on", "off"].into_iter().map(str::to_owned).collect(),
        Some("/speakers") => vec!["auto", "1", "2", "3", "4", "5", "6", "7", "8"]
            .into_iter()
            .map(str::to_owned)
            .collect(),
        Some("/formats") => [
            "txt",
            "txt_timecodes",
            "txt_diarize",
            "txt_diarize_timecodes",
            "md",
            "srt",
            "vtt",
        ]
        .into_iter()
        .map(|format| {
            format!(
                "[{}] {format}",
                if app.formats.iter().any(|selected| selected == format) {
                    "x"
                } else {
                    " "
                }
            )
        })
        .collect(),
        _ => Vec::new(),
    }
}

fn open_command_menu(app: &mut App, command: &str) -> bool {
    if matches!(command, "/backend" | "/diarize" | "/formats" | "/speakers") {
        app.command_menu = Some(command.to_owned());
        app.command_menu_index = 0;
        app.status = "Choose an option with arrows and Enter".into();
        true
    } else {
        false
    }
}

fn apply_command_menu(app: &mut App) {
    let options = command_menu_options(app);
    let Some(option) = options.get(app.command_menu_index.min(options.len().saturating_sub(1)))
    else {
        return;
    };
    let command = app.command_menu.clone().unwrap_or_default();
    match command.as_str() {
        "/backend" => {
            app.backend = option.clone();
            app.status = format!("Backend: {}", app.backend);
            app.command_menu = None;
            app.input.clear();
        }
        "/diarize" => {
            app.diarization = option == "on";
            app.status = format!("Diarization {option}");
            app.command_menu = None;
            app.input.clear();
        }
        "/speakers" => {
            app.num_speakers = option.parse().ok();
            app.status = format!("Speaker count: {option}");
            app.command_menu = None;
            app.input.clear();
        }
        "/formats" => {
            let format = option.trim_start_matches("[x] ").trim_start_matches("[ ] ");
            if let Some(index) = app.formats.iter().position(|selected| selected == format) {
                app.formats.remove(index);
            } else {
                app.formats.push(format.to_owned());
            }
            if app.formats.is_empty() {
                app.formats.push("txt".into());
            }
            app.status = format!("Formats: {}", app.formats.join(", "));
        }
        _ => {}
    }
    app.log(app.status.clone());
}

fn run_command(app: &mut App) {
    let command = app.input.trim().to_owned();
    let mut parts = command.splitn(2, char::is_whitespace);
    let name = parts.next().unwrap_or_default();
    let argument = parts.next().unwrap_or_default().trim();
    match name {
        "/exit" => {
            app.exit_requested = true;
            app.status = "Exiting…".into();
        }
        "/settings" => {
            app.status = format!(
                "backend={} · output={} · formats={} · diarization={}",
                app.backend,
                app.output_dir.as_deref().unwrap_or("next to source"),
                app.formats.join(","),
                if app.diarization { "on" } else { "off" }
            );
        }
        "/output" => {
            if argument.is_empty() {
                app.status = "Usage: /output <directory>".into();
            } else if let Err(error) = fs::create_dir_all(argument) {
                app.status = format!("Cannot create output directory: {error}");
            } else if let Ok(path) = fs::canonicalize(argument) {
                app.output_dir = Some(path.to_string_lossy().into_owned());
                app.status = "Output directory updated".into();
            }
        }
        "/backend" if matches!(argument, "auto" | "pytorch" | "mlx") => {
            app.backend = argument.into();
            app.status = format!("Backend: {}", app.backend);
        }
        "/backend" => app.status = "Usage: /backend auto|pytorch|mlx".into(),
        "/formats" => {
            let formats: Vec<String> = argument
                .split(',')
                .map(str::trim)
                .filter(|format| {
                    matches!(
                        *format,
                        "txt"
                            | "txt_timecodes"
                            | "txt_diarize"
                            | "txt_diarize_timecodes"
                            | "md"
                            | "srt"
                            | "vtt"
                    )
                })
                .map(str::to_owned)
                .collect();
            if formats.is_empty() {
                app.status = "Usage: /formats txt,srt,md,vtt".into();
            } else {
                app.formats = formats;
                app.status = format!("Formats: {}", app.formats.join(", "));
            }
        }
        "/diarize" if matches!(argument, "on" | "off") => {
            app.diarization = argument == "on";
            app.status = format!("Diarization {}", argument);
        }
        "/diarize" => app.status = "Usage: /diarize on|off".into(),
        "/speakers" if argument == "auto" => {
            app.num_speakers = None;
            app.status = "Speaker count: auto".into();
        }
        "/speakers" => match argument.parse::<u32>() {
            Ok(value) if value > 0 => {
                app.num_speakers = Some(value);
                app.status = format!("Speaker count: {value}");
            }
            _ => app.status = "Usage: /speakers auto|<positive number>".into(),
        },
        "/clear" => {
            app.files.clear();
            app.selected_file = None;
            app.result_files.clear();
            app.status = "Queue cleared".into();
        }
        "/remove" => match argument.parse::<usize>() {
            Ok(index) if index > 0 && index <= app.files.len() => {
                let file = app.files.remove(index - 1);
                app.selected_file = app
                    .files
                    .get(index - 1)
                    .map(|_| index - 1)
                    .or_else(|| index.checked_sub(2));
                app.status = format!("Removed {}", short_name(&file));
            }
            _ => app.status = "Usage: /remove <queue number>".into(),
        },
        _ => app.status = format!("Unknown command: {name}"),
    }
    app.log(app.status.clone());
    app.input.clear();
}

fn remove_selected_file(app: &mut App) {
    let Some(index) = app.selected_file.filter(|index| *index < app.files.len()) else {
        app.status = "No queued file selected".into();
        return;
    };
    let file = app.files.remove(index);
    app.selected_file = app
        .files
        .get(index)
        .map(|_| index)
        .or_else(|| index.checked_sub(1));
    app.status = format!("Removed {}", short_name(&file));
    app.log(app.status.clone());
}

fn timecode(seconds: f64) -> String {
    format!(
        "{:02}:{:02}:{:02}",
        (seconds / 3600.0) as u64,
        ((seconds / 60.0) as u64) % 60,
        seconds as u64 % 60
    )
}

fn python_in(directory: &Path) -> Option<PathBuf> {
    #[cfg(target_os = "windows")]
    let executable = directory.join("Scripts").join("python.exe");
    #[cfg(not(target_os = "windows"))]
    let executable = directory.join("bin").join("python");
    executable.is_file().then_some(executable)
}

fn find_python(project_root: &Path) -> String {
    if let Ok(python) = std::env::var("GIGAAM_PYTHON") {
        return python;
    }
    if let Ok(virtual_env) = std::env::var("VIRTUAL_ENV") {
        if let Some(python) = python_in(Path::new(&virtual_env)) {
            return python.to_string_lossy().into_owned();
        }
    }
    for name in [".venv", "venv"] {
        if let Some(python) = python_in(&project_root.join(name)) {
            return python.to_string_lossy().into_owned();
        }
    }
    "python".into()
}

fn bundled_worker() -> Option<PathBuf> {
    if let Ok(worker) = std::env::var("GIGAAM_TUI_WORKER_EXE") {
        let path = PathBuf::from(worker);
        if path.is_file() {
            return Some(path);
        }
    }
    let executable = if cfg!(target_os = "windows") {
        "GigaAMTuiWorker.exe"
    } else {
        "gigaam-tui-worker"
    };
    let directory = std::env::current_exe().ok()?.parent()?.to_path_buf();
    let worker = directory.join(executable);
    worker.is_file().then_some(worker)
}

fn spawn_worker() -> io::Result<(Child, ChildStdin, Receiver<Value>)> {
    let module = std::env::var("GIGAAM_TUI_WORKER").unwrap_or_else(|_| "src.tui_worker".into());
    let project_root = std::env::var("GIGAAM_PROJECT_ROOT")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|_| {
            std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .expect("tui has a project root")
                .to_path_buf()
        });
    let mut command = if let Some(worker) = bundled_worker() {
        Command::new(worker)
    } else {
        let python = find_python(&project_root);
        let mut command = Command::new(python);
        command.args(["-m", &module]);
        command
    };
    let mut child = command
        .current_dir(project_root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()?;
    let stdin = child.stdin.take().expect("worker stdin");
    let stdout = child.stdout.take().expect("worker stdout");
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if let Ok(value) = serde_json::from_str::<Value>(&line) {
                let _ = tx.send(value);
            }
        }
    });
    Ok((child, stdin, rx))
}

fn send(stdin: &mut ChildStdin, message: Value) -> io::Result<()> {
    writeln!(
        stdin,
        "{}",
        serde_json::to_string(&message).expect("JSON command")
    )?;
    stdin.flush()
}

fn draw(frame: &mut ratatui::Frame, app: &App) {
    let area = frame.area();
    let menu_options = if app.running {
        Vec::new()
    } else {
        command_menu_options(app)
    };
    let suggestions = if app.running || !menu_options.is_empty() {
        Vec::new()
    } else {
        command_suggestions(&app.input)
    };
    let prompt_height = 3 + menu_options.len().max(suggestions.len()).min(8) as u16;
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(2),
            Constraint::Min(10),
            Constraint::Length(prompt_height),
            Constraint::Length(1),
        ])
        .split(area);
    let accent = Color::Rgb(92, 155, 255);
    let header = Line::from(vec![
        Span::styled(
            " GigaAM",
            Style::default()
                .fg(Color::White)
                .add_modifier(Modifier::BOLD),
        ),
        Span::styled(
            "  terminal transcriber",
            Style::default().fg(Color::DarkGray),
        ),
        Span::raw(" "),
        Span::styled(
            if app.running {
                "● running"
            } else {
                "● idle"
            },
            Style::default().fg(if app.running {
                Color::Green
            } else {
                Color::DarkGray
            }),
        ),
        Span::styled(
            format!("   {} · {}", app.backend, app.formats.join(",")),
            Style::default().fg(Color::DarkGray),
        ),
    ]);
    frame.render_widget(Paragraph::new(header), chunks[0]);

    let mut body = Vec::<Line>::new();
    if app.files.is_empty() {
        body.push(Line::styled(
            "  Drop files here or type a path",
            Style::default().fg(Color::DarkGray),
        ));
    } else {
        body.push(Line::styled(
            format!(
                "  {} file{} queued",
                app.files.len(),
                if app.files.len() == 1 { "" } else { "s" }
            ),
            Style::default().fg(Color::Gray),
        ));
        for (index, file) in app.files.iter().enumerate() {
            let current = app.running && app.file_index == index;
            let selected = !app.running && app.selected_file == Some(index);
            let symbol = if current {
                "●"
            } else if selected {
                "›"
            } else if app.running && index < app.file_index {
                "✓"
            } else {
                "○"
            };
            body.push(Line::from(vec![
                Span::styled(
                    format!("  {symbol} "),
                    Style::default().fg(if current || selected {
                        accent
                    } else {
                        Color::DarkGray
                    }),
                ),
                Span::styled(
                    short_name(file),
                    Style::default()
                        .fg(if selected { accent } else { Color::White })
                        .add_modifier(if current || selected {
                            Modifier::BOLD
                        } else {
                            Modifier::empty()
                        }),
                ),
            ]));
            if current {
                let detail = match (app.processed_seconds, app.total_seconds) {
                    (Some(done), Some(total)) => {
                        format!("{} / {}", timecode(done), timecode(total))
                    }
                    _ => format!("{:>3}%", (app.progress * 100.0) as u16),
                };
                body.push(Line::styled(
                    format!(
                        "    {:<16} {:>3}%   {}",
                        app.stage,
                        (app.progress * 100.0) as u16,
                        detail
                    ),
                    Style::default().fg(Color::Gray),
                ));
            }
        }
    }
    body.push(Line::raw(""));
    body.push(Line::styled(
        format!("  {}", app.status),
        Style::default().fg(if app.running { accent } else { Color::Gray }),
    ));
    if !app.result_files.is_empty() {
        body.push(Line::raw(""));
        body.push(Line::styled("  Saved", Style::default().fg(Color::Green)));
        for file in app.result_files.iter().take(4) {
            body.push(Line::styled(
                format!("  {}", file),
                Style::default().fg(Color::Gray),
            ));
        }
    }
    if app.show_logs {
        body.push(Line::raw(""));
        body.push(Line::styled(
            "  ── activity ─────────────────────────",
            Style::default().fg(Color::DarkGray),
        ));
        for line in app.logs.iter().rev().take(5).rev() {
            body.push(Line::styled(
                format!("  {}", line),
                Style::default().fg(Color::DarkGray),
            ));
        }
    }
    frame.render_widget(
        Paragraph::new(body).wrap(Wrap { trim: true }),
        chunks[1].inner(Margin {
            horizontal: 1,
            vertical: 0,
        }),
    );
    if app.running {
        frame.render_widget(
            Gauge::default()
                .gauge_style(Style::default().fg(accent))
                .ratio(app.progress)
                .label(format!(" {}", app.stage)),
            chunks[2].inner(Margin {
                horizontal: 2,
                vertical: 1,
            }),
        );
    } else {
        let mut prompt_lines = if !menu_options.is_empty() {
            menu_options
                .iter()
                .enumerate()
                .map(|(index, option)| {
                    let selected = index
                        == app
                            .command_menu_index
                            .min(menu_options.len().saturating_sub(1));
                    Line::from(vec![
                        Span::styled(
                            if selected { "  › " } else { "    " },
                            Style::default().fg(accent),
                        ),
                        Span::styled(
                            option,
                            Style::default()
                                .fg(if selected { Color::White } else { Color::Gray })
                                .add_modifier(if selected {
                                    Modifier::BOLD
                                } else {
                                    Modifier::empty()
                                }),
                        ),
                    ])
                })
                .collect::<Vec<_>>()
        } else {
            suggestions
                .iter()
                .enumerate()
                .map(|(index, (command, description))| {
                    let selected = index
                        == app
                            .selected_command
                            .min(suggestions.len().saturating_sub(1));
                    Line::from(vec![
                        Span::styled(
                            format!("  {command:<12}"),
                            Style::default().fg(accent).add_modifier(if selected {
                                Modifier::BOLD
                            } else {
                                Modifier::empty()
                            }),
                        ),
                        Span::styled(
                            *description,
                            Style::default().fg(if selected { Color::White } else { Color::Gray }),
                        ),
                    ])
                })
                .collect::<Vec<_>>()
        };
        prompt_lines.push(Line::from(vec![
            Span::styled(
                "› ",
                Style::default().fg(accent).add_modifier(Modifier::BOLD),
            ),
            Span::raw(&app.input),
        ]));
        frame.render_widget(
            Paragraph::new(prompt_lines).block(
                Block::default()
                    .borders(Borders::TOP)
                    .border_style(Style::default().fg(Color::DarkGray)),
            ),
            chunks[2],
        );
    }
    let footer =
        "Enter add path · Tab complete · type / for commands · s start · esc cancel · l logs";
    frame.render_widget(
        Paragraph::new(footer).style(Style::default().fg(Color::DarkGray)),
        chunks[3],
    );
}

fn main() -> io::Result<()> {
    let (mut child, mut worker, events) = spawn_worker()?;
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;
    let mut app = App::default();
    let mut quit = false;
    let mut last_escape: Option<Instant> = None;
    while !quit {
        while let Ok(message) = events.try_recv() {
            app.handle_message(message);
        }
        if app.exit_requested {
            quit = true;
            continue;
        }
        terminal.draw(|frame| draw(frame, &app))?;
        if event::poll(Duration::from_millis(80))? {
            match event::read()? {
                Event::Paste(text) if !app.running => {
                    app.input.push_str(text.trim());
                    app.selected_command = 0;
                }
                Event::Key(key) => {
                    if key.kind != KeyEventKind::Press {
                        continue;
                    }
                    match key.code {
                        KeyCode::Char('q') if !app.running && app.input.is_empty() => quit = true,
                        KeyCode::Char('l') if app.input.is_empty() => {
                            app.show_logs = !app.show_logs
                        }
                        KeyCode::Char('d') if !app.running && app.input.is_empty() => {
                            app.diarization = !app.diarization;
                            app.log(format!(
                                "Diarization {}",
                                if app.diarization { "on" } else { "off" }
                            ));
                        }
                        KeyCode::Char('f') if !app.running && app.input.is_empty() => {
                            app.formats = if app.formats.len() == 1 {
                                vec!["txt".into(), "srt".into()]
                            } else {
                                vec!["txt".into()]
                            };
                            app.log(format!("Formats: {}", app.formats.join(", ")));
                        }
                        KeyCode::Char('s')
                            if !app.running && app.input.is_empty() && !app.files.is_empty() =>
                        {
                            app.result_files.clear();
                            if let Err(error) = send(
                                &mut worker,
                                json!({"type":"start", "files":app.files, "output_dir":app.output_dir, "formats":app.formats, "diarization":app.diarization, "num_speakers":app.num_speakers, "backend":app.backend}),
                            ) {
                                app.log(format!("Worker unavailable: {error}"));
                            }
                        }
                        KeyCode::Esc if app.running => {
                            let _ = send(&mut worker, json!({"type":"cancel"}));
                        }
                        KeyCode::Esc if app.command_menu.is_some() => {
                            app.command_menu = None;
                            app.input.clear();
                            app.status = "Settings menu closed".into();
                        }
                        KeyCode::Esc if app.input.is_empty() => {
                            if last_escape
                                .is_some_and(|at| at.elapsed() <= Duration::from_millis(700))
                            {
                                quit = true;
                            } else {
                                last_escape = Some(Instant::now());
                                app.status = "Press Esc again to exit".into();
                            }
                        }
                        KeyCode::Esc => {
                            app.input.clear();
                            app.status = "Input cleared".into();
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
                                app.input = format!("{} ", suggestions[index].0);
                                app.selected_command = 0;
                            } else if open_command_menu(&mut app, &raw) {
                            } else if is_command(&raw) {
                                run_command(&mut app);
                            } else if !raw.is_empty() {
                                match normalize_path(&raw) {
                                    Ok(path) => {
                                        app.files.push(path.clone());
                                        app.selected_file = app.files.len().checked_sub(1);
                                        app.status = format!("Queued {}", short_name(&path));
                                        app.log(app.status.clone());
                                        app.input.clear();
                                    }
                                    Err(error) => app.status = error,
                                }
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

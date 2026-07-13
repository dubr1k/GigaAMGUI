use std::{
    fs,
    io::{self, BufRead, BufReader, Cursor, Write},
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
use image::ImageReader;
use ratatui::{
    backend::CrosstermBackend,
    layout::{Constraint, Direction, Layout, Margin, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Gauge, Paragraph, Wrap},
    Terminal,
};
use ratatui_image::{
    picker::{Picker, ProtocolType},
    protocol::StatefulProtocol,
    StatefulImage,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

#[derive(Deserialize, Serialize)]
#[serde(default)]
struct TuiSettings {
    pet_enabled: bool,
    backend: String,
}

impl Default for TuiSettings {
    fn default() -> Self {
        Self {
            pet_enabled: false,
            backend: "auto".into(),
        }
    }
}

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
    pet_enabled: bool,
    pet_frame: usize,
    pet_running: bool,
    pet_picker: Option<Picker>,
    pet_image: Option<StatefulProtocol>,
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
            pet_enabled: false,
            pet_frame: 0,
            pet_running: false,
            pet_picker: None,
            pet_image: None,
        }
    }
}

impl App {
    fn refresh_pet_image(&mut self) -> Result<(), String> {
        let Some(picker) = self.pet_picker.as_ref() else {
            return Err("Pets require Kitty, iTerm2, or Sixel image support.".into());
        };
        let frame = if self.running {
            PET_RUN_FRAMES[self.pet_frame % PET_RUN_FRAMES.len()]
        } else {
            PET_IDLE_FRAMES[self.pet_frame % PET_IDLE_FRAMES.len()]
        };
        let image = ImageReader::new(Cursor::new(frame))
            .with_guessed_format()
            .map_err(|error| format!("Cannot read pet image: {error}"))?
            .decode()
            .map_err(|error| format!("Cannot decode pet image: {error}"))?;
        self.pet_image = Some(picker.new_resize_protocol(image));
        Ok(())
    }

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

fn settings_path() -> Option<PathBuf> {
    if let Some(directory) = std::env::var_os("GIGAAM_CONFIG_DIR") {
        return Some(PathBuf::from(directory).join("tui_settings.json"));
    }
    #[cfg(target_os = "macos")]
    {
        std::env::var_os("HOME").map(|home| {
            PathBuf::from(home)
                .join("Library")
                .join("Application Support")
                .join("GigaAMTranscriber")
                .join("tui_settings.json")
        })
    }
    #[cfg(target_os = "windows")]
    {
        std::env::var_os("APPDATA")
            .or_else(|| std::env::var_os("USERPROFILE"))
            .map(|directory| {
                PathBuf::from(directory)
                    .join("GigaAMTranscriber")
                    .join("tui_settings.json")
            })
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        std::env::var_os("XDG_CONFIG_HOME")
            .map(PathBuf::from)
            .or_else(|| std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".config")))
            .map(|directory| directory.join("GigaAMTranscriber").join("tui_settings.json"))
    }
}

fn load_settings() -> TuiSettings {
    settings_path()
        .and_then(|path| fs::read_to_string(path).ok())
        .and_then(|contents| serde_json::from_str(&contents).ok())
        .unwrap_or_default()
}

fn save_settings(settings: &TuiSettings) -> Result<(), String> {
    let path = settings_path().ok_or("Cannot determine the settings directory")?;
    let parent = path.parent().ok_or("Cannot determine the settings directory")?;
    fs::create_dir_all(parent).map_err(|error| format!("Cannot create settings directory: {error}"))?;
    let contents = serde_json::to_string_pretty(settings)
        .map_err(|error| format!("Cannot encode settings: {error}"))?;
    fs::write(path, contents).map_err(|error| format!("Cannot save settings: {error}"))
}

fn save_app_settings(app: &mut App) {
    if let Err(error) = save_settings(&TuiSettings {
        pet_enabled: app.pet_enabled,
        backend: app.backend.clone(),
    }) {
        app.status = error;
        app.log(app.status.clone());
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

fn split_shell_paths(raw: &str) -> Vec<String> {
    let mut paths = Vec::new();
    let mut current = String::new();
    let mut quote = None;
    let mut escaped = false;
    for character in raw.chars() {
        if escaped {
            current.push(character);
            escaped = false;
        } else if character == '\\' {
            escaped = true;
        } else if matches!(character, '\'' | '"') {
            if quote == Some(character) {
                quote = None;
            } else if quote.is_none() {
                quote = Some(character);
            } else {
                current.push(character);
            }
        } else if character.is_whitespace() && quote.is_none() {
            if !current.is_empty() {
                paths.push(std::mem::take(&mut current));
            }
        } else {
            current.push(character);
        }
    }
    if escaped {
        current.push('\\');
    }
    if !current.is_empty() {
        paths.push(current);
    }
    paths
}

fn queue_paths(app: &mut App, raw: &str) {
    let lines: Vec<String> = raw
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .map(str::to_owned)
        .collect();
    let candidates = if lines.len() > 1 {
        lines
    } else {
        let value = lines.first().map(String::as_str).unwrap_or(raw).trim();
        match normalize_path(value) {
            Ok(path) => vec![path],
            Err(error) => {
                let split = split_shell_paths(value);
                if split.len() == 1 && split.first().is_some_and(|path| path != value) {
                    split
                } else if split.len() > 1 {
                    split
                } else {
                    app.status = error;
                    return;
                }
            }
        }
    };

    let mut queued = 0;
    let mut errors = Vec::new();
    for candidate in candidates {
        match normalize_path(&candidate) {
            Ok(path) => {
                app.files.push(path);
                queued += 1;
            }
            Err(error) => errors.push(error),
        }
    }
    if queued == 0 {
        app.status = errors
            .into_iter()
            .next()
            .unwrap_or_else(|| "No input files supplied".into());
        return;
    }
    app.selected_file = app.files.len().checked_sub(1);
    app.input.clear();
    app.status = if errors.is_empty() {
        format!("Queued {queued} file{}", if queued == 1 { "" } else { "s" })
    } else {
        format!(
            "Queued {queued} file{} · {} skipped (see log)",
            if queued == 1 { "" } else { "s" },
            errors.len()
        )
    };
    app.log(app.status.clone());
    for error in errors {
        app.log(error);
    }
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

const PET_IDLE_FRAMES: [&[u8]; 2] = [
    include_bytes!("../../assets/pets/unicorn-idle-01.png"),
    include_bytes!("../../assets/pets/unicorn-idle-02.png"),
];
const PET_RUN_FRAMES: [&[u8]; 3] = [
    include_bytes!("../../assets/pets/unicorn-run-01.png"),
    include_bytes!("../../assets/pets/unicorn-run-02.png"),
    include_bytes!("../../assets/pets/unicorn-run-03.png"),
];

const BACK_MENU_OPTION: &str = "← Back";

const COMMANDS: [(&str, &str); 10] = [
    ("/output", "set the results directory"),
    ("/backend", "select the ASR runtime"),
    ("/formats", "output formats, e.g. txt,srt"),
    ("/diarize", "turn speaker diarization on or off"),
    ("/speakers", "auto or a fixed speaker count"),
    ("/remove", "remove a file from the queue by number"),
    ("/clear", "clear the queue and result list"),
    ("/settings", "show current processing settings"),
    ("/pets", "toggle the animated unicorn companion"),
    ("/exit", "exit the terminal UI"),
];

fn selectable_backends() -> &'static [&'static str] {
    #[cfg(target_os = "macos")]
    {
        &["pytorch", "mlx"]
    }
    #[cfg(not(target_os = "macos"))]
    {
        &["auto", "pytorch"]
    }
}

fn backend_is_supported(backend: &str) -> bool {
    #[cfg(target_os = "macos")]
    {
        matches!(backend, "auto" | "pytorch" | "mlx")
    }
    #[cfg(not(target_os = "macos"))]
    {
        matches!(backend, "auto" | "pytorch")
    }
}

fn backend_usage() -> &'static str {
    #[cfg(target_os = "macos")]
    {
        "Usage: /backend pytorch|mlx (or auto)"
    }
    #[cfg(not(target_os = "macos"))]
    {
        "Usage: /backend auto|pytorch"
    }
}

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

fn accept_command_suggestion(app: &mut App, command: &str) {
    if open_command_menu(app, command) {
        app.selected_command = 0;
        return;
    }
    app.input = command.into();
    if command == "/clear" {
        run_command(app);
    } else {
        app.input.push(' ');
    }
    app.selected_command = 0;
}

fn command_menu_options(app: &App) -> Vec<String> {
    match app.command_menu.as_deref() {
        Some("/backend") => selectable_backends()
            .iter()
            .map(|backend| (*backend).to_owned())
            .chain(std::iter::once(BACK_MENU_OPTION.to_owned()))
            .collect(),
        Some("/diarize") => ["on", "off", BACK_MENU_OPTION]
            .into_iter()
            .map(str::to_owned)
            .collect(),
        Some("/speakers") => [
            "auto",
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            BACK_MENU_OPTION,
        ]
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
        .chain(std::iter::once(BACK_MENU_OPTION.to_owned()))
        .collect(),
        _ => Vec::new(),
    }
}

fn open_command_menu(app: &mut App, command: &str) -> bool {
    if matches!(command, "/backend" | "/diarize" | "/formats" | "/speakers") {
        app.command_menu = Some(command.to_owned());
        app.command_menu_index = if command == "/backend" {
            command_menu_options(app)
                .iter()
                .position(|option| option == &app.backend)
                .unwrap_or(0)
        } else {
            0
        };
        app.status = "Choose 1–9, 0 for Back, or arrows and Enter".into();
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
    if option == BACK_MENU_OPTION {
        app.command_menu = None;
        app.input.clear();
        app.status = "Settings menu closed".into();
        app.log(app.status.clone());
        return;
    }
    let command = app.command_menu.clone().unwrap_or_default();
    match command.as_str() {
        "/backend" => {
            app.backend = option.clone();
            app.status = format!("Backend: {}", app.backend);
            app.command_menu = None;
            app.input.clear();
            save_app_settings(app);
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
        "/pets" => {
            if app.pet_enabled {
                app.pet_enabled = false;
                app.pet_image = None;
                app.status = "Pets off".into();
                save_app_settings(app);
            } else if let Err(error) = app.refresh_pet_image() {
                app.status = error;
            } else {
                app.pet_enabled = true;
                app.pet_running = app.running;
                app.status = "Pets on · /pets to hide".into();
                save_app_settings(app);
            }
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
        "/backend" if backend_is_supported(&argument.to_ascii_lowercase()) => {
            app.backend = argument.to_ascii_lowercase();
            app.status = format!("Backend: {}", app.backend);
            save_app_settings(app);
        }
        "/backend" => app.status = backend_usage().into(),
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

fn request_exit(
    app: &mut App,
    quit: &mut bool,
    last_exit_request: &mut Option<(&'static str, Instant)>,
    trigger: &'static str,
    label: &str,
) {
    if last_exit_request
        .is_some_and(|(last_trigger, at)| last_trigger == trigger && at.elapsed() <= Duration::from_millis(700))
    {
        *quit = true;
    } else {
        *last_exit_request = Some((trigger, Instant::now()));
        app.status = format!("Press {label} again to exit");
    }
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

fn draw(frame: &mut ratatui::Frame, app: &mut App) {
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
    if app.pet_enabled {
        if let Some(image) = app.pet_image.as_mut() {
            let pet_area = Rect::new(
                chunks[1].right().saturating_sub(26),
                chunks[1].y.saturating_add(1),
                24.min(chunks[1].width.saturating_sub(2)),
                16.min(chunks[1].height.saturating_sub(2)),
            );
            if pet_area.width >= 16 && pet_area.height >= 10 {
                frame.render_stateful_widget(StatefulImage::default(), pet_area, image);
            }
        }
    }
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
                            if option == BACK_MENU_OPTION {
                                "0. ".into()
                            } else {
                                format!("{}. ", index + 1)
                            },
                            Style::default().fg(Color::DarkGray),
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
    let footer = "Enter add path · Tab complete · type / for commands · s start · Esc cancel · Esc×2 / Ctrl+C×2 exit · l logs";
    frame.render_widget(
        Paragraph::new(footer).style(Style::default().fg(Color::Gray)),
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
    let settings = load_settings();
    app.pet_enabled = settings.pet_enabled;
    if backend_is_supported(&settings.backend) {
        app.backend = settings.backend;
    }
    app.pet_picker = Picker::from_query_stdio()
        .ok()
        .filter(|picker| picker.protocol_type() != ProtocolType::Halfblocks);
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
        let pet_frame_interval = 470;
        if app.pet_enabled
            && (app.pet_running != app.running
                || last_pet_frame.elapsed() >= Duration::from_millis(pet_frame_interval))
        {
            if app.pet_running != app.running {
                app.pet_frame = 0;
                app.pet_running = app.running;
            } else {
                app.pet_frame = app.pet_frame.wrapping_add(1);
            }
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
                            request_exit(
                                &mut app,
                                &mut quit,
                                &mut last_exit_request,
                                "esc",
                                "Esc",
                            );
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shell_path_split_keeps_escaped_and_quoted_spaces() {
        assert_eq!(
            split_shell_paths(r#"/tmp/first\ file.mp3 "/tmp/second file.mp3""#),
            vec!["/tmp/first file.mp3", "/tmp/second file.mp3"]
        );
    }

    #[test]
    fn settings_preserve_the_selected_backend() {
        let settings = TuiSettings {
            pet_enabled: true,
            backend: "mlx".into(),
        };
        let restored: TuiSettings = serde_json::from_str(
            &serde_json::to_string(&settings).expect("settings serialize"),
        )
        .expect("settings deserialize");

        assert!(restored.pet_enabled);
        assert_eq!(restored.backend, "mlx");
    }

    #[test]
    fn backend_command_opens_its_choices_with_the_current_value_selected() {
        let mut app = App::default();
        app.backend = selectable_backends()[0].into();

        assert!(open_command_menu(&mut app, "/backend"));

        assert_eq!(app.command_menu.as_deref(), Some("/backend"));
        assert_eq!(app.command_menu_index, 0);
        assert!(command_menu_options(&app).iter().any(|option| option == &app.backend));
    }

    #[test]
    fn clear_suggestion_executes_without_an_extra_enter() {
        let mut app = App::default();
        app.files.push("/tmp/input.wav".into());
        app.result_files.push("/tmp/output.txt".into());

        accept_command_suggestion(&mut app, "/clear");

        assert!(app.files.is_empty());
        assert!(app.result_files.is_empty());
        assert!(app.input.is_empty());
    }

    #[test]
    fn back_option_closes_a_settings_menu_without_applying_a_change() {
        let mut app = App::default();
        app.command_menu = Some("/diarize".into());
        app.command_menu_index = command_menu_options(&app)
            .iter()
            .position(|option| option == BACK_MENU_OPTION)
            .expect("Back option must be present");

        apply_command_menu(&mut app);

        assert!(app.command_menu.is_none());
        assert!(!app.diarization);
    }

    #[test]
    fn queue_paths_accepts_multiple_pasted_lines() {
        let directory = std::env::temp_dir().join(format!(
            "gigaam-tui-queue-paths-{}-{}",
            std::process::id(),
            std::thread::current().name().unwrap_or("test")
        ));
        fs::create_dir_all(&directory).unwrap();
        let first = directory.join("first.wav");
        let second = directory.join("second file.mp3");
        fs::write(&first, []).unwrap();
        fs::write(&second, []).unwrap();

        let mut app = App::default();
        queue_paths(
            &mut app,
            &format!("{}\n{}", first.display(), second.display()),
        );

        assert_eq!(app.files.len(), 2);
        assert!(app.files.iter().any(|path| path.ends_with("first.wav")));
        assert!(app.files.iter().any(|path| path.ends_with("second file.mp3")));
        fs::remove_dir_all(directory).unwrap();
    }
}

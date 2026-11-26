//! Native LLM commands for the desktop shell.
//!
//! Register `LlmState::default()` with Tauri and expose `llm_process`,
//! `llm_cancel`, and (optionally) `llm_tools`. The request contains transcript
//! text, a mode (`summary`, `tasks`, `terms`, or `custom`), an optional custom
//! prompt, and provider settings. Secrets are request-scoped, never persisted
//! or included in errors. CLI providers authenticate using their own config.
//! Custom CLI arguments are individual argv entries, not a shell command; the
//! transcript and prompt are always sent over stdin, never in argv.

use std::{
    env,
    io,
    net::IpAddr,
    path::{Path, PathBuf},
    process::Stdio,
    sync::Mutex,
    time::Duration,
};

use command_group::{AsyncCommandGroup, AsyncGroupChild};
use reqwest::{header::HeaderValue, redirect::Policy, Url};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio::{
    io::{AsyncRead, AsyncReadExt, AsyncWriteExt},
    process::Command,
    sync::watch,
};
use zeroize::{Zeroize, Zeroizing};

const MAX_TRANSCRIPT_BYTES: usize = 4 * 1024 * 1024;
const MAX_PROMPT_BYTES: usize = 64 * 1024;
const MAX_RESPONSE_BYTES: usize = 8 * 1024 * 1024;
const REQUEST_TIMEOUT: Duration = Duration::from_secs(180);
const SYSTEM_PROMPT: &str = "Ты обрабатываешь транскрипт на русском языке. Не выдумывай факты, явно помечай неясности.";
const SUMMARY_PROMPT: &str = "Ты аналитик встреч и голосовых сообщений. Сделай сильную, плотную и полезную выжимку транскрипта на русском языке. Убери повторы, слова-паразиты и шум распознавания. Сохрани только смысл.\n\nСтруктура ответа:\n1. Краткое резюме в 3-6 пунктах.\n2. Ключевые договоренности и решения.\n3. Важные факты, цифры, сроки, имена и роли — если они есть.\n4. Риски, спорные места или открытые вопросы — если они есть.\n\nПиши четко, по делу, без воды. Если часть информации в транскрипте неясна, пометь это явно и не выдумывай.";
const TASKS_PROMPT: &str = "Ты project manager assistant. Из транскрипта выдели только конкретные задачи и оформи их в максимально рабочем виде на русском языке. Игнорируй рассуждения, повторы и фоновые фразы. Не выдумывай задачи, которых нет в тексте.\n\nДля каждой задачи укажи:\n- Что нужно сделать\n- Кто ответственный / исполнитель, если это можно понять\n- Срок, дедлайн или ориентир по времени, если упомянут\n- Контекст или комментарий, если он важен\n- Приоритет, если он читается из разговора\n\nСначала дай список задач. Затем отдельным коротким блоком выведи: «Открытые вопросы / неясности». Если задач нет, напиши: «Явных задач не найдено».";
const TERMS_PROMPT: &str = "Выдели основные термины, имена, организации, сокращения и их контекст.";

#[derive(Deserialize)]
pub struct LlmRequest {
    text: String,
    #[serde(default = "default_mode")]
    mode: String,
    #[serde(default)]
    prompt: String,
    settings: LlmSettings,
}

fn default_mode() -> String {
    "summary".into()
}

#[derive(Deserialize)]
struct LlmSettings {
    #[serde(default = "default_provider")]
    provider: String,
    #[serde(default)]
    api_url: String,
    #[serde(default)]
    api_key: String,
    #[serde(default = "default_api_format")]
    api_format: String,
    #[serde(default)]
    model: String,
    #[serde(default = "default_temperature")]
    temperature: f64,
    #[serde(default)]
    cli_path: String,
    #[serde(default)]
    cli_args: Vec<String>,
    #[serde(default)]
    cli_provider: String,
    #[serde(default)]
    allow_tools: bool,
}

fn default_provider() -> String { "API".into() }
fn default_api_format() -> String { "auto".into() }
fn default_temperature() -> f64 { 0.2 }

impl Drop for LlmSettings {
    fn drop(&mut self) {
        self.api_key.zeroize();
    }
}

#[derive(Serialize)]
pub struct LlmResponse {
    text: String,
}

/// At most one request can be active. Cancellation retains the slot until the
/// HTTP future is dropped or the CLI has actually been terminated and reaped.
#[derive(Default)]
pub struct LlmState {
    active: Mutex<Option<watch::Sender<bool>>>,
}

struct ActiveRequest<'a>(&'a LlmState);

impl Drop for ActiveRequest<'_> {
    fn drop(&mut self) {
        if let Ok(mut active) = self.0.active.lock() {
            *active = None;
        }
    }
}

impl LlmState {
    fn begin(&self) -> Result<(ActiveRequest<'_>, watch::Receiver<bool>), String> {
        let mut active = self.active.lock().map_err(|_| "Ошибка состояния LLM".to_string())?;
        if active.is_some() {
            return Err("LLM-обработка уже выполняется".into());
        }
        let (sender, receiver) = watch::channel(false);
        *active = Some(sender);
        Ok((ActiveRequest(self), receiver))
    }

    fn cancel(&self) -> Result<(), String> {
        let active = self.active.lock().map_err(|_| "Ошибка состояния LLM".to_string())?;
        let sender = active.as_ref().ok_or_else(|| "Нет активного LLM-запроса".to_string())?;
        sender.send(true).map_err(|_| "LLM-запрос уже завершён".to_string())
    }
}

#[tauri::command]
pub async fn llm_process(
    request: LlmRequest,
    state: tauri::State<'_, LlmState>,
) -> Result<LlmResponse, String> {
    let text = request.text.trim();
    if text.is_empty() {
        return Err("Добавьте текст транскрипта".into());
    }
    if text.len() > MAX_TRANSCRIPT_BYTES {
        return Err("Транскрипт превышает 4 МиБ".into());
    }
    let prompt = match request.mode.as_str() {
        "summary" => SUMMARY_PROMPT,
        "tasks" => TASKS_PROMPT,
        "terms" => TERMS_PROMPT,
        "custom" if !request.prompt.trim().is_empty() => request.prompt.trim(),
        "custom" => return Err("Введите пользовательскую инструкцию".into()),
        _ => return Err("Неизвестный режим LLM".into()),
    };
    if prompt.len() > MAX_PROMPT_BYTES {
        return Err("Инструкция превышает 64 КиБ".into());
    }

    let (_active, mut cancelled) = state.begin()?;
    let settings = &request.settings;
    let answer = if matches!(settings.provider.as_str(), "API" | "api" | "OpenAI-compatible") {
        tokio::select! {
            biased;
            _ = cancelled.changed() => Err("LLM-запрос отменён".to_string()),
            result = run_http(settings, text, prompt) => result,
        }
    } else {
        run_cli(settings, text, prompt, &mut cancelled).await
    }?;
    Ok(LlmResponse { text: answer })
}

#[tauri::command]
pub fn llm_cancel(state: tauri::State<'_, LlmState>) -> Result<(), String> {
    state.cancel()
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum ApiFormat {
    OpenAi,
    Anthropic,
}

fn endpoint(raw: &str, requested_format: &str) -> Result<(Url, ApiFormat, bool), String> {
    let mut url = Url::parse(raw.trim()).map_err(|_| "Укажите корректный URL API".to_string())?;
    if url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err("URL API не должен содержать логин, пароль, параметры или фрагмент".into());
    }
    let host = url.host_str().unwrap_or_default();
    let bare_host = host.trim_matches(&['[', ']'][..]);
    let loopback = bare_host.eq_ignore_ascii_case("localhost")
        || bare_host.parse::<IpAddr>().map(|ip| ip.is_loopback()).unwrap_or(false);
    match url.scheme() {
        "https" => {}
        "http" if loopback => {}
        "http" => return Err("HTTP разрешён только для локального API; используйте HTTPS".into()),
        _ => return Err("Для API необходим URL с HTTPS или локальным HTTP".into()),
    }
    let format = match requested_format {
        "" | "auto" => {
            if bare_host.eq_ignore_ascii_case("anthropic.com")
                || bare_host.to_ascii_lowercase().ends_with(".anthropic.com")
                || url.path().trim_end_matches('/').ends_with("/v1/messages")
            {
                ApiFormat::Anthropic
            } else {
                ApiFormat::OpenAi
            }
        }
        "openai" => ApiFormat::OpenAi,
        "anthropic" => ApiFormat::Anthropic,
        _ => return Err("Неизвестный формат API".into()),
    };
    let path = url.path().trim_end_matches('/');
    let suffix = match format {
        ApiFormat::OpenAi if path.ends_with("/chat/completions") => "",
        ApiFormat::Anthropic if path.ends_with("/messages") => "",
        ApiFormat::OpenAi if path.ends_with("/v1") || path.ends_with("/openai") => "/chat/completions",
        ApiFormat::Anthropic if path.ends_with("/v1") => "/messages",
        _ if path.contains("/v1/") => "",
        ApiFormat::OpenAi => "/v1/chat/completions",
        ApiFormat::Anthropic => "/v1/messages",
    };
    let endpoint_path = format!("{path}{suffix}");
    url.set_path(&endpoint_path);
    Ok((url, format, loopback))
}

async fn run_http(settings: &LlmSettings, text: &str, prompt: &str) -> Result<String, String> {
    let (url, format, local) = endpoint(&settings.api_url, &settings.api_format)?;
    let model = settings.model.trim();
    if model.is_empty() || model.len() > 1024 {
        return Err("Укажите модель API".into());
    }
    if !settings.temperature.is_finite() || !(0.0..=2.0).contains(&settings.temperature) {
        return Err("Temperature должна быть в диапазоне от 0 до 2".into());
    }
    let api_key = settings.api_key.trim();
    if api_key.is_empty() && !local {
        return Err("Укажите API Key".into());
    }
    let user_message = format!("Инструкция:\n{prompt}\n\nТранскрипт:\n{text}");
    let payload = match format {
        ApiFormat::OpenAi => json!({
            "model": model,
            "temperature": settings.temperature,
            "stream": false,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message}
            ]
        }),
        ApiFormat::Anthropic => json!({
            "model": model,
            "max_tokens": 4096,
            "temperature": settings.temperature,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_message}]
        }),
    };
    let client = reqwest::Client::builder()
        .redirect(Policy::none())
        .timeout(REQUEST_TIMEOUT)
        .build()
        .map_err(|_| "Не удалось создать HTTP-клиент".to_string())?;
    let mut request = client.post(url).json(&payload);
    if format == ApiFormat::Anthropic {
        request = request.header("anthropic-version", "2023-06-01");
    }
    if !api_key.is_empty() {
        let header = Zeroizing::new(match format {
            ApiFormat::OpenAi => format!("Bearer {api_key}"),
            ApiFormat::Anthropic => api_key.to_owned(),
        });
        let mut value = HeaderValue::from_str(&header)
            .map_err(|_| "API Key содержит недопустимые символы".to_string())?;
        value.set_sensitive(true);
        request = match format {
            ApiFormat::OpenAi => request.header(reqwest::header::AUTHORIZATION, value),
            ApiFormat::Anthropic => request.header("x-api-key", value),
        };
    }
    // Neither reqwest's error (which can include URLs) nor the server's error
    // body is returned: either may contain credentials or submitted text.
    let mut response = request.send().await.map_err(|error| {
        if error.is_timeout() {
            "Время ожидания LLM API истекло".to_string()
        } else {
            "Не удалось подключиться к LLM API".to_string()
        }
    })?;
    let status = response.status();
    if !status.is_success() {
        return Err(format!("LLM API вернул HTTP {}. Проверьте адрес, ключ и модель.", status.as_u16()));
    }
    if response.content_length().is_some_and(|length| length > MAX_RESPONSE_BYTES as u64) {
        return Err("Ответ LLM превышает допустимый размер".into());
    }
    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(|_| "Не удалось прочитать ответ LLM API".to_string())? {
        if bytes.len().saturating_add(chunk.len()) > MAX_RESPONSE_BYTES {
            return Err("Ответ LLM превышает допустимый размер".into());
        }
        bytes.extend_from_slice(&chunk);
    }
    let body: Value = serde_json::from_slice(&bytes).map_err(|_| "LLM API вернул некорректный JSON".to_string())?;
    let content = match format {
        ApiFormat::OpenAi => body.pointer("/choices/0/message/content"),
        ApiFormat::Anthropic => body.get("content"),
    };
    let answer = extract_content(content).ok_or_else(|| "LLM API вернул ответ без текста".to_string())?;
    Ok(answer)
}

fn extract_content(content: Option<&Value>) -> Option<String> {
    let content = content?;
    let answer = if let Some(text) = content.as_str() {
        text.trim().to_owned()
    } else {
        content.as_array()?.iter().filter_map(|item| {
            if let Some(text) = item.as_str() {
                Some(text)
            } else if item.get("type").and_then(Value::as_str) == Some("text") {
                item.get("text").and_then(Value::as_str)
            } else {
                None
            }
        }).filter(|text| !text.trim().is_empty()).collect::<Vec<_>>().join("\n")
    };
    if answer.is_empty() { None } else { Some(answer) }
}

#[derive(Serialize)]
pub struct LlmTool {
    provider: &'static str,
    available: bool,
    path: Option<String>,
}

/// Filesystem-only discovery: it never starts a process or opens a connection.
#[tauri::command]
pub fn llm_tools() -> Vec<LlmTool> {
    let dirs = search_dirs();
    [("Claude Code", "claude"), ("Codex", "codex"), ("OpenCode", "opencode"), ("Pi", "pi"), ("oh-my-pi", "omp")]
        .into_iter()
        .map(|(provider, binary)| {
            let path = locate(binary, &dirs).map(|p| p.to_string_lossy().into_owned());
            LlmTool { provider, available: path.is_some(), path }
        })
        .collect()
}

fn search_dirs() -> Vec<PathBuf> {
    let mut dirs: Vec<PathBuf> = env::split_paths(&env::var_os("PATH").unwrap_or_default()).collect();
    let home = env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).map(PathBuf::from);
    if let Some(home) = home {
        for suffix in [".local/bin", ".bun/bin", ".npm-global/bin", ".volta/bin", ".cargo/bin", ".omp/bin", ".pi/bin", ".local/share/pnpm", "scoop/shims"] {
            dirs.push(home.join(suffix));
        }
        let nvm = home.join(".nvm/versions/node");
        if let Ok(entries) = std::fs::read_dir(nvm) {
            let mut versions: Vec<_> = entries.filter_map(Result::ok).map(|entry| entry.path().join("bin")).collect();
            versions.sort();
            versions.reverse();
            dirs.extend(versions);
        }
    }
    if cfg!(windows) {
        if let Some(appdata) = env::var_os("APPDATA") { dirs.push(PathBuf::from(appdata).join("npm")); }
        if let Some(local) = env::var_os("LOCALAPPDATA") { dirs.push(PathBuf::from(local).join("pnpm")); }
    } else {
        dirs.extend([PathBuf::from("/usr/local/bin"), PathBuf::from("/opt/homebrew/bin"), PathBuf::from("/snap/bin")]);
    }
    dirs.retain(|path| !path.as_os_str().is_empty() && path.is_dir());
    dirs
}

fn executable(path: &Path) -> bool {
    let Ok(metadata) = path.metadata() else { return false; };
    if !metadata.is_file() { return false; }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        metadata.permissions().mode() & 0o111 != 0
    }
    #[cfg(not(unix))]
    { true }
}

fn locate(name: &str, dirs: &[PathBuf]) -> Option<PathBuf> {
    let explicit = name.contains('/') || name.contains('\\') || Path::new(name).is_absolute();
    let expanded = if let Some(rest) = name.strip_prefix("~/") {
        env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).map(|home| PathBuf::from(home).join(rest))?
    } else {
        PathBuf::from(name)
    };
    if explicit {
        return executable(&expanded).then_some(expanded);
    }
    for dir in dirs {
        let candidate = dir.join(&expanded);
        if executable(&candidate) { return Some(candidate); }
        if cfg!(windows) && candidate.extension().is_none() {
            for extension in ["exe", "cmd", "bat", "com"] {
                let path = candidate.with_extension(extension);
                if executable(&path) { return Some(path); }
            }
        }
    }
    None
}

// Kill the whole owned group if this future is dropped unexpectedly. Unix uses a
// process group; on Windows command-group creates a Job Object.
struct CliChild {
    child: AsyncGroupChild,
    finished: bool,
}

impl Drop for CliChild {
    fn drop(&mut self) {
        if !self.finished {
            let _ = self.child.start_kill();
        }
    }
}

async fn read_pipe<R: AsyncRead + Unpin>(mut reader: R, keep: bool) -> io::Result<Vec<u8>> {
    let mut output = Vec::new();
    let mut count = 0usize;
    let mut buffer = [0u8; 8192];
    loop {
        let read = reader.read(&mut buffer).await?;
        if read == 0 { break; }
        count = count.saturating_add(read);
        if count > MAX_RESPONSE_BYTES {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "CLI output limit"));
        }
        if keep { output.extend_from_slice(&buffer[..read]); }
    }
    Ok(output)
}

async fn run_cli(
    settings: &LlmSettings,
    text: &str,
    prompt: &str,
    cancelled: &mut watch::Receiver<bool>,
) -> Result<String, String> {
    let (provider, binary) = match settings.provider.as_str() {
        "Claude Code" | "claude" => ("claude", "claude"),
        "Codex" | "codex" => ("codex", "codex"),
        "OpenCode" | "opencode" => ("opencode", "opencode"),
        "Pi" | "pi" => ("pi", "pi"),
        "oh-my-pi" | "omp" => ("omp", "omp"),
        "Other" | "other" | "Другое" => ("other", ""),
        _ => return Err("Неизвестный LLM-провайдер".into()),
    };
    if matches!(provider, "codex" | "opencode" | "other") && !settings.allow_tools {
        return Err("Этот CLI может выполнять действия или сохранять историю. Явно разрешите инструменты перед запуском.".into());
    }
    if !settings.allow_tools && !settings.cli_args.is_empty() {
        return Err("Дополнительные аргументы CLI требуют разрешения инструментов".into());
    }
    if settings.cli_args.len() > 64 || settings.cli_args.iter().any(|arg| arg.len() > 8192 || arg.contains('\0')) {
        return Err("Некорректные аргументы CLI".into());
    }
    let model = settings.model.trim();
    let inner_provider = settings.cli_provider.trim();
    if model.len() > 1024 || model.starts_with('-') || inner_provider.len() > 1024 || inner_provider.starts_with('-') {
        return Err("Некорректное имя модели или провайдера".into());
    }
    let requested = if settings.cli_path.trim().is_empty() { binary } else { settings.cli_path.trim() };
    if requested.is_empty() || requested.contains('\0') {
        return Err("Укажите исполняемый файл CLI".into());
    }
    let dirs = search_dirs();
    let path = locate(requested, &dirs).ok_or_else(|| "CLI не найден. Установите его или укажите путь в настройках.".to_string())?;
    let mut args: Vec<String> = Vec::new();
    match provider {
        "claude" => {
            args.extend(["-p".into(), "--output-format".into(), "text".into()]);
            if !model.is_empty() { args.extend(["--model".into(), model.into()]); }
            if !settings.allow_tools { args.extend(["--tools".into(), "".into(), "--no-session-persistence".into()]); }
        }
        "codex" => {
            args.extend(["exec".into(), "--json".into(), "--skip-git-repo-check".into(), "--sandbox".into(), "read-only".into()]);
            if !model.is_empty() { args.extend(["--model".into(), model.into()]); }
        }
        "opencode" => {
            // A private server keeps the request in the owned process group;
            // shared OpenCode services can otherwise outlive cancellation.
            args.extend(["run".into(), "--standalone".into(), "--format".into(), "json".into()]);
            if !model.is_empty() { args.extend(["--model".into(), model.into()]); }
        }
        "pi" | "omp" => {
            args.extend(["-p".into(), "--mode".into(), "text".into()]);
            if !inner_provider.is_empty() { args.extend(["--provider".into(), inner_provider.into()]); }
            if !model.is_empty() { args.extend(["--model".into(), model.into()]); }
            if !settings.allow_tools { args.extend(["--no-tools".into(), "--no-session".into()]); }
        }
        _ => {}
    }
    args.extend(settings.cli_args.iter().filter(|arg| arg.as_str() != "{stdin}").cloned());
    if provider == "codex" { args.push("-".into()); }
    // Windows batch shims are interpreted by cmd.exe. Never let untrusted
    // model/provider values or argv metacharacters become shell syntax.
    if cfg!(windows)
        && path.extension().and_then(|ext| ext.to_str()).is_some_and(|ext| ext.eq_ignore_ascii_case("cmd") || ext.eq_ignore_ascii_case("bat"))
        && args.iter().any(|arg| arg.chars().any(|ch| "&|<>^()%!\"\r\n".contains(ch)))
    {
        return Err("Аргументы Windows CLI содержат недопустимые для .cmd/.bat символы".into());
    }
    let input = format!("{SYSTEM_PROMPT}\n\nИнструкция:\n{prompt}\n\nТранскрипт:\n{text}\n");
    let mut command = Command::new(path);
    command.args(args).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
    command.kill_on_drop(true);
    if let Ok(path) = env::join_paths(&dirs) { command.env("PATH", path); }
    let mut group = command.group();
    group.kill_on_drop(true);
    #[cfg(windows)]
    group.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    if *cancelled.borrow() {
        return Err("LLM-запрос отменён".into());
    }
    let child = group.spawn().map_err(|_| "Не удалось запустить CLI. Проверьте установку и путь.".to_string())?;
    let mut child = CliChild { child, finished: false };
    let mut stdin = child.child.inner().stdin.take().ok_or_else(|| "Не удалось открыть ввод CLI".to_string())?;
    let stdout = child.child.inner().stdout.take().ok_or_else(|| "Не удалось открыть вывод CLI".to_string())?;
    let stderr = child.child.inner().stderr.take().ok_or_else(|| "Не удалось открыть диагностику CLI".to_string())?;

    let result: Result<(_, Vec<u8>, Vec<u8>), String> = tokio::select! {
        biased;
        _ = cancelled.changed() => Err("LLM-запрос отменён".into()),
        _ = tokio::time::sleep(REQUEST_TIMEOUT) => Err("Время ожидания CLI истекло".into()),
        result = async {
            let write_input = async move {
                stdin.write_all(input.as_bytes()).await?;
                // ChildStdin::shutdown is a no-op on Unix pipes; dropping the
                // handle is what delivers EOF to CLI tools reading all stdin.
                drop(stdin);
                Ok::<(), io::Error>(())
            };
            let (_, output, diagnostic) = tokio::try_join!(write_input, read_pipe(stdout, true), read_pipe(stderr, true))
                .map_err(|_| "Ошибка обмена с CLI или превышен размер ответа".to_string())?;
            let status = child.child.inner().wait().await.map_err(|_| "Не удалось дождаться CLI".to_string())?;
            Ok((status, output, diagnostic))
        } => result,
    };
    let (status, output, diagnostic) = match result {
        Ok(done) => { child.finished = true; done }
        Err(error) => {
            let killed = child.child.start_kill().is_ok();
            let waited = tokio::time::timeout(Duration::from_secs(5), child.child.inner().wait()).await;
            if killed && matches!(waited, Ok(Ok(_))) {
                child.finished = true;
                return Err(error);
            }
            return Err("Не удалось подтвердить завершение CLI после отмены или ошибки".into());
        }
    };
    if !status.success() {
        return Err(format!("CLI завершился с ошибкой (код {}). {}", status.code().map(|code| code.to_string()).unwrap_or_else(|| "нет".into()), cli_failure_hint(&diagnostic)));
    }
    let output = String::from_utf8(output).map_err(|_| "CLI вернул текст не в UTF-8".to_string())?;
    let answer = match provider {
        "codex" => codex_text(&output),
        "opencode" => opencode_text(&output),
        _ => Some(output.trim().to_owned()).filter(|text| !text.is_empty()),
    };
    answer.ok_or_else(|| "CLI вернул ответ без текста".to_string())
}

// CLI diagnostics can include prompts, file paths or credentials. Classify
// common configuration failures without ever returning the original text.
fn cli_failure_hint(diagnostic: &[u8]) -> &'static str {
    let diagnostic = String::from_utf8_lossy(diagnostic).to_ascii_lowercase();
    if diagnostic.contains("unauthorized") || diagnostic.contains("not authenticated") || diagnostic.contains("login required") {
        "Проверьте авторизацию в выбранном CLI."
    } else if diagnostic.contains("unexpected argument") || diagnostic.contains("unknown option") || diagnostic.contains("unrecognized argument") {
        "Проверьте дополнительные аргументы CLI."
    } else if diagnostic.contains("model not found") || diagnostic.contains("unknown model") {
        "Проверьте имя модели и доступ к ней."
    } else {
        "Проверьте установку, вход и настройки."
    }
}

fn codex_text(output: &str) -> Option<String> {
    output.lines().filter_map(|line| serde_json::from_str::<Value>(line).ok()).filter_map(|event| {
        if event.get("type").and_then(Value::as_str) == Some("item.completed")
            && event.pointer("/item/type").and_then(Value::as_str) == Some("agent_message")
        {
            event.pointer("/item/text").and_then(Value::as_str).map(str::to_owned)
        } else { None }
    }).filter(|text| !text.trim().is_empty()).last().map(|text| text.trim().to_owned())
}

fn opencode_text(output: &str) -> Option<String> {
    let parts: Vec<String> = output.lines().filter_map(|line| serde_json::from_str::<Value>(line).ok()).filter_map(|event| {
        if event.get("type").and_then(Value::as_str) == Some("text") {
            event.pointer("/part/text").and_then(Value::as_str).map(str::to_owned)
        } else { None }
    }).filter(|text| !text.trim().is_empty()).collect();
    let answer = parts.join("\n");
    if answer.is_empty() { None } else { Some(answer) }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn endpoint_accepts_real_openai_and_anthropic_shapes() {
        let (url, kind, _) = endpoint("https://api.openai.com/v1/", "auto").unwrap();
        assert!(kind == ApiFormat::OpenAi);
        assert_eq!(url.as_str(), "https://api.openai.com/v1/chat/completions");
        let (url, kind, _) = endpoint("https://api.anthropic.com", "auto").unwrap();
        assert!(kind == ApiFormat::Anthropic);
        assert_eq!(url.as_str(), "https://api.anthropic.com/v1/messages");
        let (url, _, local) = endpoint("http://127.0.0.1:11434/v1", "auto").unwrap();
        assert!(local);
        assert_eq!(url.path(), "/v1/chat/completions");
        let (url, _, _) = endpoint("https://generativelanguage.googleapis.com/v1beta/openai/", "auto").unwrap();
        assert_eq!(url.path(), "/v1beta/openai/chat/completions");
    }

    #[test]
    fn endpoint_rejects_insecure_and_secret_bearing_urls() {
        for url in ["http://example.com/v1", "https://key@example.com/v1", "https://example.com/v1?key=secret", "https://example.com/v1#secret", "file:///tmp/test"] {
            assert!(endpoint(url, "auto").is_err());
        }
        assert!(endpoint("https://example.com", "unknown").is_err());
    }

    #[test]
    fn parses_supported_text_and_never_returns_tool_calls() {
        let content = json!([{"type":"text","text":"one"}, {"type":"tool_use","text":"not an answer"}, {"type":"text","text":"two"}]);
        assert_eq!(extract_content(Some(&content)).as_deref(), Some("one\ntwo"));
        assert!(extract_content(Some(&json!([]))).is_none());
        let codex = "{\"type\":\"item.completed\",\"item\":{\"type\":\"agent_message\",\"text\":\"answer\"}}";
        assert_eq!(codex_text(codex).as_deref(), Some("answer"));
        let opencode = "{\"type\":\"text\",\"part\":{\"text\":\"answer\"}}";
        assert_eq!(opencode_text(opencode).as_deref(), Some("answer"));
    }

    #[test]
    fn cli_failure_hints_do_not_echo_sensitive_diagnostics() {
        assert!(cli_failure_hint(b"unexpected argument secret-value").contains("аргументы"));
        assert!(cli_failure_hint(b"unauthorized secret-value").contains("авторизац"));
        assert!(cli_failure_hint(b"unknown model secret-value").contains("модел"));
        assert!(!cli_failure_hint(b"secret-value").contains("secret-value"));
    }

    #[test]
    fn only_one_job_and_cancel_does_not_release_its_slot() {
        let state = LlmState::default();
        let (guard, receiver) = state.begin().unwrap();
        assert!(state.begin().is_err());
        state.cancel().unwrap();
        assert!(*receiver.borrow());
        assert!(state.begin().is_err());
        drop(guard);
        assert!(state.begin().is_ok());
    }
}

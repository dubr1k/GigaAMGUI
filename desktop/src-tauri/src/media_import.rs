use std::collections::HashMap;
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use reqwest::header::{HeaderMap, HeaderName, HeaderValue, AUTHORIZATION, CONTENT_RANGE, CONTENT_TYPE, COOKIE, PROXY_AUTHORIZATION, USER_AGENT};
use reqwest::{Client, StatusCode, Url};
use serde::Serialize;
use tauri::{AppHandle, Emitter, State};
use tempfile::TempDir;
use tokio::sync::watch;
use ytdown::{Container, Format, MediaInfo, Ytdown};

const MAX_BYTES: u64 = 2 * 1024 * 1024 * 1024;
const CANCELLED: &str = "Загрузка отменена";
const TOO_LARGE: &str = "Файл превышает ограничение загрузки (2 ГиБ)";
const UNSUPPORTED: &str = "Ссылка не поддерживается: нужна прямая ссылка на аудио/видео или поддерживаемая публичная запись";
const TIKTOK_USER_AGENT: &str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36";

#[derive(Default)]
pub struct MediaImportState {
    active: Arc<Mutex<Option<ActiveImport>>>,
    files: Mutex<HashMap<String, TempDir>>,
}

struct ActiveImport {
    id: String,
    cancel: watch::Sender<bool>,
}

struct ImportGuard {
    active: Arc<Mutex<Option<ActiveImport>>>,
    id: String,
}

impl Drop for ImportGuard {
    fn drop(&mut self) {
        let mut active = self.active.lock().unwrap_or_else(|error| error.into_inner());
        if active.as_ref().is_some_and(|job| job.id == self.id) {
            *active = None;
        }
    }
}

// On cancellation the open file must be closed before its directory is removed,
// including on Windows where an open handle can prevent deletion.
struct StagedMedia {
    file: File,
    directory: TempDir,
    path: PathBuf,
}

#[derive(Clone, Serialize)]
struct Progress<'a> {
    job_id: &'a str,
    stage: &'static str,
    downloaded: u64,
    total: Option<u64>,
}

fn progress(app: &AppHandle, job_id: &str, stage: &'static str, downloaded: u64, total: Option<u64>) {
    let _ = app.emit("media-import-progress", Progress { job_id, stage, downloaded, total });
}

fn begin_import(state: &MediaImportState, id: &str) -> Result<(ImportGuard, watch::Receiver<bool>), String> {
    if id.is_empty() || id.len() > 128 || !id.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.')) {
        return Err("Недопустимый идентификатор загрузки".into());
    }
    let mut active = state.active.lock().map_err(|_| "Не удалось открыть список загрузок")?;
    if active.is_some() {
        return Err("Другая загрузка ещё выполняется".into());
    }
    let (cancel, receiver) = watch::channel(false);
    *active = Some(ActiveImport { id: id.to_owned(), cancel });
    Ok((ImportGuard { active: state.active.clone(), id: id.to_owned() }, receiver))
}

#[tauri::command]
pub fn cancel_media_import(state: State<'_, MediaImportState>, job_id: String) -> Result<bool, String> {
    let active = state.active.lock().map_err(|_| "Не удалось открыть список загрузок")?;
    if let Some(job) = active.as_ref().filter(|job| job.id == job_id) {
        job.cancel.send_replace(true);
        Ok(true)
    } else {
        Ok(false)
    }
}

#[tauri::command]
pub fn release_media_import(state: State<'_, MediaImportState>, path: String) -> Result<bool, String> {
    let mut files = state.files.lock().map_err(|_| "Не удалось открыть список временных файлов")?;
    let Some(directory) = files.get(&path) else { return Ok(false); };
    match std::fs::remove_dir_all(directory.path()) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(_) => return Err("Не удалось удалить временный загруженный файл".into()),
    }
    files.remove(&path);
    Ok(true)
}

fn validate_url(raw: &str) -> Result<Url, String> {
    if raw.len() > 16 * 1024 {
        return Err("Ссылка слишком длинная".into());
    }
    let url = Url::parse(raw.trim()).map_err(|_| "Введите корректную ссылку HTTP или HTTPS")?;
    if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() {
        return Err("Введите корректную ссылку HTTP или HTTPS".into());
    }
    if !url.username().is_empty() || url.password().is_some() {
        return Err("Ссылки с логином и паролем не поддерживаются".into());
    }
    Ok(url)
}

// ytdown 0.8.1 recognizes watch/shorts/embed, but not the /live/ share URL
// used for both broadcasts and their replays. Keep live rejection downstream.
fn youtube_live_url(url: &Url) -> Option<Url> {
    if !matches!(url.host_str()?, "youtube.com" | "www.youtube.com" | "m.youtube.com" | "music.youtube.com") {
        return None;
    }
    let mut segments = url.path_segments()?;
    if segments.next()? != "live" {
        return None;
    }
    let id = segments.next()?;
    if id.len() != 11
        || !id.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-'))
        || segments.any(|segment| !segment.is_empty())
    {
        return None;
    }
    let mut normalized = url.clone();
    normalized.set_path("/watch");
    normalized.set_query(None);
    normalized.set_fragment(None);
    normalized.query_pairs_mut().append_pair("v", id);
    Some(normalized)
}

// ytdown rewrites vm/vt shortlinks to www.tiktok.com/t/{code}. Follow the
// supplied mobile URL instead; those hosts may route the same code differently.
async fn tiktok_mobile_redirect(client: &Client, url: &Url) -> Result<Option<Url>, String> {
    if !matches!(url.host_str(), Some("vm.tiktok.com" | "vt.tiktok.com")) {
        return Ok(None);
    }
    let Some(segments) = url.path_segments() else { return Ok(None) };
    let mut segments = segments.filter(|segment| !segment.is_empty());
    if segments.next().is_none() || segments.next().is_some() {
        return Ok(None);
    }
    let response = tokio::time::timeout(
        Duration::from_secs(120),
        client.get(url.clone())
            .header(USER_AGENT, TIKTOK_USER_AGENT)
            .header(reqwest::header::ACCEPT, "text/html,application/xhtml+xml")
            .send(),
    ).await.map_err(|_| "Истекло время ожидания ответа TikTok".to_string())?
        .map_err(|_| "Не удалось открыть ссылку TikTok".to_string())?;
    if matches!(response.status(), StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN | StatusCode::TOO_MANY_REQUESTS) {
        return Err(format!("TikTok отклонил запрос (HTTP {})", response.status().as_u16()));
    }
    if !response.status().is_success() {
        return Ok(None);
    }
    let target = response.url();
    let Some(host) = target.host_str() else { return Ok(None) };
    if host != "tiktok.com" && !host.ends_with(".tiktok.com") {
        return Ok(None);
    }
    let Some(mut path) = target.path_segments() else { return Ok(None) };
    let (Some(user), Some(kind), Some(id)) = (path.next(), path.next(), path.next()) else { return Ok(None) };
    if user.len() <= 1 || !user.starts_with('@')
        || !matches!(kind, "video" | "photo")
        || id.is_empty() || !id.bytes().all(|b| b.is_ascii_digit())
    {
        return Ok(None);
    }
    Ok(Some(target.clone()))
}

fn safe_redirect(previous: &Url, next: &Url) -> bool {
    next.as_str().len() <= 16 * 1024
        && matches!(next.scheme(), "http" | "https")
        && next.host_str().is_some()
        && next.username().is_empty()
        && next.password().is_none()
        && !(previous.scheme() == "https" && next.scheme() == "http")
}

fn redirect_policy() -> reqwest::redirect::Policy {
    let limited = reqwest::redirect::Policy::limited(10);
    reqwest::redirect::Policy::custom(move |attempt| {
        if attempt.previous().last().is_some_and(|previous| !safe_redirect(previous, attempt.url())) {
            attempt.error("unsafe redirect")
        } else {
            limited.redirect(attempt)
        }
    })
}

fn extension_from_path(url: &Url) -> Option<&'static str> {
    let name = url.path_segments()?.next_back()?;
    extension_from_name(name.rsplit_once('.')?.1)
}

fn extension_from_name(ext: &str) -> Option<&'static str> {
    match ext.to_ascii_lowercase().as_str() {
        "mp3" => Some("mp3"),
        "m4a" | "m4b" => Some("m4a"),
        "mp4" | "m4v" => Some("mp4"),
        "mov" => Some("mov"),
        "mkv" => Some("mkv"),
        "webm" | "weba" => Some("webm"),
        "wav" | "wave" => Some("wav"),
        "ogg" | "oga" => Some("ogg"),
        "opus" => Some("opus"),
        "flac" => Some("flac"),
        "aac" => Some("aac"),
        _ => None,
    }
}

fn extension_from_mime(mime: &str) -> Option<&'static str> {
    match mime.split(';').next()?.trim().to_ascii_lowercase().as_str() {
        "audio/mpeg" | "audio/mp3" => Some("mp3"),
        "audio/mp4" | "audio/x-m4a" | "audio/m4a" => Some("m4a"),
        "video/mp4" | "application/mp4" => Some("mp4"),
        "video/quicktime" => Some("mov"),
        "video/x-matroska" | "audio/x-matroska" => Some("mkv"),
        "audio/webm" | "video/webm" => Some("webm"),
        "audio/wav" | "audio/x-wav" | "audio/wave" => Some("wav"),
        "audio/ogg" | "video/ogg" | "application/ogg" => Some("ogg"),
        "audio/opus" => Some("opus"),
        "audio/flac" | "audio/x-flac" => Some("flac"),
        "audio/aac" | "audio/aacp" => Some("aac"),
        _ => None,
    }
}

fn format_extension(format: &Format) -> Option<&'static str> {
    let container = match format.container.as_ref() {
        Some(Container::Mp4) => Some("mp4"),
        Some(Container::M4a) => Some("m4a"),
        Some(Container::WebM | Container::Weba) => Some("webm"),
        Some(Container::Other(ext)) => extension_from_name(ext).or_else(|| extension_from_mime(ext)),
        _ => None,
    };
    container.or_else(|| format.mime_type.as_deref().and_then(extension_from_mime))
}

fn select_format(formats: &[Format], limit: u64) -> Result<(&Format, Url, HeaderMap), String> {
    let mut supported = false;
    let mut oversized = false;
    let mut invalid_headers = false;
    let mut best = None;
    for format in formats {
        if format.audio.is_none() || format_extension(format).is_none() {
            continue;
        }
        let Ok(url) = validate_url(&format.url) else { continue };
        supported = true;
        if format.filesize.is_some_and(|size| size > limit) {
            oversized = true;
            continue;
        }
        let Ok(headers) = safe_headers(format, &url) else {
            invalid_headers = true;
            continue;
        };
        let rank = (format.video.is_none(), format.audio.as_ref().and_then(|audio| audio.bitrate).or(format.bitrate).unwrap_or(0));
        if best.as_ref().is_none_or(|(previous, _, _, _)| rank >= *previous) {
            best = Some((rank, format, url, headers));
        }
    }
    best.map(|(_, format, url, headers)| (format, url, headers)).ok_or_else(|| {
        if invalid_headers { "Площадка вернула недопустимые параметры загрузки".into() }
        else if supported && oversized { TOO_LARGE.into() }
        else { "На странице нет поддерживаемой аудиодорожки; трансляции и защищённое медиа не поддерживаются".into() }
    })
}

fn extraction_error(error: ytdown::Error) -> String {
    use ytdown::error::UnavailableReason;
    match error {
        ytdown::Error::Unavailable { reason, .. } => match reason {
            UnavailableReason::Gone => "Площадка не предоставила запись: она может быть недоступна либо загрузчик не смог её извлечь",
            UnavailableReason::AgeRestricted => "Запись ограничена по возрасту; авторизация не поддерживается",
            UnavailableReason::BotCheck => "Площадка запросила проверку или авторизацию; вход и проверка в приложении не поддерживаются",
            UnavailableReason::GeoBlocked => "Запись недоступна в вашем регионе",
            UnavailableReason::Live => "Прямые эфиры не поддерживаются",
            _ => "Площадка сообщает, что запись недоступна",
        }.into(),
        ytdown::Error::Extraction { stage, message } => match (stage, message.as_str()) {
            ("tiktok", "photo/slideshow posts are not supported") =>
                "Фотографии и слайд-шоу TikTok не поддерживаются; нужна видеозапись",
            ("tiktok", "webpage request failed with HTTP 403") =>
                "TikTok запретил доступ к странице; вход и проверка в приложении не поддерживаются",
            ("tiktok", "webpage request failed with HTTP 429") =>
                "TikTok ограничил число запросов; повторите позже",
            ("twitter", "syndication request failed with HTTP 403") =>
                "X запретил доступ к записи; авторизация в приложении не поддерживается",
            ("twitter", "syndication request failed with HTTP 429") =>
                "X ограничил число запросов; повторите позже",
            _ => "Не удалось извлечь медиа с этой страницы; площадка могла изменить формат или ограничить доступ",
        }.into(),
        ytdown::Error::Network { .. } => "Не удалось связаться с площадкой; проверьте подключение".into(),
        ytdown::Error::Cipher(_) => "Не удалось обработать формат ссылки площадки".into(),
        _ => "Не удалось извлечь медиа с этой страницы; площадка могла изменить формат или ограничить доступ".into(),
    }
}

fn safe_headers(format: &Format, url: &Url) -> Result<HeaderMap, String> {
    let mut headers = HeaderMap::new();
    for (name, value) in &format.http_headers {
        let name = HeaderName::from_bytes(name.as_bytes()).map_err(|_| "Площадка вернула недопустимые параметры загрузки")?;
        if url.scheme() != "https" && (name == COOKIE || name == AUTHORIZATION || name == PROXY_AUTHORIZATION) {
            return Err("Защищённые параметры загрузки требуют HTTPS".into());
        }
        let mut value = HeaderValue::from_str(value).map_err(|_| "Площадка вернула недопустимые параметры загрузки")?;
        value.set_sensitive(true);
        headers.append(name, value);
    }
    Ok(headers)
}

fn response_extension(response: &reqwest::Response, hint: Option<&str>) -> Result<&'static str, String> {
    let content_type = response.headers().get(CONTENT_TYPE).and_then(|value| value.to_str().ok());
    let fallback = || hint.and_then(extension_from_name).or_else(|| extension_from_path(response.url())).unwrap_or("bin");
    match content_type.map(|value| value.split(';').next().unwrap_or("").trim().to_ascii_lowercase()) {
        Some(mime) if matches!(mime.as_str(), "application/vnd.apple.mpegurl" | "application/x-mpegurl" | "audio/mpegurl" | "audio/x-mpegurl") => Err("Плейлисты и прямые эфиры не поддерживаются".into()),
        Some(mime) if mime == "application/octet-stream" || mime == "binary/octet-stream" || mime.starts_with("audio/") || mime.starts_with("video/") => {
            Ok(extension_from_mime(&mime).unwrap_or_else(fallback))
        }
        Some(mime) => extension_from_mime(&mime).ok_or_else(|| UNSUPPORTED.into()),
        None => Ok(fallback()),
    }
}

async fn media_response(client: &Client, url: &Url, headers: HeaderMap) -> Result<reqwest::Response, String> {
    let response = client.get(url.clone()).headers(headers).send().await.map_err(|error| {
        if error.is_timeout() { "Истекло время ожидания загрузки" } else { "Не удалось загрузить медиа; проверьте подключение" }
    })?;
    if response.status() == StatusCode::PARTIAL_CONTENT {
        return Err("Сервер вернул частичный файл; загрузка не поддерживается".into());
    }
    if response.status() != StatusCode::OK {
        let message = match response.status() {
            StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN => "Сервер запретил доступ к медиа",
            StatusCode::NOT_FOUND | StatusCode::GONE => "Медиа не найдено или больше недоступно",
            StatusCode::TOO_MANY_REQUESTS => "Сервер ограничил число запросов; повторите позже",
            status if status.is_server_error() => "Ошибка сервера при загрузке медиа",
            _ => "Сервер отклонил загрузку медиа",
        };
        return Err(format!("{message} (HTTP {})", response.status().as_u16()));
    }
    if response.headers().contains_key(CONTENT_RANGE) {
        return Err("Сервер вернул частичный файл; загрузка не поддерживается".into());
    }
    Ok(response)
}

fn redirected_webpage(original: &Url, response: &reqwest::Response) -> bool {
    response.url() != original
        && response.headers().get(CONTENT_TYPE)
            .and_then(|value| value.to_str().ok())
            .and_then(|value| value.split(';').next())
            .is_some_and(|mime| {
                let mime = mime.trim();
                mime.eq_ignore_ascii_case("text/html") || mime.eq_ignore_ascii_case("application/xhtml+xml")
            })
}

async fn download_media<F>(
    client: &Client,
    url: &Url,
    headers: HeaderMap,
    hint: Option<&str>,
    expected: Option<u64>,
    limit: u64,
    temp_root: &Path,
    on_progress: F,
) -> Result<(String, TempDir), String>
where
    F: FnMut(u64, Option<u64>),
{
    if expected.is_some_and(|size| size > limit) {
        return Err(TOO_LARGE.into());
    }
    let response = media_response(client, url, headers).await?;
    download_response(response, hint, expected, limit, temp_root, on_progress).await
}

async fn download_response<F>(
    mut response: reqwest::Response,
    hint: Option<&str>,
    expected: Option<u64>,
    limit: u64,
    temp_root: &Path,
    mut on_progress: F,
) -> Result<(String, TempDir), String>
where
    F: FnMut(u64, Option<u64>),
{
    if expected.is_some_and(|size| size > limit) {
        return Err(TOO_LARGE.into());
    }
    let declared = response.content_length();
    if declared.is_some_and(|size| size > limit) {
        return Err(TOO_LARGE.into());
    }
    let extension = response_extension(&response, hint)?;
    let directory = tempfile::Builder::new().prefix("gigaam-media-").tempdir_in(temp_root)
        .map_err(|_| "Не удалось создать временный каталог загрузки")?;
    let path = directory.path().join(format!("media.{extension}"));
    let file = OpenOptions::new().write(true).create_new(true).open(&path)
        .map_err(|_| "Не удалось создать временный файл загрузки")?;
    let mut staged = StagedMedia { file, directory, path };
    let mut count = 0u64;
    let total = declared.or(expected);
    let mut last_progress = Instant::now() - Duration::from_secs(1);
    while let Some(chunk) = response.chunk().await.map_err(|_| "Загрузка прервана; файл не добавлен в очередь")? {
        count = count.checked_add(chunk.len() as u64).ok_or(TOO_LARGE)?;
        if count > limit {
            return Err(TOO_LARGE.into());
        }
        staged.file.write_all(&chunk).map_err(|_| "Не удалось записать временный файл загрузки")?;
        if last_progress.elapsed() >= Duration::from_millis(200) {
            on_progress(count, total);
            last_progress = Instant::now();
        }
    }
    if count == 0 || declared.is_some_and(|length| length != count) || expected.is_some_and(|length| length != count) {
        return Err("Загрузка прервана или сервер вернул неполный файл".into());
    }
    staged.file.flush().map_err(|_| "Не удалось завершить запись временного файла")?;
    let path = staged.path.to_str().ok_or("Путь временного файла не поддерживается")?.to_owned();
    drop(staged.file);
    gigastt_core::inference::audio::probe_duration_file(&path)
        .map_err(|_| "Загруженные данные не являются поддерживаемым аудио или видео")?;
    on_progress(count, total);
    Ok((path, staged.directory))
}

async fn resolve_media(extractor: &Ytdown, url: &Url) -> Result<Result<MediaInfo, ytdown::Error>, String> {
    let normalized = youtube_live_url(url);
    let url = normalized.as_ref().unwrap_or(url);
    tokio::time::timeout(Duration::from_secs(120), extractor.resolve(url.as_str())).await
        .map_err(|_| "Истекло время ожидания ответа площадки".into())
}

async fn import_url<F>(
    client: &Client,
    extractor: &Ytdown,
    url: Url,
    limit: u64,
    temp_root: &Path,
    mut on_progress: F,
) -> Result<(String, TempDir), String>
where
    F: FnMut(u64, Option<u64>),
{
    let url = tiktok_mobile_redirect(client, &url).await?.unwrap_or(url);
    let resolved = resolve_media(extractor, &url).await?;
    tokio::time::timeout(Duration::from_secs(60 * 60), async {
        let mut direct_response = None;
        let resolved = match resolved {
            Err(ytdown::Error::UnsupportedUrl(_)) => {
                on_progress(0, None);
                let response = media_response(client, &url, HeaderMap::new()).await?;
                if redirected_webpage(&url, &response) {
                    let redirected = resolve_media(extractor, response.url()).await?;
                    if matches!(&redirected, Err(ytdown::Error::UnsupportedUrl(_))) {
                        direct_response = Some(response);
                        None
                    } else {
                        Some(redirected)
                    }
                } else {
                    direct_response = Some(response);
                    None
                }
            }
            result => Some(result),
        };
        let (target, headers, hint, expected) = match resolved {
            Some(Ok(MediaInfo::Single(info))) => {
                if info.is_live { return Err("Прямые эфиры не поддерживаются".into()); }
                let (format, target, headers) = select_format(&info.formats, limit)?;
                (target, headers, format_extension(format), format.filesize)
            }
            Some(Ok(MediaInfo::Collection(_))) => return Err("Плейлисты и каналы не поддерживаются; укажите отдельную запись".into()),
            Some(Err(error)) => return Err(extraction_error(error)),
            None => (url.clone(), HeaderMap::new(), extension_from_path(&url), None),
        };
        if direct_response.is_none() {
            on_progress(0, expected);
        }
        match direct_response {
            Some(response) => download_response(response, hint, expected, limit, temp_root, on_progress).await,
            None => download_media(client, &target, headers, hint, expected, limit, temp_root, on_progress).await,
        }
    }).await.map_err(|_| "Истекло время загрузки; файл не добавлен в очередь".to_string())?
}

fn media_client() -> Result<Client, String> {
    Client::builder()
        .use_native_tls()
        .connect_timeout(Duration::from_secs(20))
        .read_timeout(Duration::from_secs(45))
        .redirect(redirect_policy())
        .cookie_store(true)
        .referer(false)
        .build().map_err(|_| "Не удалось подготовить сетевой клиент".into())
}

async fn import_inner(app: &AppHandle, job_id: &str, url: Url) -> Result<(String, TempDir), String> {
    let client = media_client()?;
    let extractor = Ytdown::builder().client(client.clone()).build()
        .map_err(|_| "Не удалось подготовить загрузчик медиа")?;
    import_url(&client, &extractor, url, MAX_BYTES, &std::env::temp_dir(),
        |downloaded, total| progress(app, job_id, "downloading", downloaded, total),
    ).await
}

#[tauri::command]
pub async fn import_media(app: AppHandle, state: State<'_, MediaImportState>, job_id: String, url: String) -> Result<String, String> {
    let url = validate_url(&url)?;
    let (guard, mut cancelled) = begin_import(&state, &job_id)?;
    progress(&app, &job_id, "started", 0, None);
    let (path, directory) = tokio::select! {
        biased;
        _ = cancelled.changed() => return Err(CANCELLED.into()),
        result = import_inner(&app, &job_id, url) => result?,
    };
    // Commit and cancellation use the same lock: a successful cancellation can
    // never race with publishing a completed temporary file to the UI.
    let mut active = state.active.lock().map_err(|_| "Не удалось открыть список загрузок")?;
    let job = active.as_ref().filter(|job| job.id == job_id).ok_or(CANCELLED)?;
    if *job.cancel.borrow() { return Err(CANCELLED.into()); }
    let mut files = state.files.lock().map_err(|_| "Не удалось открыть список временных файлов")?;
    files.insert(path.clone(), directory);
    *active = None;
    drop(files);
    drop(active);
    drop(guard);
    Ok(path)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::sync::mpsc;
    use std::thread;

    fn serve(response: &'static [u8]) -> (Url, thread::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let worker = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            let mut request = [0; 2048];
            let _ = socket.read(&mut request);
            socket.write_all(response).unwrap();
        });
        (Url::parse(&format!("http://{address}/audio.mp3")).unwrap(), worker)
    }
    #[tokio::test]
    async fn client_keeps_ephemeral_site_session_for_redirects() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let worker = thread::spawn(move || {
            let (mut first, _) = listener.accept().unwrap();
            let mut request = [0; 2048];
            let _ = first.read(&mut request).unwrap();
            first.write_all(b"HTTP/1.1 302 Found\r\nLocation: /media\r\nSet-Cookie: session=temporary; Path=/\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").unwrap();
            let (mut second, _) = listener.accept().unwrap();
            let count = second.read(&mut request).unwrap();
            let received = String::from_utf8_lossy(&request[..count]).to_ascii_lowercase();
            second.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").unwrap();
            received
        });
        let client = media_client().unwrap();
        let response = client.get(format!("http://{address}/start")).send().await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert!(worker.join().unwrap().contains("cookie: session=temporary"));
    }

    #[tokio::test]
    async fn downloads_probeable_audio_and_removes_it_when_released() {
        const WAV: &[u8] = b"RIFF\x26\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80\x3e\x00\x00\x00\x7d\x00\x00\x02\x00\x10\x00data\x02\x00\x00\x00\x00\x00";
        const RESPONSE: &[u8] = b"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\nContent-Length: 46\r\nConnection: close\r\n\r\nRIFF\x26\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80\x3e\x00\x00\x00\x7d\x00\x00\x02\x00\x10\x00data\x02\x00\x00\x00\x00\x00";
        let root = tempfile::tempdir().unwrap();
        let (mut url, worker) = serve(RESPONSE);
        url.set_path("/get");
        let (path, directory) = download_media(&Client::new(), &url, HeaderMap::new(), None, None, 100, root.path(), |_, _| {}).await.unwrap();
        worker.join().unwrap();
        let (mut url2, worker2) = serve(RESPONSE);
        url2.set_path("/get");
        let (path2, directory2) = download_media(&Client::new(), &url2, HeaderMap::new(), None, None, 100, root.path(), |_, _| {}).await.unwrap();
        worker2.join().unwrap();
        assert_ne!(path, path2);
        assert_eq!(std::fs::read(&path).unwrap(), WAV);
        assert_eq!(std::fs::read(&path2).unwrap(), WAV);
        drop(directory);
        assert!(!Path::new(&path).exists());
        assert!(Path::new(&path2).exists());
        drop(directory2);
        assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 0);
    }


    #[tokio::test]
    async fn rejects_truncated_content_and_removes_partial_file() {
        let root = tempfile::tempdir().unwrap();
        let (url, worker) = serve(b"HTTP/1.1 200 OK\r\nContent-Type: audio/mpeg\r\nContent-Length: 20\r\nConnection: close\r\n\r\nabc");
        let result = download_media(&Client::new(), &url, HeaderMap::new(), None, None, 100, root.path(), |_, _| {}).await;
        worker.join().unwrap();
        assert!(result.is_err());
        assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 0);
    }

    #[tokio::test]
    async fn enforces_stream_limit_without_content_length_and_cleans_up() {
        let root = tempfile::tempdir().unwrap();
        let (url, worker) = serve(b"HTTP/1.1 200 OK\r\nContent-Type: audio/mpeg\r\nConnection: close\r\n\r\n123456");
        let error = download_media(&Client::new(), &url, HeaderMap::new(), None, None, 5, root.path(), |_, _| {}).await.err().unwrap();
        worker.join().unwrap();
        assert_eq!(error, TOO_LARGE);
        assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 0);
    }

    #[tokio::test]
    async fn cancellation_closes_and_removes_partial_file() {
        let root = tempfile::tempdir().unwrap();
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let (release, wait) = mpsc::channel();
        let worker = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            let mut request = [0; 2048];
            let _ = socket.read(&mut request);
            socket.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: audio/mpeg\r\nConnection: close\r\n\r\nabc").unwrap();
            let _ = wait.recv();
        });
        let url = Url::parse(&format!("http://{address}/audio.mp3")).unwrap();
        let client = Client::new();
        let (started, started_rx) = tokio::sync::oneshot::channel();
        let mut started = Some(started);
        let mut download = Box::pin(download_media(&client, &url, HeaderMap::new(), None, None, 100, root.path(), |_, _| {
            if let Some(started) = started.take() { let _ = started.send(()); }
        }));
        tokio::select! {
            _ = started_rx => {},
            result = &mut download => panic!("download unexpectedly finished: {:?}", result.err()),
        }
        // Dropping the pending operation represents the same cancellation as the command's select!.
        drop(download);
        release.send(()).unwrap();
        worker.join().unwrap();
        assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 0);
    }
    #[test]
    fn prefers_a_supported_audio_only_track_under_the_size_limit() {
        let mut audio = Format::default();
        audio.url = "https://example.test/audio".into();
        audio.container = Some(Container::M4a);
        audio.audio = Some(ytdown::AudioStream::default());
        audio.filesize = Some(10);
        let mut progressive = audio.clone();
        progressive.video = Some(ytdown::VideoStream::default());
        progressive.bitrate = Some(1_000_000);
        let mut oversized = audio.clone();
        oversized.filesize = Some(101);
        oversized.bitrate = Some(2_000_000);
        let formats = [progressive, oversized, audio];
        let (selected, _, _) = select_format(&formats, 100).unwrap();
        assert!(selected.video.is_none());
        assert_eq!(selected.filesize, Some(10));
    }

    #[test]
    fn skips_formats_with_invalid_headers_before_ranking() {
        let mut invalid = Format::default();
        invalid.url = "https://example.test/broken".into();
        invalid.container = Some(Container::M4a);
        invalid.audio = Some(ytdown::AudioStream::default());
        invalid.bitrate = Some(1_000_000);
        invalid.http_headers.push(("bad header".into(), "secret".into()));
        let mut usable = invalid.clone();
        usable.url = "https://example.test/usable".into();
        usable.bitrate = Some(10);
        usable.http_headers.clear();
        let formats = [invalid, usable];
        let (selected, _, _) = select_format(&formats, 100).unwrap();
        assert_eq!(selected.url, "https://example.test/usable");
    }

    #[tokio::test]
    async fn shortlink_forbidden_is_not_retried() {
        let (url, worker) = serve(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
        let address = url.socket_addrs(|| None).unwrap()[0];
        let client = Client::builder().no_proxy().redirect(redirect_policy()).resolve("vm.tiktok.com", address).build().unwrap();
        let extractor = Ytdown::builder().client(client.clone()).build().unwrap();
        let root = tempfile::tempdir().unwrap();
        let url = Url::parse(&format!("http://vm.tiktok.com:{}/code?token=secret", address.port())).unwrap();
        let error = import_url(&client, &extractor, url, 100, root.path(), |_, _| {}).await.err().unwrap();
        worker.join().unwrap();
        assert!(error.contains("403"));
        assert!(!error.contains("secret"));
        assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 0);
    }


    #[test]
    fn selects_ytdown_other_container_mime_types() {
        let mut audio = Format::default();
        audio.url = "https://example.test/audio".into();
        audio.container = Some(Container::Other("audio/mpeg".into()));
        audio.mime_type = Some("audio/mpeg".into());
        audio.audio = Some(ytdown::AudioStream::default());
        assert_eq!(format_extension(&audio), Some("mp3"));
        assert!(select_format(&[audio], 100).is_ok());

        let mut audio = Format::default();
        audio.container = Some(Container::Other("unknown".into()));
        audio.mime_type = Some("audio/ogg; codecs=opus".into());
        assert_eq!(format_extension(&audio), Some("ogg"));
    }

    #[test]
    fn protects_extracted_headers_and_redirects() {
        let mut format = Format::default();
        format.url = "http://example.test/audio".into();
        format.http_headers.push(("Cookie".into(), "token=secret".into()));
        assert!(safe_headers(&format, &validate_url(&format.url).unwrap()).is_err());
        format.url = "https://example.test/audio".into();
        let headers = safe_headers(&format, &validate_url(&format.url).unwrap()).unwrap();
        assert!(headers.get(COOKIE).unwrap().is_sensitive());
        format.http_headers.push(("cookie".into(), "other=value".into()));
        let headers = safe_headers(&format, &validate_url(&format.url).unwrap()).unwrap();
        let values: Vec<_> = headers.get_all(COOKIE).iter().collect();
        assert_eq!(values.len(), 2);
        assert!(values.iter().all(|value| value.is_sensitive()));
        assert_eq!(values[0], "token=secret");
        assert_eq!(values[1], "other=value");

        let secure = Url::parse("https://example.test/audio").unwrap();
        let same_origin = Url::parse("https://example.test/other").unwrap();
        let insecure = Url::parse("http://example.test/other").unwrap();
        let credentials = Url::parse("https://user:secret@example.test/other").unwrap();
        assert!(safe_redirect(&secure, &same_origin));
        assert!(!safe_redirect(&secure, &insecure));
        assert!(!safe_redirect(&secure, &credentials));
        assert!(safe_redirect(&insecure, &secure));
        let same_port_secure = Url::parse("https://example.test:8080/audio").unwrap();
        let same_port_insecure = Url::parse("http://example.test:8080/audio").unwrap();
        assert!(!safe_redirect(&same_port_secure, &same_port_insecure));
    }

    #[tokio::test]
    async fn credential_redirect_is_rejected_before_contacting_target() {
        let target = TcpListener::bind("127.0.0.1:0").unwrap();
        target.set_nonblocking(true).unwrap();
        let target_address = target.local_addr().unwrap();
        let source = TcpListener::bind("127.0.0.1:0").unwrap();
        let source_address = source.local_addr().unwrap();
        let worker = thread::spawn(move || {
            let (mut socket, _) = source.accept().unwrap();
            let mut request = [0; 2048];
            let _ = socket.read(&mut request);
            write!(socket, "HTTP/1.1 302 Found\r\nLocation: http://user:secret@{target_address}/audio\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").unwrap();
        });
        let client = Client::builder().no_proxy().redirect(redirect_policy()).build().unwrap();
        let url = Url::parse(&format!("http://{source_address}/audio")).unwrap();
        let error = media_response(&client, &url, HeaderMap::new()).await.err().unwrap();
        worker.join().unwrap();
        assert!(!error.contains("secret"));
        assert_eq!(target.accept().unwrap_err().kind(), std::io::ErrorKind::WouldBlock);
    }

    #[tokio::test]
    async fn imports_a_redirected_page_using_the_native_extractor() {
        const WAV: &[u8] = b"RIFF\x26\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80\x3e\x00\x00\x00\x7d\x00\x00\x02\x00\x10\x00data\x02\x00\x00\x00\x00\x00";
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let worker = thread::spawn(move || {
            for step in 0..4 {
                let (mut socket, _) = listener.accept().unwrap();
                socket.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
                let mut request = [0; 4096];
                let n = socket.read(&mut request).unwrap();
                let request = String::from_utf8_lossy(&request[..n]);
                match step {
                    0 => {
                        assert!(request.starts_with("GET /shortcut "));
                        write!(socket, "HTTP/1.1 302 Found\r\nLocation: http://www.tiktok.com:{}/@user/video/123\r\nContent-Length: 0\r\nConnection: close\r\n\r\n", address.port()).unwrap();
                    }
                    1 => {
                        assert!(request.starts_with("GET /@user/video/123 "));
                        socket.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").unwrap();
                    }
                    2 => {
                        assert!(request.starts_with("GET /@user/video/123 "));
                        let body = format!(r#"<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">{{"__DEFAULT_SCOPE__":{{"webapp.video-detail":{{"statusCode":0,"itemInfo":{{"itemStruct":{{"id":"123","author":{{}},"video":{{"playAddr":"http://{address}/audio.wav"}}}}}}}}}}}}</script>"#);
                        write!(socket, "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", body.len(), body).unwrap();
                    }
                    _ => {
                        assert!(request.starts_with("GET /audio.wav "));
                        write!(socket, "HTTP/1.1 200 OK\r\nContent-Type: audio/wav\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", WAV.len()).unwrap();
                        socket.write_all(WAV).unwrap();
                    }
                }
            }
        });
        let client = Client::builder().no_proxy().redirect(redirect_policy())
            .resolve("www.tiktok.com", address).build().unwrap();
        let extractor = Ytdown::builder().client(client.clone()).clear_extractors()
            .extractor(Box::new(ytdown::extractor::tiktok::TiktokExtractor::with_base_url(format!("http://{address}"))))
            .build().unwrap();
        let root = tempfile::tempdir().unwrap();
        let url = Url::parse(&format!("http://{address}/shortcut")).unwrap();
        let (path, directory) = import_url(&client, &extractor, url, 100, root.path(), |_, _| {}).await.unwrap();
        worker.join().unwrap();
        assert_eq!(std::fs::read(&path).unwrap(), WAV);
        drop(directory);
        assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 0);
    }

    #[test]
    fn normalizes_only_well_formed_youtube_live_video_links() {
        for host in ["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"] {
            let url = Url::parse(&format!("https://{host}/live/dQw4w9WgXcQ?si=secret&list=other#private")).unwrap();
            let normalized = youtube_live_url(&url).unwrap();
            assert_eq!(normalized.path(), "/watch");
            assert_eq!(normalized.query(), Some("v=dQw4w9WgXcQ"));
            assert!(normalized.fragment().is_none());
        }
        for url in [
            "https://youtube.com.evil.test/live/dQw4w9WgXcQ",
            "https://www.youtube.com/live/not-an-id",
            "https://www.youtube.com/live/dQw4w9WgXcQ/extra",
            "https://www.youtube.com/@user/live",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        ] {
            assert!(youtube_live_url(&Url::parse(url).unwrap()).is_none(), "{url}");
        }
    }

    #[tokio::test]
    async fn imports_youtube_live_replays_but_still_rejects_live_broadcasts() {
        const WAV: &[u8] = b"RIFF\x26\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80\x3e\x00\x00\x00\x7d\x00\x00\x02\x00\x10\x00data\x02\x00\x00\x00\x00\x00";
        for is_live in [false, true] {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let address = listener.local_addr().unwrap();
            let worker = thread::spawn(move || {
                for step in 0..if is_live { 3 } else { 4 } {
                    let (mut socket, _) = listener.accept().unwrap();
                    socket.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
                    let mut request = [0; 4096];
                    let n = socket.read(&mut request).unwrap();
                    let request = String::from_utf8_lossy(&request[..n]);
                    match step {
                        0 => {
                            assert!(request.starts_with("GET /iframe_api "));
                            socket.write_all(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").unwrap();
                        }
                        1 => {
                            assert!(request.starts_with("POST /youtubei/v1/visitor_id?"));
                            socket.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}").unwrap();
                        }
                        2 => {
                            assert!(request.starts_with("POST /youtubei/v1/player?"));
                            let body = serde_json::json!({
                                "playabilityStatus": {"status": "OK"},
                                "videoDetails": {"videoId": "dQw4w9WgXcQ", "title": "recording", "isLive": is_live},
                                "streamingData": {"adaptiveFormats": [{
                                    "itag": 140, "url": format!("http://{address}/audio.wav"), "mimeType": "audio/mp4"
                                }]}
                            }).to_string();
                            write!(socket, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", body.len(), body).unwrap();
                        }
                        _ => {
                            assert!(request.starts_with("GET /audio.wav "));
                            write!(socket, "HTTP/1.1 200 OK\r\nContent-Type: audio/wav\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", WAV.len()).unwrap();
                            socket.write_all(WAV).unwrap();
                        }
                    }
                }
            });
            let client = Client::builder().no_proxy().redirect(redirect_policy()).build().unwrap();
            let extractor = Ytdown::builder().client(client.clone()).clear_extractors()
                .extractor(Box::new(ytdown::extractor::youtube::YoutubeExtractor::with_base_url(format!("http://{address}"))))
                .build().unwrap();
            let root = tempfile::tempdir().unwrap();
            let url = Url::parse("https://www.youtube.com/live/dQw4w9WgXcQ?si=secret").unwrap();
            let result = import_url(&client, &extractor, url, 100, root.path(), |_, _| {}).await;
            worker.join().unwrap();
            if is_live {
                assert_eq!(result.err().unwrap(), "Прямые эфиры не поддерживаются");
            } else {
                let (path, directory) = result.unwrap();
                assert_eq!(std::fs::read(&path).unwrap(), WAV);
                drop(directory);
            }
            assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 0);
        }
    }

    #[tokio::test]
    async fn imports_tiktok_mobile_shortlinks_from_the_original_host_and_path() {
        const WAV: &[u8] = b"RIFF\x26\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80\x3e\x00\x00\x00\x7d\x00\x00\x02\x00\x10\x00data\x02\x00\x00\x00\x00\x00";
        for host in ["vm.tiktok.com", "vt.tiktok.com"] {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let address = listener.local_addr().unwrap();
            let worker = thread::spawn(move || {
                for step in 0..4 {
                    let (mut socket, _) = listener.accept().unwrap();
                    socket.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
                    let mut request = [0; 4096];
                    let n = socket.read(&mut request).unwrap();
                    let request = String::from_utf8_lossy(&request[..n]);
                    match step {
                        0 => {
                            assert!(request.starts_with("GET /Zabc123?token=secret "));
                            assert!(request.to_ascii_lowercase().contains(&format!("host: {host}:{}", address.port())));
                            assert!(request.to_ascii_lowercase().contains("user-agent: mozilla/"));
                            write!(socket, "HTTP/1.1 302 Found\r\nLocation: http://www.tiktok.com:{}/@user/video/123\r\nContent-Length: 0\r\nConnection: close\r\n\r\n", address.port()).unwrap();
                        }
                        1 => {
                            assert!(request.starts_with("GET /@user/video/123 "));
                            socket.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").unwrap();
                        }
                        2 => {
                            assert!(request.starts_with("GET /@user/video/123 "));
                            let body = format!(r#"<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">{{"__DEFAULT_SCOPE__":{{"webapp.video-detail":{{"statusCode":0,"itemInfo":{{"itemStruct":{{"id":"123","author":{{}},"video":{{"playAddr":"http://{address}/audio.wav"}}}}}}}}}}}}</script>"#);
                            write!(socket, "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", body.len(), body).unwrap();
                        }
                        _ => {
                            assert!(request.starts_with("GET /audio.wav "));
                            write!(socket, "HTTP/1.1 200 OK\r\nContent-Type: audio/wav\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", WAV.len()).unwrap();
                            socket.write_all(WAV).unwrap();
                        }
                    }
                }
            });
            let client = Client::builder().no_proxy().redirect(redirect_policy())
                .resolve(host, address).resolve("www.tiktok.com", address).build().unwrap();
            let extractor = Ytdown::builder().client(client.clone()).clear_extractors()
                .extractor(Box::new(ytdown::extractor::tiktok::TiktokExtractor::with_base_url(format!("http://{address}"))))
                .build().unwrap();
            let root = tempfile::tempdir().unwrap();
            let url = Url::parse(&format!("http://{host}:{}/Zabc123?token=secret", address.port())).unwrap();
            let result = import_url(&client, &extractor, url, 100, root.path(), |_, _| {}).await;
            worker.join().unwrap();
            let (path, directory) = result.unwrap();
            assert_eq!(std::fs::read(&path).unwrap(), WAV);
            drop(directory);
            assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 0);
        }
    }

    #[tokio::test]
    async fn reports_http_failures_without_leaking_the_url() {
        for (response, expected) in [
            (&b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"[..], "доступ"),
            (&b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"[..], "не найдено"),
            (&b"HTTP/1.1 429 Too Many Requests\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"[..], "ограничил"),
            (&b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"[..], "Ошибка сервера"),
            (&b"HTTP/1.1 206 Partial Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"[..], "частичный"),
            (&b"HTTP/1.1 403 Forbidden\r\nContent-Range: bytes 0-1/3\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"[..], "доступ"),
        ] {
            let (mut url, worker) = serve(response);
            url.set_query(Some("token=secret"));
            let error = media_response(&Client::new(), &url, HeaderMap::new()).await.err().unwrap();
            worker.join().unwrap();
            assert!(error.contains(expected), "{error}");
            assert!(!error.contains("secret"));
        }
    }

    #[test]
    fn reports_tiktok_slideshows_without_exposing_extractor_messages() {
        let error = extraction_error(ytdown::Error::Extraction {
            stage: "tiktok",
            message: "photo/slideshow posts are not supported".into(),
        });
        assert!(error.contains("слайд-шоу"));
        let error = extraction_error(ytdown::Error::Extraction {
            stage: "tiktok",
            message: "token=secret".into(),
        });
        assert!(!error.contains("secret"));
    }

    #[test]
    fn reports_known_platform_restrictions_without_exposing_response_text() {
        for (stage, message, expected) in [
            ("tiktok", "webpage request failed with HTTP 403", "TikTok запретил"),
            ("tiktok", "webpage request failed with HTTP 429", "TikTok ограничил"),
            ("twitter", "syndication request failed with HTTP 403", "X запретил"),
            ("twitter", "syndication request failed with HTTP 429", "X ограничил"),
        ] {
            let error = extraction_error(ytdown::Error::Extraction { stage, message: message.into() });
            assert!(error.contains(expected));
        }
        let error = extraction_error(ytdown::Error::Extraction {
            stage: "twitter",
            message: "syndication request failed with HTTP 403; token=secret".into(),
        });
        assert!(!error.contains("secret"));
    }

    #[tokio::test]
    async fn cancelling_during_a_tiktok_shortlink_request_leaves_no_files() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let (seen, seen_rx) = tokio::sync::oneshot::channel();
        let (release, wait) = mpsc::channel();
        let worker = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            let mut request = [0; 2048];
            let _ = socket.read(&mut request);
            let _ = seen.send(());
            let _ = wait.recv();
        });
        let client = Client::builder().no_proxy().redirect(redirect_policy())
            .resolve("vm.tiktok.com", address).build().unwrap();
        let extractor = Ytdown::builder().client(client.clone()).build().unwrap();
        let root = tempfile::tempdir().unwrap();
        let url = Url::parse(&format!("http://vm.tiktok.com:{}/Zabc123", address.port())).unwrap();
        let mut import = Box::pin(import_url(&client, &extractor, url, 100, root.path(), |_, _| {}));
        tokio::time::timeout(Duration::from_secs(3), async {
            tokio::select! {
                _ = seen_rx => {},
                result = &mut import => panic!("import unexpectedly finished: {:?}", result.err()),
            }
        }).await.expect("shortlink request did not start");
        drop(import);
        release.send(()).unwrap();
        worker.join().unwrap();
        assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 0);
    }

    #[test]
    fn rejects_credentials_and_unsupported_schemes_without_echoing_them() {
        for value in ["https://name:secret@example.org/audio.mp3", "file:///tmp/private.mp3"] {
            let error = validate_url(value).unwrap_err();
            assert!(!error.contains("secret"));
            assert!(!error.contains(value));
        }
    }
}

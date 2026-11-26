use std::collections::HashMap;
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use reqwest::header::{HeaderMap, HeaderName, HeaderValue, CONTENT_RANGE, CONTENT_TYPE};
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
    match format.container.as_ref() {
        Some(Container::Mp4) => Some("mp4"),
        Some(Container::M4a) => Some("m4a"),
        Some(Container::WebM | Container::Weba) => Some("webm"),
        Some(Container::Other(ext)) => extension_from_name(ext),
        _ => format.mime_type.as_deref().and_then(extension_from_mime),
    }
}

fn select_format(formats: &[Format], limit: u64) -> Result<&Format, String> {
    let supported = |format: &&Format| {
        format.audio.is_some()
            && format_extension(format).is_some()
            && validate_url(&format.url).is_ok()
    };
    let mut candidates = formats.iter().filter(supported).peekable();
    if candidates.peek().is_none() {
        return Err("На странице нет поддерживаемой аудиодорожки; трансляции и защищённое медиа не поддерживаются".into());
    }
    candidates
        .filter(|format| format.filesize.is_none_or(|size| size <= limit))
        .max_by_key(|format| (format.video.is_none(), format.audio.as_ref().and_then(|audio| audio.bitrate).or(format.bitrate).unwrap_or(0)))
        .ok_or_else(|| TOO_LARGE.into())
}

fn extraction_error(error: ytdown::Error) -> String {
    use ytdown::error::UnavailableReason;
    match error {
        ytdown::Error::Unavailable { reason, .. } => match reason {
            UnavailableReason::Gone => "Площадка не предоставила запись: она может быть недоступна либо загрузчик не смог её извлечь",
            UnavailableReason::AgeRestricted => "Запись ограничена по возрасту; авторизация не поддерживается",
            UnavailableReason::BotCheck => "Площадка запросила проверку или авторизацию; повторите позже",
            UnavailableReason::GeoBlocked => "Запись недоступна в вашем регионе",
            UnavailableReason::Live => "Прямые эфиры не поддерживаются",
            _ => "Площадка сообщает, что запись недоступна",
        }.into(),
        ytdown::Error::Network { .. } => "Не удалось связаться с площадкой; проверьте подключение".into(),
        ytdown::Error::Cipher(_) => "Не удалось обработать формат ссылки площадки".into(),
        _ => "Не удалось извлечь медиа с этой страницы; площадка могла изменить формат или ограничить доступ".into(),
    }
}

fn safe_headers(format: &Format) -> Result<HeaderMap, String> {
    let mut headers = HeaderMap::new();
    for (name, value) in &format.http_headers {
        let name = HeaderName::from_bytes(name.as_bytes()).map_err(|_| "Площадка вернула недопустимые параметры загрузки")?;
        let value = HeaderValue::from_str(value).map_err(|_| "Площадка вернула недопустимые параметры загрузки")?;
        headers.insert(name, value);
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

async fn download_media<F>(
    client: &Client,
    url: &Url,
    headers: HeaderMap,
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
    let mut response = client.get(url.clone()).headers(headers).send().await.map_err(|error| {
        if error.is_timeout() { "Истекло время ожидания загрузки" } else { "Не удалось загрузить медиа; проверьте подключение" }
    })?;
    if response.status() != StatusCode::OK || response.headers().contains_key(CONTENT_RANGE) {
        return Err(format!("Сервер отклонил загрузку (HTTP {}); частичные ответы не поддерживаются", response.status().as_u16()));
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

async fn import_inner(app: &AppHandle, job_id: &str, url: Url) -> Result<(String, TempDir), String> {
    let client = Client::builder()
        .use_native_tls()
        .connect_timeout(Duration::from_secs(20))
        .read_timeout(Duration::from_secs(45))
        .redirect(reqwest::redirect::Policy::limited(10))
        .referer(false)
        .build().map_err(|_| "Не удалось подготовить сетевой клиент")?;
    let extractor = Ytdown::builder().client(client.clone()).build()
        .map_err(|_| "Не удалось подготовить загрузчик медиа")?;
    let resolved = tokio::time::timeout(Duration::from_secs(120), extractor.resolve(url.as_str())).await
        .map_err(|_| "Истекло время ожидания ответа площадки")?;
    let (target, headers, hint, expected) = match resolved {
        Ok(MediaInfo::Single(info)) => {
            if info.is_live { return Err("Прямые эфиры не поддерживаются".into()); }
            let format = select_format(&info.formats, MAX_BYTES)?;
            (validate_url(&format.url)?, safe_headers(format)?, format_extension(format), format.filesize)
        }
        Ok(MediaInfo::Collection(_)) => return Err("Плейлисты и каналы не поддерживаются; укажите отдельную запись".into()),
        Err(ytdown::Error::UnsupportedUrl(_)) => (url.clone(), HeaderMap::new(), extension_from_path(&url), None),
        Err(error) => return Err(extraction_error(error)),
    };
    progress(app, job_id, "downloading", 0, expected);
    tokio::time::timeout(Duration::from_secs(60 * 60), download_media(
        &client, &target, headers, hint, expected, MAX_BYTES, &std::env::temp_dir(),
        |downloaded, total| progress(app, job_id, "downloading", downloaded, total),
    )).await.map_err(|_| "Истекло время загрузки; файл не добавлен в очередь".to_string())?
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
        let selected = select_format(&formats, 100).unwrap();
        assert!(selected.video.is_none());
        assert_eq!(selected.filesize, Some(10));
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

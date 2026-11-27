//! Provision the tract-compatible powerset model in the application's model cache.
//!
//! The cache directory must be controlled by the application user. Downloads are
//! never exposed under the model name until their exact size and SHA-256 match the
//! pinned upstream artifact. Independent processes may download concurrently;
//! each owns its temporary file and can publish only the same verified bytes.
//! Errors and cancellation explicitly remove the partial file; dropping a
//! pending future attempts the same cleanup. An unclean process termination or
//! a filesystem cleanup failure can leave an orphaned temporary file.

use std::fmt;
use std::fs::{self, File};
use std::future::Future;
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use reqwest::{header, redirect::Policy, Client, StatusCode};
use sha2::{Digest, Sha256};
use tempfile::NamedTempFile;

const MODEL_NAME: &str = "powerset_fp32_tract.onnx";
const TEMP_PREFIX: &str = ".segmentation-";
const CANCEL_INTERVAL: Duration = Duration::from_millis(100);

// Polyvoice 1.0.0's models.powerset_fp32_tract manifest entry:
// https://raw.githubusercontent.com/ekhodzitsky/polyvoice/v1.0.0/src/models/manifest.toml
const OFFICIAL_MODEL: ModelSpec<'static> = ModelSpec {
    url: "https://github.com/ekhodzitsky/polyvoice/releases/download/models-tract-v1/powerset_fp32_tract.onnx",
    size: 5_985_883,
    sha256: [
        0xd5, 0x33, 0x5d, 0xe3, 0x34, 0xa2, 0x44, 0x70, 0xb1, 0xf0, 0x32, 0x6f, 0x72, 0xe5,
        0x13, 0xc5, 0x9e, 0x15, 0x63, 0x08, 0xa7, 0x8b, 0xa3, 0xe1, 0x62, 0x68, 0x51, 0xf5,
        0x70, 0x69, 0x6c, 0xe4,
    ],
    #[cfg(test)]
    partial_written: None,
};

#[derive(Clone, Copy)]
struct ModelSpec<'a> {
    url: &'a str,
    size: u64,
    sha256: [u8; 32],
    // Signal only after bytes reach the open file, without relying on directory metadata.
    #[cfg(test)]
    partial_written: Option<&'a tokio::sync::Notify>,
}

#[derive(Debug)]
enum InstallError {
    Cancelled,
    Cleanup(String),
    Failed(String),
}

impl fmt::Display for InstallError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Cancelled => write!(f, "Задание отменено"),
            Self::Cleanup(message) | Self::Failed(message) => f.write_str(message),
        }
    }
}

fn check_cancelled(cancelled: &AtomicBool) -> Result<(), InstallError> {
    if cancelled.load(Ordering::Relaxed) {
        Err(InstallError::Cancelled)
    } else {
        Ok(())
    }
}

/// Return the verified model path, downloading the official artifact if needed.
/// A valid cache works without network access. The caller must use an
/// application-controlled directory and must not substitute a different file
/// after this function returns.
pub(crate) async fn ensure_segmentation_model(
    model_dir: &Path,
    cancelled: &AtomicBool,
) -> Result<PathBuf, String> {
    ensure_model(model_dir, cancelled, OFFICIAL_MODEL, official_client)
        .await
        .map_err(|error| error.to_string())
}

fn official_client() -> Result<Client, InstallError> {
    let policy = Policy::custom(|attempt| {
        let url = attempt.url();
        let allowed = attempt.previous().len() < 5
            && url.scheme() == "https"
            && url.username().is_empty()
            && url.password().is_none()
            && url.port().map_or(true, |port| port == 443)
            && matches!(url.host_str(), Some("github.com" | "release-assets.githubusercontent.com"));
        if allowed {
            attempt.follow()
        } else {
            attempt.error("untrusted model download redirect")
        }
    });
    Client::builder()
        .https_only(true)
        .redirect(policy)
        .connect_timeout(Duration::from_secs(20))
        .timeout(Duration::from_secs(600))
        .build()
        .map_err(|_| InstallError::Failed("Не удалось создать клиент загрузки модели сегментации".into()))
}

async fn ensure_model<F>(
    model_dir: &Path,
    cancelled: &AtomicBool,
    spec: ModelSpec<'_>,
    make_client: F,
) -> Result<PathBuf, InstallError>
where
    F: FnOnce() -> Result<Client, InstallError>,
{
    let destination = model_dir.join(MODEL_NAME);
    if cached_model_is_valid(&destination, spec, cancelled)? {
        return Ok(destination);
    }
    check_cancelled(cancelled)?;
    fs::create_dir_all(model_dir).map_err(|error| {
        InstallError::Failed(format!("Не удалось создать каталог модели сегментации: {error}"))
    })?;
    // Another instance may have completed installation while the directory was created.
    if cached_model_is_valid(&destination, spec, cancelled)? {
        return Ok(destination);
    }
    let client = make_client()?;
    let result = download_and_install(&client, model_dir, &destination, spec, cancelled).await;
    match result {
        Ok(()) => {
            check_cancelled(cancelled)?;
            Ok(destination)
        }
        Err(InstallError::Cancelled) => Err(InstallError::Cancelled),
        Err(error @ InstallError::Cleanup(_)) => Err(error),
        Err(error) => {
            // A concurrent process may have published a complete model even if
            // this download failed. Never treat its mere existence as success.
            match cached_model_is_valid(&destination, spec, cancelled) {
                Ok(true) => Ok(destination),
                Ok(false) => Err(error),
                Err(InstallError::Cancelled) => Err(InstallError::Cancelled),
                Err(cache_error) => Err(InstallError::Failed(format!("{error}; {cache_error}"))),
            }
        }
    }
}

fn cached_model_is_valid(
    path: &Path,
    spec: ModelSpec<'_>,
    cancelled: &AtomicBool,
) -> Result<bool, InstallError> {
    check_cancelled(cancelled)?;
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(false),
        Err(error) => {
            return Err(InstallError::Failed(format!("Не удалось проверить кэш модели сегментации: {error}")));
        }
    };
    if !metadata.file_type().is_file() {
        return Err(InstallError::Failed("Путь модели сегментации занят не обычным файлом".into()));
    }
    if metadata.len() != spec.size {
        return Ok(false);
    }
    let mut file = File::open(path).map_err(|error| {
        InstallError::Failed(format!("Не удалось открыть кэш модели сегментации: {error}"))
    })?;
    let opened = file.metadata().map_err(|error| {
        InstallError::Failed(format!("Не удалось проверить файл модели сегментации: {error}"))
    })?;
    if !opened.is_file() || opened.len() != spec.size {
        return Ok(false);
    }
    verify_contents(&mut file, spec, cancelled)
}

fn verify_contents(
    reader: &mut impl Read,
    spec: ModelSpec<'_>,
    cancelled: &AtomicBool,
) -> Result<bool, InstallError> {
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    let mut size = 0u64;
    loop {
        check_cancelled(cancelled)?;
        let count = reader.read(&mut buffer).map_err(|error| {
            InstallError::Failed(format!("Не удалось прочитать модель сегментации: {error}"))
        })?;
        if count == 0 {
            break;
        }
        size = size.saturating_add(count as u64);
        if size > spec.size {
            return Ok(false);
        }
        hasher.update(&buffer[..count]);
    }
    check_cancelled(cancelled)?;
    Ok(size == spec.size && hasher.finalize()[..] == spec.sha256[..])
}

// Dropping this future also drops the request and any owned temporary file.
async fn cancellable<F: Future>(future: F, cancelled: &AtomicBool) -> Result<F::Output, InstallError> {
    tokio::pin!(future);
    loop {
        check_cancelled(cancelled)?;
        tokio::select! {
            output = &mut future => {
                check_cancelled(cancelled)?;
                return Ok(output);
            }
            _ = tokio::time::sleep(CANCEL_INTERVAL) => {}
        }
    }
}

async fn download_and_install(
    client: &Client,
    model_dir: &Path,
    destination: &Path,
    spec: ModelSpec<'_>,
    cancelled: &AtomicBool,
) -> Result<(), InstallError> {
    let request = client.get(spec.url).header(header::ACCEPT_ENCODING, "identity").send();
    let mut response = cancellable(request, cancelled).await?
        .map_err(|_| InstallError::Failed("Не удалось загрузить модель сегментации".into()))?;
    if response.status() != StatusCode::OK {
        return Err(InstallError::Failed(format!(
            "Сервер модели сегментации вернул HTTP {}", response.status().as_u16()
        )));
    }
    if response.content_length().is_some_and(|length| length != spec.size) {
        return Err(InstallError::Failed("Неверный размер ответа модели сегментации".into()));
    }
    check_cancelled(cancelled)?;
    let mut temporary = tempfile::Builder::new()
        .prefix(TEMP_PREFIX)
        .suffix(".part")
        .rand_bytes(16)
        .tempfile_in(model_dir)
        .map_err(|error| InstallError::Failed(format!("Не удалось создать временный файл модели: {error}")))?;
    if let Err(error) = receive_and_verify(&mut response, &mut temporary, spec, cancelled).await {
        return Err(close_failed_download(temporary, error));
    }
    if let Err(error) = check_cancelled(cancelled) {
        return Err(close_failed_download(temporary, error));
    }
    match temporary.persist(destination) {
        Ok(_) => Ok(()),
        Err(error) => {
            let reason = InstallError::Failed(format!("Не удалось сохранить модель сегментации: {}", error.error));
            Err(close_failed_download(error.file, reason))
        }
    }
}

async fn receive_and_verify(
    response: &mut reqwest::Response,
    temporary: &mut NamedTempFile,
    spec: ModelSpec<'_>,
    cancelled: &AtomicBool,
) -> Result<(), InstallError> {
    let mut size = 0u64;
    loop {
        let chunk = cancellable(response.chunk(), cancelled).await?
            .map_err(|_| InstallError::Failed("Не удалось прочитать загрузку модели сегментации".into()))?;
        let Some(chunk) = chunk else { break };
        if (chunk.len() as u64) > spec.size.saturating_sub(size) {
            return Err(InstallError::Failed("Загрузка модели сегментации превышает ожидаемый размер".into()));
        }
        for block in chunk.chunks(64 * 1024) {
            check_cancelled(cancelled)?;
            temporary.write_all(block).map_err(|error| {
                InstallError::Failed(format!("Не удалось записать модель сегментации: {error}"))
            })?;
            size += block.len() as u64;
            #[cfg(test)]
            if let Some(partial_written) = spec.partial_written {
                partial_written.notify_one();
            }
        }
    }
    if size != spec.size {
        return Err(InstallError::Failed("Загрузка модели сегментации неполная".into()));
    }
    temporary.flush().and_then(|_| temporary.seek(SeekFrom::Start(0))).map_err(|error| {
        InstallError::Failed(format!("Не удалось проверить загруженную модель сегментации: {error}"))
    })?;
    if !verify_contents(temporary, spec, cancelled)? {
        return Err(InstallError::Failed("SHA-256 загруженной модели сегментации не совпадает".into()));
    }
    check_cancelled(cancelled)?;
    temporary.as_file().sync_all().map_err(|error| {
        InstallError::Failed(format!("Не удалось синхронизировать модель сегментации: {error}"))
    })?;
    Ok(())
}

fn close_failed_download(temporary: NamedTempFile, error: InstallError) -> InstallError {
    match temporary.close() {
        Ok(()) => error,
        Err(cleanup) => InstallError::Cleanup(format!("{error}; не удалось удалить временный файл модели: {cleanup}")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::{TcpListener, TcpStream};
    use std::sync::mpsc;
    use std::thread::{self, JoinHandle};
    use std::time::Instant;

    const FIXTURE: &[u8] = b"abc";
    // SHA-256 of the fixture, kept independently of the code under test.
    const FIXTURE_SHA256: [u8; 32] = [
        0xba, 0x78, 0x16, 0xbf, 0x8f, 0x01, 0xcf, 0xea, 0x41, 0x41, 0x40, 0xde, 0x5d, 0xae,
        0x22, 0x23, 0xb0, 0x03, 0x61, 0xa3, 0x96, 0x17, 0x7a, 0x9c, 0xb4, 0x10, 0xff, 0x61,
        0xf2, 0x00, 0x15, 0xad,
    ];

    fn fixture_spec(url: &str) -> ModelSpec<'_> {
        ModelSpec {
            url,
            size: FIXTURE.len() as u64,
            sha256: FIXTURE_SHA256,
            partial_written: None,
        }
    }

    fn test_client() -> Result<Client, InstallError> {
        Client::builder().no_proxy().timeout(Duration::from_secs(5)).build()
            .map_err(|error| InstallError::Failed(error.to_string()))
    }

    fn response(body: &[u8], length: usize) -> Vec<u8> {
        let mut bytes = format!("HTTP/1.1 200 OK\r\nContent-Length: {length}\r\nConnection: close\r\n\r\n").into_bytes();
        bytes.extend_from_slice(body);
        bytes
    }

    // Accept every connection before responding, so the concurrency case really
    // has two downloads in flight rather than the second one finding the cache.
    fn server(replies: Vec<Vec<u8>>) -> (String, JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let url = format!("http://{}/model", listener.local_addr().unwrap());
        let thread = thread::spawn(move || {
            let mut streams: Vec<TcpStream> = Vec::new();
            let deadline = Instant::now() + Duration::from_secs(3);
            while streams.len() < replies.len() {
                match listener.accept() {
                    Ok((stream, _)) => {
                        stream.set_nonblocking(false).unwrap();
                        stream.set_read_timeout(Some(Duration::from_secs(3))).unwrap();
                        streams.push(stream);
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock && Instant::now() < deadline => {
                        thread::sleep(Duration::from_millis(5));
                    }
                    Err(error) => panic!("server did not accept all requests: {error}"),
                }
            }
            for (mut stream, reply) in streams.into_iter().zip(replies) {
                let mut request = [0u8; 4096];
                stream.read(&mut request).unwrap();
                stream.write_all(&reply).unwrap();
            }
        });
        (url, thread)
    }

    fn stalled_server() -> (String, mpsc::Sender<()>, JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}/model", listener.local_addr().unwrap());
        let (release, wait_for_release) = mpsc::channel();
        let worker = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream.set_read_timeout(Some(Duration::from_secs(3))).unwrap();
            let mut request = [0u8; 4096];
            stream.read(&mut request).unwrap();
            stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\na").unwrap();
            wait_for_release.recv().expect("test did not release stalled response");
            drop(stream);
        });
        (url, release, worker)
    }

    fn assert_only_model(dir: &Path) {
        let entries: Vec<_> = fs::read_dir(dir).unwrap().map(|entry| entry.unwrap().file_name()).collect();
        assert_eq!(entries, vec![std::ffi::OsString::from(MODEL_NAME)]);
    }

    fn assert_partial_file(dir: &Path) {
        let mut entries = fs::read_dir(dir).unwrap();
        let entry = entries.next().expect("partial file was not created").unwrap();
        assert!(entry.file_name().to_string_lossy().starts_with(TEMP_PREFIX));
        // Read through a file handle: Windows may keep directory-entry sizes stale
        // until the writer closes its handle.
        assert_eq!(fs::read(entry.path()).unwrap(), b"a");
        assert!(entries.next().is_none(), "unexpected extra file");
    }

    #[tokio::test]
    async fn verified_cache_is_usable_offline_without_constructing_a_client() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join(MODEL_NAME);
        fs::write(&path, FIXTURE).unwrap();
        let cancelled = AtomicBool::new(false);
        let result = ensure_model(dir.path(), &cancelled, fixture_spec("http://127.0.0.1:1/model"), || {
            panic!("a verified cache must not construct a client")
        }).await.unwrap();
        assert_eq!(result, path);
        assert_eq!(fs::read(result).unwrap(), FIXTURE);
        assert_only_model(dir.path());
    }

    #[tokio::test]
    async fn downloads_verifies_and_atomically_installs() {
        let dir = tempfile::tempdir().unwrap();
        let (url, worker) = server(vec![response(FIXTURE, FIXTURE.len())]);
        let path = ensure_model(dir.path(), &AtomicBool::new(false), fixture_spec(&url), test_client).await.unwrap();
        worker.join().unwrap();
        assert_eq!(path, dir.path().join(MODEL_NAME));
        assert_eq!(fs::read(path).unwrap(), FIXTURE);
        assert_only_model(dir.path());
    }

    #[tokio::test]
    async fn corrupt_cached_file_is_not_accepted_offline_or_destroyed() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join(MODEL_NAME);
        fs::write(&path, b"bad").unwrap();
        let result = ensure_model(dir.path(), &AtomicBool::new(false), fixture_spec("http://127.0.0.1:1/model"), test_client).await;
        assert!(result.is_err());
        assert_eq!(fs::read(path).unwrap(), b"bad");
        assert_only_model(dir.path());
    }
    #[tokio::test]
    async fn corrupt_cached_file_is_replaced_only_by_a_verified_download() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join(MODEL_NAME);
        fs::write(&path, b"bad").unwrap();
        let (url, worker) = server(vec![response(FIXTURE, FIXTURE.len())]);
        let installed = ensure_model(dir.path(), &AtomicBool::new(false), fixture_spec(&url), test_client).await.unwrap();
        worker.join().unwrap();
        assert_eq!(installed, path);
        assert_eq!(fs::read(path).unwrap(), FIXTURE);
        assert_only_model(dir.path());
    }


    #[tokio::test]
    async fn corrupt_download_is_rejected_and_partial_file_is_removed() {
        let dir = tempfile::tempdir().unwrap();
        let (url, worker) = server(vec![response(b"bad", FIXTURE.len())]);
        let result = ensure_model(dir.path(), &AtomicBool::new(false), fixture_spec(&url), test_client).await;
        worker.join().unwrap();
        assert!(result.unwrap_err().to_string().contains("SHA-256"));
        assert_eq!(fs::read_dir(dir.path()).unwrap().count(), 0);
    }

    #[tokio::test]
    async fn interrupted_download_preserves_existing_cache_and_removes_partial_file() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join(MODEL_NAME);
        fs::write(&path, b"bad").unwrap();
        let (url, worker) = server(vec![response(b"a", FIXTURE.len())]);
        let result = ensure_model(dir.path(), &AtomicBool::new(false), fixture_spec(&url), test_client).await;
        worker.join().unwrap();
        assert!(result.is_err());
        assert_eq!(fs::read(path).unwrap(), b"bad");
        assert_only_model(dir.path());
    }

    #[tokio::test]
    async fn body_larger_than_pinned_size_is_rejected_without_a_partial_file() {
        let dir = tempfile::tempdir().unwrap();
        let reply = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n4\r\nabcd\r\n0\r\n\r\n".to_vec();
        let (url, worker) = server(vec![reply]);
        let result = ensure_model(dir.path(), &AtomicBool::new(false), fixture_spec(&url), test_client).await;
        worker.join().unwrap();
        assert!(result.is_err());
        assert_eq!(fs::read_dir(dir.path()).unwrap().count(), 0);
    }

    #[tokio::test]
    async fn simultaneous_installers_leave_one_complete_verified_cache_file() {
        let dir = tempfile::tempdir().unwrap();
        let (url, worker) = server(vec![response(FIXTURE, FIXTURE.len()), response(FIXTURE, FIXTURE.len())]);
        let cancelled = AtomicBool::new(false);
        let first = ensure_model(dir.path(), &cancelled, fixture_spec(&url), test_client);
        let second = ensure_model(dir.path(), &cancelled, fixture_spec(&url), test_client);
        let (first, second) = tokio::join!(first, second);
        worker.join().unwrap();
        assert_eq!(first.unwrap(), second.unwrap());
        assert_eq!(fs::read(dir.path().join(MODEL_NAME)).unwrap(), FIXTURE);
        assert_only_model(dir.path());
    }

    #[tokio::test]
    async fn cancellation_during_a_stalled_download_removes_its_partial_file() {
        let dir = tempfile::tempdir().unwrap();
        let (url, release, worker) = stalled_server();
        let cancelled = AtomicBool::new(false);
        let partial_written = tokio::sync::Notify::new();
        let spec = ModelSpec { partial_written: Some(&partial_written), ..fixture_spec(&url) };
        let mut install = Box::pin(ensure_model(dir.path(), &cancelled, spec, test_client));
        tokio::time::timeout(Duration::from_secs(4), async {
            tokio::select! {
                biased;
                result = &mut install => panic!("download ended before cancellation: {result:?}"),
                _ = partial_written.notified() => {}
            }
        }).await.expect("partial download did not start");
        assert_partial_file(dir.path());
        cancelled.store(true, Ordering::Relaxed);
        let result = tokio::time::timeout(Duration::from_secs(3), &mut install)
            .await.expect("installer did not respond to cancellation");
        assert!(matches!(&result, Err(InstallError::Cancelled)), "unexpected installer result: {result:?}");
        assert_eq!(fs::read_dir(dir.path()).unwrap().count(), 0);
        release.send(()).unwrap();
        worker.join().unwrap();
    }
    #[tokio::test]
    async fn dropping_a_pending_installer_removes_its_partial_file() {
        let dir = tempfile::tempdir().unwrap();
        let (url, release, worker) = stalled_server();
        let cancelled = AtomicBool::new(false);
        let partial_written = tokio::sync::Notify::new();
        let spec = ModelSpec { partial_written: Some(&partial_written), ..fixture_spec(&url) };
        let mut install = Box::pin(ensure_model(dir.path(), &cancelled, spec, test_client));
        tokio::time::timeout(Duration::from_secs(4), async {
            tokio::select! {
                biased;
                result = &mut install => panic!("download completed before it could be dropped: {result:?}"),
                _ = partial_written.notified() => {}
            }
        }).await.expect("partial download did not start");
        assert_partial_file(dir.path());
        drop(install);
        assert_eq!(fs::read_dir(dir.path()).unwrap().count(), 0);
        release.send(()).unwrap();
        worker.join().unwrap();
    }


    #[tokio::test]
    async fn already_cancelled_does_not_use_cache_or_network() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join(MODEL_NAME), FIXTURE).unwrap();
        let cancelled = AtomicBool::new(true);
        let result = ensure_model(dir.path(), &cancelled, fixture_spec("http://127.0.0.1:1/model"), || {
            panic!("cancelled work must not construct a client")
        }).await;
        assert!(matches!(result, Err(InstallError::Cancelled)));
        assert_only_model(dir.path());
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn cache_symlink_is_not_followed() {
        let dir = tempfile::tempdir().unwrap();
        let target = tempfile::NamedTempFile::new().unwrap();
        fs::write(target.path(), FIXTURE).unwrap();
        std::os::unix::fs::symlink(target.path(), dir.path().join(MODEL_NAME)).unwrap();
        let result = ensure_model(dir.path(), &AtomicBool::new(false), fixture_spec("http://127.0.0.1:1/model"), || {
            panic!("a symlink must not be accepted or downloaded over")
        }).await;
        assert!(result.is_err());
        assert_eq!(fs::read(target.path()).unwrap(), FIXTURE);
    }
}

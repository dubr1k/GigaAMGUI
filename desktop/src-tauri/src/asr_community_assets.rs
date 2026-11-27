//! Verified, atomic installation of the converted Community-1 FP32 bundle.
//!
//! The source model is pyannoteAI's `pyannote/speaker-diarization-community-1`,
//! https://huggingface.co/pyannote/speaker-diarization-community-1, licensed
//! under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). The pinned
//! assets are ONNX/JSON conversions of snapshot 3533c8cf8e369892e6b79ff1bf80f7b0286a54ee.

use std::fs::{self, OpenOptions};
use std::future::Future;
use std::io::{self, Write};
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use reqwest::{redirect::Policy, Client};
use sha2::{Digest, Sha256};
use tempfile::TempDir;

const DISK_RESERVE: u64 = 512 * 1024 * 1024;

struct Asset<'a> {
    path: &'a str,
    url: &'a str,
    size: u64,
    sha256: &'a str,
}

// Release metadata is embedded in the application, not supplied by the server.
const ASSETS: [Asset<'static>; 4] = [
    Asset {
        path: "export.json",
        url: "https://github.com/dubr1k/GigaAMGUI/releases/download/community-1-fp32-v1/export.json",
        size: 2882,
        sha256: "99cf740b8093e4a4c7491fcfdd389887941a470ac651887694384940a2dea7ad",
    },
    Asset {
        path: "plda.json",
        url: "https://github.com/dubr1k/GigaAMGUI/releases/download/community-1-fp32-v1/plda.json",
        size: 983502,
        sha256: "8cadc0a017fce6e9fc9f1802b40b7a2a367889ec6f6e01b31f7b5b5adeead17a",
    },
    Asset {
        path: "tract/segmentation.onnx",
        url: "https://github.com/dubr1k/GigaAMGUI/releases/download/community-1-fp32-v1/segmentation.onnx",
        size: 5984909,
        sha256: "8e8ddc4b066e14bf6fdc0e97a93206326de6346f7b99df9f06a5f9903d3ae2b5",
    },
    Asset {
        path: "tract/embedding.onnx",
        url: "https://github.com/dubr1k/GigaAMGUI/releases/download/community-1-fp32-v1/embedding.onnx",
        size: 26553403,
        sha256: "26ce0859c0afd8084e60b3ccd9d9f0a1999785ac18b13e1abf51cca50caea5af",
    },
];

fn check_cancelled(cancelled: &AtomicBool) -> Result<(), String> {
    if cancelled.load(Ordering::Acquire) { Err("Задание отменено".into()) } else { Ok(()) }
}

fn client() -> Result<Client, String> {
    let redirects = Policy::custom(|attempt| {
        let url = attempt.url();
        if attempt.previous().len() < 5 && url.scheme() == "https"
            && url.username().is_empty() && url.password().is_none()
            && url.port().map_or(true, |port| port == 443)
            && matches!(url.host_str(), Some("github.com" | "release-assets.githubusercontent.com"))
        {
            attempt.follow()
        } else {
            attempt.error("untrusted Community-1 download redirect")
        }
    });
    Client::builder().https_only(true).redirect(redirects)
        .connect_timeout(Duration::from_secs(20)).timeout(Duration::from_secs(3600))
        .build().map_err(|_| "Не удалось создать клиент загрузки Community-1".into())
}

async fn cancellable<F: Future>(future: F, cancelled: &AtomicBool) -> Result<F::Output, String> {
    tokio::pin!(future);
    loop {
        check_cancelled(cancelled)?;
        tokio::select! {
            result = &mut future => return Ok(result),
            _ = tokio::time::sleep(Duration::from_millis(100)) => {},
        }
    }
}

// Retain the existing local-bundle contract, including user-supplied exports.
// The inference loader performs the complete manifest and file-hash validation.
fn existing_bundle(directory: &Path) -> Result<bool, String> {
    match fs::symlink_metadata(directory) {
        Ok(_) => {
            super::long_diarization::check_community_bundle(directory).map_err(|error| format!(
                "{error}. Существующий каталог Community-1 не изменён; исправьте его или переместите для автоматической установки"
            ))?;
            Ok(true)
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(format!("Не удалось проверить каталог Community-1 {}: {error}", directory.display())),
    }
}

/// Never changes a pre-existing bundle. A completed download appears at the
/// destination in a single no-replace rename; cancellation drops the staging dir.
pub(super) async fn ensure(directory: &Path, cancelled: &AtomicBool) -> Result<(), String> {
    ensure_with(directory, cancelled, &ASSETS, client).await
}

async fn ensure_with<F>(directory: &Path, cancelled: &AtomicBool, assets: &[Asset<'_>], make_client: F) -> Result<(), String>
where
    F: FnOnce() -> Result<Client, String>,
{
    check_cancelled(cancelled)?;
    if existing_bundle(directory)? {
        return Ok(());
    }
    let parent = directory.parent().ok_or_else(|| "Некорректный путь к каталогу Community-1".to_string())?;
    fs::create_dir_all(parent).map_err(|error| format!("Не удалось создать каталог моделей: {error}"))?;
    let required = assets.iter().try_fold(DISK_RESERVE, |total, asset| total.checked_add(asset.size))
        .ok_or_else(|| "Некорректный размер модели Community-1".to_string())?;
    let available = fs2::available_space(parent).map_err(|error| format!("Не удалось проверить место для Community-1: {error}"))?;
    if available < required {
        return Err("Недостаточно свободного места для Community-1 и резервного пространства".into());
    }
    check_cancelled(cancelled)?;
    let client = make_client()?;
    let stage = tempfile::Builder::new().prefix(".community-1-install-").tempdir_in(parent)
        .map_err(|error| format!("Не удалось создать временный каталог Community-1: {error}"))?;

    let result: Result<bool, String> = async {
        install(&client, &stage, assets, cancelled).await?;
        check_cancelled(cancelled)?;
        match publish_noreplace(stage.path(), directory) {
            Ok(()) => Ok(true),
            Err(error) => {
                // Another process may have finished the same installation, or
                // the user may have created a directory while we downloaded.
                check_cancelled(cancelled)?;
                if existing_bundle(directory)? {
                    Ok(false)
                } else {
                    Err(format!("Не удалось безопасно установить Community-1: {error}"))
                }
            }
        }
    }.await;

    match result {
        // The directory was renamed, so the TempDir's old path no longer exists.
        Ok(true) => check_cancelled(cancelled),
        Ok(false) => {
            stage.close().map_err(|error| format!("Не удалось удалить временные файлы Community-1: {error}"))?;
            check_cancelled(cancelled)
        }
        Err(error) => match stage.close() {
            Ok(()) => Err(error),
            Err(cleanup) => Err(format!("{error}; не удалось удалить временные файлы Community-1: {cleanup}")),
        },
    }
}

async fn install(client: &Client, stage: &TempDir, assets: &[Asset<'_>], cancelled: &AtomicBool) -> Result<(), String> {
    fs::create_dir(stage.path().join("tract"))
        .map_err(|error| format!("Не удалось подготовить каталог Community-1: {error}"))?;
    for asset in assets {
        check_cancelled(cancelled)?;
        let response = cancellable(client.get(asset.url).send(), cancelled).await?
            .map_err(|_| format!("Не удалось скачать {} для Community-1; проверьте подключение к интернету", asset.path))?;
        if !response.status().is_success() {
            return Err(format!("Не удалось скачать {} для Community-1: HTTP {}. Проверьте доступность релиза", asset.path, response.status().as_u16()));
        }
        if response.content_length().is_some_and(|length| length != asset.size) {
            return Err(format!("Неверный размер файла Community-1: {}", asset.path));
        }
        let mut response = response;
        let mut file = OpenOptions::new().write(true).create_new(true).open(stage.path().join(asset.path))
            .map_err(|error| format!("Не удалось сохранить {} для Community-1: {error}", asset.path))?;
        let mut hasher = Sha256::new();
        let mut received = 0u64;
        while let Some(chunk) = cancellable(response.chunk(), cancelled).await?
            .map_err(|_| format!("Загрузка {} для Community-1 прервалась", asset.path))?
        {
            received = received.checked_add(chunk.len() as u64)
                .ok_or_else(|| format!("Неверный размер файла Community-1: {}", asset.path))?;
            if received > asset.size {
                return Err(format!("Неверный размер файла Community-1: {}", asset.path));
            }
            file.write_all(&chunk).map_err(|error| format!("Не удалось сохранить {} для Community-1: {error}", asset.path))?;
            hasher.update(&chunk);
        }
        check_cancelled(cancelled)?;
        if received != asset.size {
            return Err(format!("Неполный файл Community-1: {}", asset.path));
        }
        let digest = hasher.finalize();
        let expected = asset.sha256.as_bytes();
        const HEX: &[u8; 16] = b"0123456789abcdef";
        if expected.len() != 64 || digest.iter().enumerate().any(|(index, byte)| {
            expected[index * 2] != HEX[(byte >> 4) as usize] || expected[index * 2 + 1] != HEX[(byte & 15) as usize]
        }) {
            return Err(format!("Не совпадает контрольная сумма Community-1: {}", asset.path));
        }
        file.sync_all().map_err(|error| format!("Не удалось завершить запись {} для Community-1: {error}", asset.path))?;
    }
    Ok(())
}

// Standard rename may replace an empty directory on Unix. Use an atomic
// no-replace operation instead, including for races with non-app processes.
#[cfg(target_os = "linux")]
fn publish_noreplace(source: &Path, destination: &Path) -> io::Result<()> {
    use std::ffi::{c_char, c_int, c_uint, CString};
    use std::os::unix::ffi::OsStrExt;

    extern "C" {
        fn renameat2(old_dir: c_int, old_path: *const c_char, new_dir: c_int, new_path: *const c_char, flags: c_uint) -> c_int;
    }
    let source = CString::new(source.as_os_str().as_bytes()).map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "path contains NUL"))?;
    let destination = CString::new(destination.as_os_str().as_bytes()).map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "path contains NUL"))?;
    const AT_FDCWD: c_int = -100;
    const RENAME_NOREPLACE: c_uint = 1;
    // SAFETY: both C strings remain valid for the call and are NUL-terminated.
    if unsafe { renameat2(AT_FDCWD, source.as_ptr(), AT_FDCWD, destination.as_ptr(), RENAME_NOREPLACE) } == 0 {
        Ok(())
    } else {
        Err(io::Error::last_os_error())
    }
}

#[cfg(target_os = "windows")]
fn publish_noreplace(source: &Path, destination: &Path) -> io::Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::MoveFileW;

    fn wide(path: &Path) -> io::Result<Vec<u16>> {
        let mut value: Vec<u16> = path.as_os_str().encode_wide().collect();
        if value.contains(&0) {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, "path contains NUL"));
        }
        value.push(0);
        Ok(value)
    }
    let source = wide(source)?;
    let destination = wide(destination)?;
    // SAFETY: both buffers are valid NUL-terminated UTF-16 paths. MoveFileW
    // fails when the destination exists, rather than replacing it.
    if unsafe { MoveFileW(source.as_ptr(), destination.as_ptr()) } != 0 {
        Ok(())
    } else {
        Err(io::Error::last_os_error())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::{TcpListener, TcpStream};
    use std::sync::{Arc, Barrier};
    use std::thread;
    use std::time::Instant;

    const PATHS: [&str; 4] = ["export.json", "plda.json", "tract/segmentation.onnx", "tract/embedding.onnx"];
    const NAMES: [&str; 4] = ["export.json", "plda.json", "segmentation.onnx", "embedding.onnx"];
    const BODIES: [&[u8]; 4] = [b"manifest", b"plda", b"segmentation", b"embedding"];

    fn hashes() -> Vec<String> {
        BODIES.iter().map(|body| Sha256::digest(body).iter().map(|byte| format!("{byte:02x}")).collect()).collect()
    }

    fn assets<'a>(urls: &'a [String], hashes: &'a [String]) -> Vec<Asset<'a>> {
        (0..PATHS.len()).map(|index| Asset {
            path: PATHS[index], url: &urls[index], size: BODIES[index].len() as u64, sha256: &hashes[index],
        }).collect()
    }

    fn urls(base: &str) -> Vec<String> {
        NAMES.iter().map(|name| format!("{base}/{name}")).collect()
    }

    fn accept(listener: &TcpListener) -> TcpStream {
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            match listener.accept() {
                Ok((stream, _)) => return stream,
                Err(error) if error.kind() == io::ErrorKind::WouldBlock && Instant::now() < deadline => thread::sleep(Duration::from_millis(10)),
                Err(error) => panic!("accept: {error}"),
            }
        }
    }

    fn read_request(stream: &mut TcpStream, name: &str) {
        stream.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
        let mut request = Vec::new();
        let mut buffer = [0u8; 1024];
        while !request.windows(4).any(|part| part == b"\r\n\r\n") {
            let count = stream.read(&mut buffer).unwrap();
            assert!(count > 0 && request.len() < 8192);
            request.extend_from_slice(&buffer[..count]);
        }
        assert!(request.starts_with(format!("GET /{name} ").as_bytes()));
    }

    fn server(responses: Vec<(&'static str, u16, Vec<u8>)>) -> (String, thread::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let handle = thread::spawn(move || {
            for (name, status, body) in responses {
                let mut stream = accept(&listener);
                read_request(&mut stream, name);
                write!(stream, "HTTP/1.1 {status} Test\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", body.len()).unwrap();
                stream.write_all(&body).unwrap();
            }
        });
        (base, handle)
    }

    fn assert_no_stage(root: &Path) {
        assert!(!fs::read_dir(root).unwrap().any(|entry| entry.unwrap().file_name().to_string_lossy().starts_with(".community-1-install-")));
    }

    #[tokio::test]
    async fn existing_manual_bundle_is_preserved_without_a_client() {
        let root = tempfile::tempdir().unwrap();
        let destination = root.path().join("pyannote-community");
        fs::create_dir_all(destination.join("tract")).unwrap();
        for (path, body) in PATHS.iter().zip(BODIES) {
            fs::write(destination.join(path), body).unwrap();
        }
        fs::write(destination.join("user-note"), b"keep").unwrap();
        ensure_with(&destination, &AtomicBool::new(false), &ASSETS, || panic!("existing bundle must not use the network")).await.unwrap();
        assert_eq!(fs::read(destination.join("user-note")).unwrap(), b"keep");
        assert_eq!(fs::read(destination.join(PATHS[3])).unwrap(), BODIES[3]);
        assert_no_stage(root.path());
    }

    #[tokio::test]
    async fn incomplete_existing_directory_is_never_modified() {
        let root = tempfile::tempdir().unwrap();
        let destination = root.path().join("pyannote-community");
        fs::create_dir(&destination).unwrap();
        fs::write(destination.join("user-note"), b"keep").unwrap();
        fs::write(destination.join("export.json"), b"invalid-manifest").unwrap();
        let error = ensure_with(&destination, &AtomicBool::new(false), &ASSETS, || panic!("existing directory must not use the network")).await.unwrap_err();
        assert!(error.contains("Не найден подготовленный файл Community-1"));
        assert!(error.contains("не изменён"));
        assert_eq!(fs::read(destination.join("user-note")).unwrap(), b"keep");
        assert_eq!(fs::read(destination.join("export.json")).unwrap(), b"invalid-manifest");
        assert_eq!(fs::read_dir(&destination).unwrap().count(), 2);
        assert_no_stage(root.path());
    }

    #[tokio::test]
    async fn downloads_and_publishes_only_the_complete_verified_bundle() {
        let root = tempfile::tempdir().unwrap();
        let destination = root.path().join("pyannote-community");
        let responses = NAMES.iter().zip(BODIES).map(|(name, body)| (*name, 200, body.to_vec())).collect();
        let (base, server) = server(responses);
        let urls = urls(&base);
        let hashes = hashes();
        ensure_with(&destination, &AtomicBool::new(false), &assets(&urls, &hashes), || Ok(Client::new())).await.unwrap();
        server.join().unwrap();
        for (path, body) in PATHS.iter().zip(BODIES) {
            assert_eq!(fs::read(destination.join(path)).unwrap(), body);
        }
        assert_no_stage(root.path());
    }

    #[tokio::test]
    async fn bad_checksum_removes_staging_without_publishing() {
        let root = tempfile::tempdir().unwrap();
        let destination = root.path().join("pyannote-community");
        let responses = NAMES.iter().zip(BODIES).take(3).map(|(name, body)| (*name, 200, body.to_vec())).collect();
        let (base, server) = server(responses);
        let urls = urls(&base);
        let mut hashes = hashes();
        hashes[2] = "0".repeat(64);
        let error = ensure_with(&destination, &AtomicBool::new(false), &assets(&urls, &hashes), || Ok(Client::new())).await.unwrap_err();
        server.join().unwrap();
        assert!(error.contains("контрольная сумма"));
        assert!(!destination.exists());
        assert_no_stage(root.path());
    }

    #[tokio::test]
    async fn wrong_content_length_removes_staging_without_publishing() {
        let root = tempfile::tempdir().unwrap();
        let destination = root.path().join("pyannote-community");
        let (base, server) = server(vec![(NAMES[0], 200, Vec::new())]);
        let urls = urls(&base);
        let hashes = hashes();
        let error = ensure_with(&destination, &AtomicBool::new(false), &assets(&urls, &hashes), || Ok(Client::new())).await.unwrap_err();
        server.join().unwrap();
        assert!(error.contains("Неверный размер"));
        assert!(!destination.exists());
        assert_no_stage(root.path());
    }

    #[tokio::test]
    async fn http_failure_removes_staging_without_publishing() {
        let root = tempfile::tempdir().unwrap();
        let destination = root.path().join("pyannote-community");
        let (base, server) = server(vec![(NAMES[0], 404, Vec::new())]);
        let urls = urls(&base);
        let hashes = hashes();
        let error = ensure_with(&destination, &AtomicBool::new(false), &assets(&urls, &hashes), || Ok(Client::new())).await.unwrap_err();
        server.join().unwrap();
        assert!(error.contains("HTTP 404"));
        assert!(!destination.exists());
        assert_no_stage(root.path());
    }

    #[tokio::test]
    async fn dropping_an_inflight_install_removes_staging() {
        let root = tempfile::tempdir().unwrap();
        let destination = root.path().join("pyannote-community");
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let (started, ready) = tokio::sync::oneshot::channel();
        let (finish, wait_for_finish) = std::sync::mpsc::channel();
        let server = thread::spawn(move || {
            let mut stream = accept(&listener);
            read_request(&mut stream, NAMES[0]);
            write!(stream, "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\nm", BODIES[0].len()).unwrap();
            stream.flush().unwrap();
            let _ = started.send(());
            let _ = wait_for_finish.recv_timeout(Duration::from_secs(5));
        });
        let urls = urls(&base);
        let hashes = hashes();
        let assets = assets(&urls, &hashes);
        let cancelled = AtomicBool::new(false);
        let mut installation = Box::pin(ensure_with(&destination, &cancelled, &assets, || Ok(Client::new())));
        tokio::select! {
            result = ready => result.expect("server did not start the response"),
            result = &mut installation => panic!("installation finished prematurely: {result:?}"),
        }
        drop(installation);
        finish.send(()).unwrap();
        server.join().unwrap();
        assert!(!destination.exists());
        assert_no_stage(root.path());
    }

    #[tokio::test]
    async fn cancellation_during_a_download_removes_staging() {
        let root = tempfile::tempdir().unwrap();
        let destination = root.path().join("pyannote-community");
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let (started, ready) = tokio::sync::oneshot::channel();
        let server = thread::spawn(move || {
            let mut stream = accept(&listener);
            read_request(&mut stream, NAMES[0]);
            write!(stream, "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\nm", BODIES[0].len()).unwrap();
            stream.flush().unwrap();
            let _ = started.send(());
            thread::sleep(Duration::from_secs(1));
        });
        let cancelled = Arc::new(AtomicBool::new(false));
        let flag = cancelled.clone();
        let cancel = tokio::spawn(async move {
            if tokio::time::timeout(Duration::from_secs(5), ready).await.is_ok() {
                flag.store(true, Ordering::Release);
            }
        });
        let urls = urls(&base);
        let hashes = hashes();
        let error = ensure_with(&destination, &cancelled, &assets(&urls, &hashes), || Ok(Client::new())).await.unwrap_err();
        cancel.await.unwrap();
        server.join().unwrap();
        assert!(error.contains("отменено"));
        assert!(!destination.exists());
        assert_no_stage(root.path());
    }

    #[test]
    fn publication_never_replaces_even_an_empty_user_directory() {
        let root = tempfile::tempdir().unwrap();
        let source = root.path().join("stage");
        let destination = root.path().join("pyannote-community");
        fs::create_dir(&source).unwrap();
        fs::write(source.join("file"), b"download").unwrap();
        fs::create_dir(&destination).unwrap();
        assert!(publish_noreplace(&source, &destination).is_err());
        assert_eq!(fs::read_dir(&destination).unwrap().count(), 0);
        assert_eq!(fs::read(source.join("file")).unwrap(), b"download");
    }

    #[test]
    fn concurrent_publications_have_exactly_one_winner() {
        let root = tempfile::tempdir().unwrap();
        let destination = root.path().join("pyannote-community");
        let barrier = Arc::new(Barrier::new(3));
        let mut threads = Vec::new();
        for label in ["one", "two"] {
            let source = root.path().join(label);
            fs::create_dir(&source).unwrap();
            fs::write(source.join("file"), label).unwrap();
            let destination = destination.clone();
            let barrier = barrier.clone();
            threads.push(thread::spawn(move || {
                barrier.wait();
                (label, publish_noreplace(&source, &destination))
            }));
        }
        barrier.wait();
        let results: Vec<_> = threads.into_iter().map(|thread| thread.join().unwrap()).collect();
        assert_eq!(results.iter().filter(|(_, result)| result.is_ok()).count(), 1);
        let winner = results.iter().find(|(_, result)| result.is_ok()).unwrap().0;
        assert_eq!(fs::read(destination.join("file")).unwrap(), winner.as_bytes());
    }
}

//! Verified model installation. Metadata is supplied by the release build, not
//! downloaded from the network; a release URL alone is not an integrity pin.

use std::collections::HashSet;
use std::fs::{self, File, OpenOptions};
use std::future::Future;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use reqwest::{header, redirect::Policy, Client, StatusCode, Url};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use tempfile::{NamedTempFile, TempDir};

use crate::asr_zapformer::ZapformerModel;

const V4_METADATA: &str = include_str!(concat!(env!("OUT_DIR"), "/gigaam_v4_assets.json"));
const MULTILINGUAL_METADATA: &str = include_str!(concat!(env!("OUT_DIR"), "/gigaam_multilingual_v2_assets.json"));
const MAX_METADATA: usize = 2_000_000;
const MAX_PART: u64 = 1_900_000_000;
const MAX_PARTS: usize = 128;
const MAX_FILES: usize = 4096;
const DISK_RESERVE: u64 = 512 * 1024 * 1024;
const REQUIRED: [&str; 7] = [
    "streaming.json", "rnnt_320ms_stream_frontend.onnx", "rnnt_320ms_stream_step.onnx",
    "rnnt_encoder.onnx", "rnnt_e2e_decoder.onnx", "rnnt_e2e_joint.onnx", "rnnt_e2e_tokenizer.model",
];

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    version: u32,
    model_id: String,
    parts: Vec<Part>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Part {
    url: String,
    size: u64,
    sha256: String,
    files: Vec<ModelFile>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ModelFile {
    name: String,
    size: u64,
    sha256: String,
}

fn check_cancelled(cancelled: &AtomicBool) -> Result<(), String> {
    if cancelled.load(Ordering::Acquire) { Err("Задание отменено".into()) } else { Ok(()) }
}

fn valid_hash(hash: &str) -> bool {
    hash.len() == 64 && hash.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn hex_hash(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for &byte in bytes {
        out.push(HEX[(byte >> 4) as usize] as char);
        out.push(HEX[(byte & 15) as usize] as char);
    }
    out
}

fn valid_name(name: &str) -> bool {
    if name.is_empty() || name.len() > 255 || name.starts_with('.') || name.ends_with('.')
        || !name.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-' | b'.'))
    {
        return false;
    }
    let stem = name.split('.').next().unwrap_or_default().to_ascii_uppercase();
    if matches!(stem.as_str(), "CON" | "PRN" | "AUX" | "NUL") { return false; }
    let bytes = stem.as_bytes();
    !(bytes.len() == 4 && (&bytes[..3] == b"COM" || &bytes[..3] == b"LPT") && (b'1'..=b'9').contains(&bytes[3]))
}

fn valid_url(value: &str) -> bool {
    let Ok(url) = Url::parse(value) else { return false };
    if url.scheme() != "https" || url.host_str() != Some("github.com")
        || !url.username().is_empty() || url.password().is_some()
        || url.port().is_some_and(|port| port != 443) || url.query().is_some() || url.fragment().is_some()
    {
        return false;
    }
    let Some(segments) = url.path_segments() else { return false };
    let segments: Vec<_> = segments.collect();
    segments.len() == 6
        && segments[..4] == ["dubr1k", "GigaAMGUI", "releases", "download"]
        && valid_name(segments[4]) && valid_name(segments[5])
}

fn metadata(model: ZapformerModel) -> Result<Manifest, String> {
    let raw = match model {
        ZapformerModel::V4Rnnt => V4_METADATA,
        ZapformerModel::MultilingualV2Rnnt => MULTILINGUAL_METADATA,
    };
    parse_metadata(raw, model)
}

fn parse_metadata(raw: &str, model: ZapformerModel) -> Result<Manifest, String> {
    if raw.trim() == "null" {
        return Err(format!("Модель {} недоступна: проверенные данные публикации не включены в сборку", model.id()));
    }
    if raw.len() > MAX_METADATA { return Err("Слишком большой встроенный манифест модели".into()); }
    let manifest: Manifest = serde_json::from_str(raw).map_err(|_| "Некорректный встроенный манифест модели")?;
    if manifest.version != 1 || manifest.model_id != model.id()
        || manifest.parts.is_empty() || manifest.parts.len() > MAX_PARTS
    {
        return Err("Несовместимый встроенный манифест модели".into());
    }
    let mut names = HashSet::new();
    let mut urls = HashSet::new();
    for part in &manifest.parts {
        if !valid_url(&part.url) || !urls.insert(part.url.as_str()) || !valid_hash(&part.sha256)
            || part.size == 0 || part.size > MAX_PART || part.files.is_empty()
        {
            return Err("Некорректная часть манифеста модели".into());
        }
        let mut size = 0u64;
        for file in &part.files {
            if !valid_name(&file.name) || !valid_hash(&file.sha256) || file.size == 0
                || !names.insert(file.name.to_ascii_lowercase()) || names.len() > MAX_FILES
            {
                return Err("Некорректный файл в манифесте модели".into());
            }
            size = size.checked_add(file.size).ok_or("Переполнение размера модели")?;
        }
        if size != part.size { return Err("Размер части не соответствует входящим файлам".into()); }
    }
    if REQUIRED.iter().any(|name| !names.contains(*name)) {
        return Err("Встроенный манифест не содержит все графы модели".into());
    }
    Ok(manifest)
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
            attempt.error("untrusted model download redirect")
        }
    });
    Client::builder().https_only(true).redirect(redirects)
        .connect_timeout(Duration::from_secs(20)).timeout(Duration::from_secs(3600))
        .build().map_err(|_| "Не удалось создать клиент загрузки модели".into())
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

fn regular_file(path: &Path, size: u64) -> bool {
    fs::symlink_metadata(path).is_ok_and(|metadata| metadata.file_type().is_file() && metadata.len() == size)
}

fn verified_directory(directory: &Path, manifest: &Manifest, cancelled: &AtomicBool) -> Result<bool, String> {
    if !fs::symlink_metadata(directory).is_ok_and(|m| m.file_type().is_dir()) { return Ok(false); }
    let mut buffer = vec![0u8; 128 * 1024];
    for part in &manifest.parts {
        let mut part_hash = Sha256::new();
        for entry in &part.files {
            check_cancelled(cancelled)?;
            let path = directory.join(&entry.name);
            if !regular_file(&path, entry.size) { return Ok(false); }
            let Ok(mut file) = File::open(path) else { return Ok(false) };
            if !file.metadata().is_ok_and(|metadata| metadata.is_file() && metadata.len() == entry.size) {
                return Ok(false);
            }
            let mut file_hash = Sha256::new();
            let mut length = 0u64;
            loop {
                check_cancelled(cancelled)?;
                let count = file.read(&mut buffer).map_err(|error| format!("Не удалось проверить файл модели: {error}"))?;
                if count == 0 { break; }
                length = length.checked_add(count as u64).ok_or("Переполнение размера модели")?;
                if length > entry.size { return Ok(false); }
                part_hash.update(&buffer[..count]);
                file_hash.update(&buffer[..count]);
            }
            if length != entry.size || hex_hash(&file_hash.finalize()) != entry.sha256 { return Ok(false); }
        }
        if hex_hash(&part_hash.finalize()) != part.sha256 { return Ok(false); }
    }
    check_cancelled(cancelled)?;
    Ok(true)
}

fn cached_directory(root: &Path, manifest: &Manifest, cancelled: &AtomicBool) -> Result<Option<PathBuf>, String> {
    let marker = root.join("current");
    if !fs::symlink_metadata(&marker).is_ok_and(|m| m.file_type().is_file() && m.len() <= 100) {
        return Ok(None);
    }
    let Ok(name) = fs::read_to_string(marker) else { return Ok(None) };
    if !name.starts_with("bundle-") || !valid_name(&name) { return Ok(None); }
    let directory = root.join(name);
    if verified_directory(&directory, manifest, cancelled)? { Ok(Some(directory)) } else { Ok(None) }
}
/// Cheap UI hint only. `ensure` verifies every hash before loading the model.
pub(crate) fn installed_hint(root: &Path, model: ZapformerModel) -> Option<bool> {
    let manifest = metadata(model).ok()?;
    let marker = root.join("current");
    let name = if fs::symlink_metadata(&marker).is_ok_and(|m| m.file_type().is_file() && m.len() <= 100) {
        fs::read_to_string(&marker).ok().filter(|name| name.starts_with("bundle-") && valid_name(name))
    } else {
        None
    };
    let installed = name.is_some_and(|name| {
        let directory = root.join(name);
        fs::symlink_metadata(&directory).is_ok_and(|m| m.file_type().is_dir())
            && manifest.parts.iter().flat_map(|part| &part.files)
                .all(|file| regular_file(&directory.join(&file.name), file.size))
    });
    Some(installed)
}



/// Ensure the pinned complete bundle is available. Incomplete staging dirs are
/// private to this attempt and removed on error, cancellation or future drop.
/// A previous published directory is retained for another process using it.
pub(crate) async fn ensure(root: &Path, model: ZapformerModel, cancelled: &AtomicBool) -> Result<PathBuf, String> {
    let manifest = metadata(model)?;
    check_cancelled(cancelled)?;
    fs::create_dir_all(root).map_err(|error| format!("Не удалось создать каталог модели: {error}"))?;
    if !fs::symlink_metadata(root).is_ok_and(|m| m.file_type().is_dir()) {
        return Err("Каталог модели должен быть обычным каталогом".into());
    }
    if let Some(path) = cached_directory(root, &manifest, cancelled)? { return Ok(path); }
    let needed = manifest.parts.iter().try_fold(DISK_RESERVE, |sum, part| sum.checked_add(part.size))
        .ok_or("Переполнение размера модели")?;
    let available = fs2::available_space(root).map_err(|error| format!("Не удалось проверить свободное место: {error}"))?;
    if available < needed { return Err("Недостаточно места для установки модели и системного резерва".into()); }
    let stage = tempfile::Builder::new().prefix("bundle-").tempdir_in(root)
        .map_err(|error| format!("Не удалось создать временный каталог модели: {error}"))?;
    let result = install(&client()?, &stage, &manifest, cancelled).await;
    if let Err(error) = result {
        return match stage.close() {
            Ok(()) => Err(error),
            Err(cleanup) => Err(format!("{error}; не удалось удалить временную модель: {cleanup}")),
        };
    }
    check_cancelled(cancelled)?;
    let name = stage.path().file_name().and_then(|name| name.to_str()).ok_or("Недопустимый временный каталог модели")?.to_owned();
    let mut marker = NamedTempFile::new_in(root).map_err(|error| format!("Не удалось создать указатель модели: {error}"))?;
    marker.write_all(name.as_bytes()).and_then(|_| marker.as_file_mut().sync_all())
        .map_err(|error| format!("Не удалось записать указатель модели: {error}"))?;
    check_cancelled(cancelled)?;
    let directory = stage.keep();
    if let Err(error) = marker.persist(root.join("current")) {
        let cleanup = fs::remove_dir_all(&directory);
        return Err(match cleanup {
            Ok(()) => format!("Не удалось опубликовать модель: {}", error.error),
            Err(cleanup) => format!("Не удалось опубликовать модель: {}; не удалось удалить {}: {cleanup}", error.error, directory.display()),
        });
    }
    Ok(directory)
}

async fn install(client: &Client, stage: &TempDir, manifest: &Manifest, cancelled: &AtomicBool) -> Result<(), String> {
    for part in &manifest.parts {
        check_cancelled(cancelled)?;
        let request = client.get(&part.url).header(header::ACCEPT_ENCODING, "identity").send();
        let mut response = cancellable(request, cancelled).await?
            .map_err(|_| "Не удалось загрузить часть модели".to_string())?;
        if response.status() != StatusCode::OK || response.content_length().is_some_and(|size| size != part.size) {
            return Err("Сервер вернул неверный размер или статус части модели".into());
        }
        let mut part_hash = Sha256::new();
        let mut part_length = 0u64;
        let mut index = 0usize;
        let mut current: Option<(File, Sha256, u64)> = None;
        let mut space_check_budget = 0usize;
        loop {
            let chunk = cancellable(response.chunk(), cancelled).await?
                .map_err(|_| "Ошибка чтения части модели".to_string())?;
            let Some(chunk) = chunk else { break };
            part_length = part_length.checked_add(chunk.len() as u64).ok_or("Переполнение размера части")?;
            if part_length > part.size { return Err("Часть модели больше указанного размера".into()); }
            if chunk.len() > space_check_budget {
                let available = fs2::available_space(stage.path())
                    .map_err(|error| format!("Не удалось проверить свободное место: {error}"))?;
                let needed = DISK_RESERVE.saturating_add(chunk.len() as u64);
                if available < needed {
                    return Err("Недостаточно места для продолжения загрузки модели".into());
                }
                space_check_budget = (available - DISK_RESERVE).min(16 * 1024 * 1024) as usize;
            }
            space_check_budget = space_check_budget.saturating_sub(chunk.len());
            part_hash.update(&chunk);
            let mut bytes = chunk.as_ref();
            while !bytes.is_empty() {
                check_cancelled(cancelled)?;
                let entry = part.files.get(index).ok_or("Лишние данные части модели")?;
                if current.is_none() {
                    let file = OpenOptions::new().write(true).create_new(true).open(stage.path().join(&entry.name))
                        .map_err(|error| format!("Не удалось создать файл модели: {error}"))?;
                    current = Some((file, Sha256::new(), 0));
                }
                let (file, hash, written) = current.as_mut().ok_or("Ошибка состояния загрузки")?;
                let take = bytes.len().min((entry.size - *written) as usize);
                file.write_all(&bytes[..take]).map_err(|error| format!("Не удалось записать файл модели: {error}"))?;
                hash.update(&bytes[..take]);
                *written += take as u64;
                bytes = &bytes[take..];
                if *written == entry.size {
                    let (file, hash, _) = current.take().ok_or("Ошибка состояния загрузки")?;
                    file.sync_all().map_err(|error| format!("Не удалось сохранить файл модели: {error}"))?;
                    if hex_hash(&hash.finalize()) != entry.sha256 { return Err("Контрольная сумма файла модели не совпадает".into()); }
                    index += 1;
                }
            }
        }
        if part_length != part.size || index != part.files.len() || current.is_some()
            || hex_hash(&part_hash.finalize()) != part.sha256
        {
            return Err("Размер или контрольная сумма части модели не совпадает".into());
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Value};

    fn manifest_value() -> Value {
        let files: Vec<_> = REQUIRED.iter().map(|name| json!({
            "name": name, "size": 1, "sha256": "a".repeat(64)
        })).collect();
        json!({
            "version": 1,
            "model_id": "v4_rnnt",
            "parts": [{
                "url": "https://github.com/dubr1k/GigaAMGUI/releases/download/model-v1/part.bin",
                "size": files.len(), "sha256": "b".repeat(64), "files": files,
            }]
        })
    }

    #[test]
    fn rejects_missing_pins_untrusted_urls_and_duplicate_names() {
        let model = ZapformerModel::V4Rnnt;
        assert!(parse_metadata("null", model).is_err());
        let value = manifest_value();
        assert!(parse_metadata(&value.to_string(), model).is_ok());
        let mut bad = value.clone();
        bad["parts"][0]["url"] = json!("https://example.com/fake");
        assert!(parse_metadata(&bad.to_string(), model).is_err());
        let mut bad = value.clone();
        bad["parts"][0]["files"][0]["name"] = json!("RNNT_ENCODER.ONNX");
        assert!(parse_metadata(&bad.to_string(), model).is_err());
        let mut bad = value.clone();
        bad["parts"][0]["sha256"] = json!("A".repeat(64));
        assert!(parse_metadata(&bad.to_string(), model).is_err());
        let mut bad = value;
        bad["parts"][0]["size"] = json!(MAX_PART + 1);
        assert!(parse_metadata(&bad.to_string(), model).is_err());
    }

    #[test]
    fn verifies_each_file_and_concatenated_part_and_detects_substitution() {
        let directory = tempfile::tempdir().unwrap();
        fs::write(directory.path().join("one"), b"abc").unwrap();
        fs::write(directory.path().join("two"), b"defg").unwrap();
        let hash = |bytes: &[u8]| hex_hash(&Sha256::digest(bytes));
        let manifest = Manifest {
            version: 1, model_id: "v4_rnnt".into(),
            parts: vec![Part {
                url: String::new(), size: 7, sha256: hash(b"abcdefg"),
                files: vec![
                    ModelFile { name: "one".into(), size: 3, sha256: hash(b"abc") },
                    ModelFile { name: "two".into(), size: 4, sha256: hash(b"defg") },
                ],
            }],
        };
        let cancelled = AtomicBool::new(false);
        assert!(verified_directory(directory.path(), &manifest, &cancelled).unwrap());
        fs::write(directory.path().join("two"), b"DEFG").unwrap();
        assert!(!verified_directory(directory.path(), &manifest, &cancelled).unwrap());
        cancelled.store(true, Ordering::Release);
        assert!(verified_directory(directory.path(), &manifest, &cancelled).is_err());
    }
}

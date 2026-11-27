use std::collections::HashSet;
use std::path::Path;

fn sha256(value: &serde_json::Value) -> bool {
    value.as_str().is_some_and(|s| s.len() == 64 && s.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)))
}

fn model_file_name(name: &str) -> bool {
    if name.is_empty() || name.len() > 255 || name.starts_with('.') || name.ends_with('.')
        || !name.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-')) {
        return false;
    }
    let stem = name.split('.').next().unwrap_or("").to_ascii_uppercase();
    !matches!(stem.as_str(), "CON" | "PRN" | "AUX" | "NUL" | "COM1" | "COM2" | "COM3" | "COM4" | "COM5" | "COM6" | "COM7" | "COM8" | "COM9" | "LPT1" | "LPT2" | "LPT3" | "LPT4" | "LPT5" | "LPT6" | "LPT7" | "LPT8" | "LPT9")
}

fn valid_asset_url(value: &str) -> bool {
    let Ok(url) = url::Url::parse(value) else { return false };
    if url.scheme() != "https" || url.host_str() != Some("github.com") || url.port().is_some()
        || !url.username().is_empty() || url.password().is_some()
        || url.query().is_some() || url.fragment().is_some() {
        return false;
    }
    let Some(path) = url.path().strip_prefix("/dubr1k/GigaAMGUI/releases/download/") else { return false };
    let mut segments = path.split('/');
    matches!((segments.next(), segments.next(), segments.next()),
        (Some(tag), Some(asset), None) if model_file_name(tag) && model_file_name(asset))
}

fn validate_model_metadata(bytes: &[u8], expected_id: &str) -> Result<(), String> {
    if bytes.len() > 2_000_000 { return Err("metadata exceeds size limit".into()); }
    let value: serde_json::Value = serde_json::from_slice(bytes).map_err(|e| e.to_string())?;
    if value["version"].as_u64() != Some(1) || value["model_id"].as_str() != Some(expected_id) {
        return Err("incorrect model id or metadata version".into());
    }
    let parts = value["parts"].as_array().ok_or("missing parts")?;
    if parts.is_empty() || parts.len() > 128 { return Err("invalid number of parts".into()); }
    let mut names = HashSet::new();
    let mut urls = HashSet::new();
    for part in parts {
        let location = part["url"].as_str().ok_or("missing part URL")?;
        if !valid_asset_url(location) || !urls.insert(location) || !sha256(&part["sha256"]) {
            return Err("invalid or duplicate part URL or SHA-256".into());
        }
        let size = part["size"].as_u64().filter(|&n| n > 0 && n <= 1_900_000_000).ok_or("invalid part size")?;
        let files = part["files"].as_array().filter(|v| !v.is_empty()).ok_or("empty part")?;
        let mut total = 0u64;
        for file in files {
            let name = file["name"].as_str().ok_or("missing file name")?;
            if !model_file_name(name) || !names.insert(name.to_ascii_lowercase())
                || names.len() > 4096 || !sha256(&file["sha256"]) {
                return Err("invalid or duplicate model file or SHA-256".into());
            }
            let file_size = file["size"].as_u64().filter(|&n| n > 0).ok_or("invalid file size")?;
            total = total.checked_add(file_size).ok_or("part size overflow")?;
        }
        if total != size { return Err("part size does not match its files".into()); }
    }
    for required in ["streaming.json", "rnnt_320ms_stream_frontend.onnx", "rnnt_320ms_stream_step.onnx",
        "rnnt_encoder.onnx", "rnnt_e2e_decoder.onnx", "rnnt_e2e_joint.onnx", "rnnt_e2e_tokenizer.model"] {
        if !names.contains(required) { return Err(format!("missing required model file {required}")); }
    }
    Ok(())
}

fn write_model_metadata(out: &Path, variable: &str, filename: &str, id: &str, required: bool) {
    println!("cargo:rerun-if-env-changed={variable}");
    let bytes = match std::env::var_os(variable) {
        Some(path) => {
            let path = std::path::PathBuf::from(path);
            println!("cargo:rerun-if-changed={}", path.display());
            let bytes = std::fs::read(&path).unwrap_or_else(|e| panic!("cannot read {variable}: {e}"));
            validate_model_metadata(&bytes, id).unwrap_or_else(|e| panic!("invalid {variable}: {e}"));
            bytes
        }
        None if required => panic!("{variable} is required for this release build"),
        None => b"null".to_vec(),
    };
    std::fs::write(out.join(filename), bytes).expect("failed to write model metadata");
}

fn main() {
    let manifest = "../../packaging/rust-liquid/generated/ffmpeg/SHA256SUMS";
    println!("cargo:rerun-if-changed={manifest}");
    let contents = match std::fs::read(manifest) {
        Ok(contents) => contents,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Vec::new(),
        Err(error) => panic!("failed to read FFmpeg manifest: {error}"),
    };
    let out = std::path::PathBuf::from(std::env::var_os("OUT_DIR").expect("OUT_DIR"));
    println!("cargo:rerun-if-env-changed=GIGAAM_REQUIRE_RUST_MODELS");
    let target = std::env::var("CARGO_CFG_TARGET_OS").unwrap_or_default();
    let required = matches!(target.as_str(), "windows" | "linux")
        && std::env::var("GIGAAM_REQUIRE_RUST_MODELS").as_deref() == Ok("1");
    write_model_metadata(&out, "GIGAAM_V4_ASSETS", "gigaam_v4_assets.json", "v4_rnnt", required);
    write_model_metadata(&out, "GIGAAM_MULTILINGUAL_V2_ASSETS", "gigaam_multilingual_v2_assets.json", "multilingual_v2_rnnt", required);
    std::fs::write(out.join("gigaam_ffmpeg_sha256sums"), contents)
        .expect("failed to write FFmpeg manifest");
    tauri_build::build()
}

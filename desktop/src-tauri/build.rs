fn main() {
    let manifest = "../../packaging/rust-liquid/generated/ffmpeg/SHA256SUMS";
    println!("cargo:rerun-if-changed={manifest}");
    let contents = match std::fs::read(manifest) {
        Ok(contents) => contents,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Vec::new(),
        Err(error) => panic!("failed to read FFmpeg manifest: {error}"),
    };
    let out = std::path::PathBuf::from(std::env::var_os("OUT_DIR").expect("OUT_DIR"));
    std::fs::write(out.join("gigaam_ffmpeg_sha256sums"), contents)
        .expect("failed to write FFmpeg manifest");
    tauri_build::build()
}

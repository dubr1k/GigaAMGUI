use std::{collections::HashMap, fs};

use serde_json::json;

use super::{args::*, headless::*};
use crate::settings::TuiSettings;

#[test]
fn data_directory_argument_accepts_separate_and_equals_forms() {
    assert_eq!(
        data_dir_from_args(["--data-dir", "/mnt/models"]),
        Ok(Some("/mnt/models".into()))
    );
    assert_eq!(
        data_dir_from_args(["--data-dir=/srv/gigaam"]),
        Ok(Some("/srv/gigaam".into()))
    );
    assert!(data_dir_from_args(["--data-dir"]).is_err());
    assert!(data_dir_from_args(["--data-dir", "--help"]).is_err());
}

#[cfg(unix)]
#[test]
fn a_non_utf8_argument_is_a_usage_error_not_a_panic() {
    use std::{ffi::OsString, os::unix::ffi::OsStringExt};
    let args = vec![
        OsString::from("transcribe"),
        OsString::from_vec(b"/tmp/\xff.wav".to_vec()),
    ];
    let error = utf8_args(args).unwrap_err();
    assert!(error.contains("/tmp/\u{fffd}.wav"), "{error}");
    assert_eq!(
        utf8_args(vec![OsString::from("llm"), OsString::from("a.txt")]).unwrap(),
        ["llm", "a.txt"]
    );
}

#[test]
fn strip_data_dir_removes_both_argument_forms() {
    let args = |list: &[&str]| list.iter().map(|s| s.to_string()).collect::<Vec<_>>();
    assert_eq!(
        strip_data_dir(args(&["--data-dir", "/x", "transcribe", "a.wav"])),
        args(&["transcribe", "a.wav"])
    );
    assert_eq!(
        strip_data_dir(args(&["transcribe", "--data-dir=/x", "a.wav"])),
        args(&["transcribe", "a.wav"])
    );
}

#[test]
fn headless_transcribe_args_override_settings() {
    let args: Vec<String> = [
        "transcribe",
        "/tmp/a.wav",
        "/tmp/b.mp3",
        "--formats",
        "txt,srt",
        "--diarize",
        "--speakers",
        "2",
        "--backend",
        "onnx",
        "--audio-mode",
        "denoise",
        "--output",
        "/tmp/out",
        "--json",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    let HeadlessCommand::Transcribe(parsed) = parse_headless_args(&args).unwrap() else {
        panic!("transcribe")
    };
    let settings = TuiSettings {
        backend: "mlx".into(),
        model: "multilingual_ctc".into(),
        ..TuiSettings::default()
    };
    let payload = headless_start_payload(&settings, &parsed);
    assert_eq!(payload["type"], "start");
    assert_eq!(payload["files"], json!(["/tmp/a.wav", "/tmp/b.mp3"]));
    assert_eq!(payload["formats"], json!(["txt", "srt"]));
    assert_eq!(payload["diarization"], true);
    assert_eq!(payload["num_speakers"], 2);
    assert_eq!(payload["backend"], "onnx", "flag overrides settings");
    assert_eq!(
        payload["model"], "multilingual_ctc",
        "settings fill what flags omit"
    );
    assert_eq!(payload["audio_preprocessing_mode"], "denoise");
    assert_eq!(payload["output_dir"], "/tmp/out");
    assert!(parsed.json);
}

#[test]
fn headless_args_reject_unknown_flags_and_missing_files() {
    let args = |list: &[&str]| list.iter().map(|s| s.to_string()).collect::<Vec<_>>();
    assert!(parse_headless_args(&args(&["transcribe"]))
        .unwrap_err()
        .contains("at least one file"));
    assert!(
        parse_headless_args(&args(&["transcribe", "a.wav", "--bogus"]))
            .unwrap_err()
            .contains("--bogus")
    );
    assert!(
        parse_headless_args(&args(&["transcribe", "a.wav", "--audio-mode", "loud"]))
            .unwrap_err()
            .contains("audio-mode")
    );
    assert!(parse_headless_args(&args(&["llm", "a.txt"]))
        .unwrap_err()
        .contains("--mode"));
    assert!(
        parse_headless_args(&args(&["llm", "a.txt", "--mode", "custom"]))
            .unwrap_err()
            .contains("--prompt")
    );
    assert!(
        parse_headless_args(&args(&["--data-dir", "/x"])).is_err(),
        "not a headless command"
    );
}

#[test]
fn headless_llm_payload_uses_saved_provider_settings() {
    let args = |list: &[&str]| list.iter().map(|s| s.to_string()).collect::<Vec<_>>();
    let HeadlessCommand::Llm(parsed) = parse_headless_args(&args(&[
        "llm",
        "/tmp/a.txt",
        "--mode",
        "summary",
        "--mode",
        "custom",
        "--prompt",
        "Why?",
    ]))
    .unwrap() else {
        panic!("llm")
    };
    let settings = TuiSettings {
        llm_provider: "Other".into(),
        llm_tool_paths: HashMap::from([("other".to_string(), "/bin/cat".to_string())]),
        llm_extra_args: HashMap::from([("other".to_string(), "-".to_string())]),
        ..TuiSettings::default()
    };
    let payload = headless_llm_payload(&settings, &parsed);
    assert_eq!(payload["type"], "llm_start");
    assert_eq!(payload["files"], json!(["/tmp/a.txt"]));
    assert_eq!(payload["modes"], json!(["summary", "custom"]));
    assert_eq!(payload["prompt"], "Why?");
    assert_eq!(payload["settings"]["provider"], "Other");
    assert_eq!(payload["settings"]["other_path"], "/bin/cat");
    assert_eq!(payload["settings"]["other_args"], "-");
    assert_eq!(payload["settings"]["claude_path"], "claude");
}

#[test]
fn headless_human_lines_name_saved_files_and_errors() {
    let done = json!({"type":"file_completed","file":"/tmp/a.wav","result":{"success":true,"saved_files":["/tmp/a.txt","/tmp/a.srt"]}});
    assert_eq!(
        format_headless_line(&done).unwrap(),
        "✓ a.wav → /tmp/a.txt, /tmp/a.srt"
    );
    let failed = json!({"type":"file_completed","file":"/tmp/b.mp3","result":{"success":false,"error":"boom","saved_files":[]}});
    assert_eq!(format_headless_line(&failed).unwrap(), "× b.mp3: boom");
    let error = json!({"type":"error","message":"Input file does not exist: /tmp/c.wav"});
    assert_eq!(
        format_headless_line(&error).unwrap(),
        "error: Input file does not exist: /tmp/c.wav"
    );
    assert!(format_headless_line(&json!({"type":"progress","stage":"asr"})).is_none());
}

#[test]
fn headless_paths_become_absolute_before_the_worker_sees_them() {
    // The worker runs with cwd = the repo checkout, so relative paths must be
    // resolved by the binary against the caller's cwd.
    let directory =
        std::env::temp_dir().join(format!("gigaam-headless-paths-{}", std::process::id()));
    let _ = fs::remove_dir_all(&directory);
    fs::create_dir_all(&directory).unwrap();
    fs::write(directory.join("a.wav"), b"").unwrap();
    let args = |list: &[&str]| list.iter().map(|s| s.to_string()).collect::<Vec<_>>();
    let relative_file = format!("{}/./a.wav", directory.display());
    let relative_out = format!("{}/./out", directory.display());
    let mut command = parse_headless_args(&args(&[
        "transcribe",
        &relative_file,
        "--output",
        &relative_out,
    ]))
    .unwrap();
    resolve_headless_paths(&mut command).unwrap();
    let HeadlessCommand::Transcribe(parsed) = &command else {
        panic!("transcribe")
    };
    let canonical = crate::results::canonical_path(&directory).unwrap();
    assert_eq!(
        parsed.files,
        vec![canonical.join("a.wav").to_string_lossy().into_owned()]
    );
    assert_eq!(
        parsed.output_dir.as_deref(),
        Some(canonical.join("out").to_string_lossy().as_ref()),
        "output directory is created and canonicalised"
    );
    let mut llm =
        parse_headless_args(&args(&["llm", &relative_file, "--mode", "summary"])).unwrap();
    resolve_headless_paths(&mut llm).unwrap();
    let HeadlessCommand::Llm(parsed) = &llm else {
        panic!("llm")
    };
    assert_eq!(
        parsed.files,
        vec![canonical.join("a.wav").to_string_lossy().into_owned()]
    );
    let mut missing = parse_headless_args(&args(&[
        "transcribe",
        &format!("{}/missing.wav", directory.display()),
    ]))
    .unwrap();
    assert!(resolve_headless_paths(&mut missing)
        .unwrap_err()
        .contains("missing.wav"));
    let _ = fs::remove_dir_all(&directory);
}

#[test]
fn headless_prompt_requires_custom_mode() {
    let args = |list: &[&str]| list.iter().map(|s| s.to_string()).collect::<Vec<_>>();
    let error = parse_headless_args(&args(&[
        "llm", "a.txt", "--mode", "summary", "--prompt", "x",
    ]))
    .unwrap_err();
    assert!(error.contains("--prompt requires --mode custom"), "{error}");
    assert!(parse_headless_args(&args(&[
        "llm", "a.txt", "--mode", "custom", "--prompt", "x"
    ]))
    .is_ok());
}

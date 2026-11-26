use std::path::Path;

use gigastt_core::inference::{Engine, TranscribeRequest, TranscribeSource};
use gigastt_core::model::{self, ModelVariant};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args_os().skip(1);
    let model_dir = args.next().ok_or("expected model directory and audio file")?;
    let audio = args.next().ok_or("expected model directory and audio file")?;
    if args.next().is_some() {
        return Err("expected model directory and audio file".into());
    }
    let model_dir = Path::new(&model_dir).to_str().ok_or("model path is not UTF-8")?;
    let audio = Path::new(&audio).to_str().ok_or("audio path is not UTF-8")?;
    let variant = ModelVariant::E2eRnnt;
    tauri::async_runtime::block_on(model::ensure_model_variant(Some(variant), model_dir))?;
    let engine = Engine::load_with_execution_provider(
        model_dir, Some(variant), 1, 1, 0, 2, None,
        gigastt_core::ExecutionProviderChoice::Cpu,
    )?;
    let mut session = engine.pool.checkout_blocking()?;
    let result = engine.transcribe_request(
        TranscribeRequest::new(TranscribeSource::Path(audio)),
        &mut session,
    )?;
    if result.text.trim().is_empty() {
        return Err("fixture produced an empty transcript".into());
    }
    println!("fixture transcript: {}", result.text);
    Ok(())
}

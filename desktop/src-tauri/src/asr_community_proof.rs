//! Standalone Community-1 proof entry point. Compile this file as the binary of
//! a separate crate with the same audio/tract dependencies as the desktop app;
//! it intentionally does not depend on Tauri or bundle gated model weights.
//!
//! Usage: community-proof EXPORTED_BUNDLE MONO_16KHZ_WAVE
//! Output: `COMMUNITY_REGION <start_sample> <end_sample> <speaker|OVERLAP>`.
//! All results are provisional until the process exits successfully.

#[path = "asr_long_audio.rs"]
mod audio;
#[path = "asr_long_embedder.rs"]
mod embedder;
#[path = "asr_long_identity.rs"]
mod identity;
#[path = "asr_long_pipeline.rs"]
mod pipeline;
#[path = "asr_community.rs"]
mod community;

use std::path::Path;
use std::sync::atomic::AtomicBool;

fn run() -> Result<(), String> {
    let mut args = std::env::args_os().skip(1);
    let bundle = args.next().ok_or("expected EXPORTED_BUNDLE and MONO_16KHZ_WAVE")?;
    let wav = args.next().ok_or("expected EXPORTED_BUNDLE and MONO_16KHZ_WAVE")?;
    if args.next().is_some() { return Err("expected exactly two arguments".into()); }
    let flag = AtomicBool::new(false);
    community::require_bundle(Path::new(&bundle))?;
    let mut samples = 0u64;
    audio::for_each_window(Path::new(&wav), 16_000, &flag, |start, chunk| {
        if start != samples { return Err("non-contiguous WAVE timeline".into()); }
        samples = samples.checked_add(chunk.len() as u64).ok_or("audio is too long")?;
        Ok(())
    }).map_err(|error| format!("read WAVE: {error}"))?;
    let mut processor = community::Pipeline::load(Path::new(&bundle), &flag)?;
    let mut regions = Vec::new();
    let mut progress = 0.0f64;
    processor.diarize(Path::new(&wav), samples, &flag,
        |fraction| progress = fraction,
        |region| { regions.push(region); Ok(()) })?;
    for region in regions {
        let kind = match region.kind {
            pipeline::RegionKind::Speaker(id) => format!("SPEAKER_{id:02}"),
            pipeline::RegionKind::Unknown => "UNKNOWN".to_string(),
            pipeline::RegionKind::Overlap => "OVERLAP".to_string(),
        };
        println!("COMMUNITY_REGION {} {} {}", region.start_sample, region.end_sample, kind);
    }
    eprintln!("processed {samples} samples; inference progress {:.0}%", progress * 100.0);
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("community proof failed: {error}");
        std::process::exit(1);
    }
}

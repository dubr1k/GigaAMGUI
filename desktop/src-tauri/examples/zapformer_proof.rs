//! CPU proof for the app's Zapformer adapters. Use trusted, immutable model exports.
//!
//! cargo run --release --example zapformer_proof -- v4 MODEL_DIR INPUT.wav
//! cargo run --release --example zapformer_proof -- --stream multilingual MODEL_DIR STREAM_DIR INPUT.wav 1600
//!
//! The WAV must be mono 16 kHz PCM16. Full-context input is limited to 30 seconds.
//! Streaming packet length is in samples, defaults to 1600, and may be 1..=160000.

#[cfg(any(target_os = "windows", target_os = "linux"))]
#[path = "../src/asr_zapformer.rs"]
mod asr_zapformer;
#[cfg(any(target_os = "windows", target_os = "linux"))]
#[path = "../src/asr_zapformer_stream.rs"]
mod asr_zapformer_stream;

#[cfg(any(target_os = "windows", target_os = "linux"))]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    use std::{path::Path, sync::atomic::AtomicBool};
    use asr_zapformer::{Zapformer, ZapformerModel};
    use asr_zapformer_stream::{StreamUpdate, ZapformerStream};
    use gigastt_core::inference::WordInfo;

    fn append_update(text: &mut String, words: &mut Vec<WordInfo>, update: StreamUpdate) {
        eprintln!("COMMIT {:?}", update.text);
        for word in &update.words {
            eprintln!("WORD {} {:.3} {:.3}", word.word, word.start, word.end);
        }
        text.push_str(&update.text);
        words.extend(update.words);
        if let Some(partial) = update.partial {
            eprintln!("PARTIAL {partial:?}");
        }
    }

    let mut args = std::env::args().skip(1);
    let first = args.next().ok_or("expected model or --stream")?;
    let streaming = first == "--stream";
    let selected = if streaming { args.next().ok_or("expected model")? } else { first };
    let model = match selected.as_str() {
        "v4" => ZapformerModel::V4Rnnt,
        "multilingual" => ZapformerModel::MultilingualV2Rnnt,
        _ => return Err("expected v4 or multilingual".into()),
    };
    let dir = args.next().ok_or("expected model directory")?;
    let stream_dir = if streaming {
        Some(args.next().ok_or("expected streaming export directory")?)
    } else {
        None
    };
    let file = args.next().ok_or("expected 16k mono WAV")?;
    let packet_samples = match args.next() {
        Some(value) if streaming => value.parse::<usize>().map_err(|_| "invalid packet_samples")?,
        Some(_) => return Err("unexpected extra argument".into()),
        None => 1600,
    };
    if !(1..=160_000).contains(&packet_samples) || args.next().is_some() {
        return Err("packet_samples must be between 1 and 160000, with no extra arguments".into());
    }
    let mut wav = hound::WavReader::open(file)?;
    let spec = wav.spec();
    if spec.sample_rate != 16_000 || spec.channels != 1 || spec.bits_per_sample != 16
        || spec.sample_format != hound::SampleFormat::Int
    {
        return Err("expected 16k mono PCM16 WAV".into());
    }
    let cancelled = AtomicBool::new(false);
    if let Some(stream_dir) = stream_dir {
        let mut stream = ZapformerStream::load(Path::new(&dir), Path::new(&stream_dir), model, 2, &cancelled)?;
        let mut text = String::new();
        let mut words = Vec::new();
        let mut packet = Vec::with_capacity(packet_samples);
        for sample in wav.samples::<i16>() {
            packet.push(sample? as f32 / 32768.0);
            if packet.len() == packet_samples {
                append_update(&mut text, &mut words, stream.push(&packet, &cancelled)?);
                packet.clear();
            }
        }
        if !packet.is_empty() {
            append_update(&mut text, &mut words, stream.push(&packet, &cancelled)?);
        }
        append_update(&mut text, &mut words, stream.finish(&cancelled)?);
        println!("{text}");
        for word in words {
            println!("{} {:.3} {:.3}", word.word, word.start, word.end);
        }
    } else {
        let pcm: Vec<f32> = wav.samples::<i16>()
            .map(|sample| sample.map(|value| value as f32 / 32768.0))
            .collect::<Result<_, _>>()?;
        let result = Zapformer::load(Path::new(&dir), model, 2, &cancelled)?.transcribe(&pcm, &cancelled)?;
        println!("{}", result.text);
        for word in result.words {
            println!("{} {:.3} {:.3}", word.word, word.start, word.end);
        }
    }
    Ok(())
}

#[cfg(not(any(target_os = "windows", target_os = "linux")))]
fn main() {
    eprintln!("zapformer_proof requires a Linux or Windows target");
    std::process::exit(1);
}

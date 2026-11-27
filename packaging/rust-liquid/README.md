# Rust Liquid build candidates

## Experimental Community-1 export

Community-1 is experimental and requires a locally prepared, authorized model
bundle; the Rust application does not download it. Install the pinned build-only
requirements in a separate Python 3.11 environment without replacing the
application's existing pyannote dependencies:

```sh
python packaging/rust-liquid/community-export/export.py --snapshot SNAPSHOT --output NEW_EXPORT_DIR
python packaging/rust-liquid/community-export/quantize.py --model-dir NEW_EXPORT_DIR --calibration-dir CALIBRATION --output-dir NEW_INT8_DIR
```

Calibration requires representative `.npz` batches in `segmentation/`
(`waveforms`) and `embedding/` (`fbank`, `mask`); tensor shapes and frontend
conventions are described in `quantize.py`. The output is a complete local
runtime bundle with quantized graphs and an `fp32/` copy of the original files.
Segmentation quantizes linear operators only for Tract compatibility. The
calibration check uses ONNX Runtime, not the application's Tract runtime; it
does not establish diarization quality, pipeline parity, speed, or Windows/Linux
compatibility. Checkpoints are executable trusted inputs.

The installed-app UI proof accepts `--community-missing` to verify the error
without weights, or `--community-bundle PATH` to copy an authorized local export
into a dedicated proof app-data directory. The latter requires a fixture with two
separated speakers and at least two distinct numbered labels in the output; this
is a smoke check, not an assertion of speaker accuracy.
The latter does not download or upload model files. On Windows the dedicated
root must also be connected to the application's real known folders, as in the
existing CI Windows isolation step; merely setting `APPDATA` is insufficient.

`.github/workflows/rust-liquid.yml` builds the Tauri client independently of
`build.yml`. It uploads `rust-liquid-linux-x64-candidate` and
`rust-liquid-windows-x64-candidate` workflow artifacts, not GitHub release
assets. Their filenames have the `GigaAMGUI-Rust-Liquid-` prefix and do not
match the existing portable or macOS Liquid filenames. The artifact names also
do not match `publish-release.yml`'s `GigaAM*` selection. Nothing in this
workflow publishes or changes a release.

The common Tauri overlay gives this build its own product name and application
identifier. Icons are generated from `assets/icon-liquid.png` under the ignored
`generated/` directory; neither the original Tauri configuration nor the UI is
modified. The Windows overlay embeds the offline WebView2 installer and puts
DirectML and the x64 Visual C++ CRT beside the application. The Linux overlay
adds GNU libstdc++ and ALSA to the AppImage and enables the GStreamer bundle.

## Bundled FFmpeg

The candidate packages include an audio-only FFmpeg 9.0.2 executable and its
corresponding source and notices. The source archive is pinned to SHA-256
`8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e`;
the build also verifies the official detached signature against the release
key fingerprint `FCF986EA15E6E293A5644F10B4322F04D67658D8`.
No GPL, nonfree, network protocol or external codec library is enabled.
The enabled native decoders include Opus, Vorbis, MP3, AAC, WMA, FLAC,
ALAC, common PCM/ADPCM, AC-3, E-AC-3 and DTS; the demuxers include
WAV, MP3, MP4/MOV, Matroska/WebM, Ogg, FLAC, AAC, AVI and ASF.
This is not a claim that every codec in those containers is supported.

`build-ffmpeg.sh` builds Linux and Windows x64 on an amd64 Linux Docker host
using a digest-pinned Debian image and a dated, signed Debian package snapshot.
It builds each binary twice in isolated, network-disabled containers and fails
unless the two files match byte for byte. This verifies repeatability in that
pinned environment, not independent reproducibility on every host. The
packages include the unmodified FFmpeg source, signature, signing key,
build scripts, toolchain package versions, LGPL text and dependency notices.
The Windows package also includes the exact MinGW source and runtime notice.
FFmpeg is a separate process, statically linked with its own libraries; Linux
uses the host glibc (the build baseline is Debian 12 / glibc 2.36), while the
Windows executable imports only checked Windows system DLLs. Patent and
redistribution obligations still require review before publication.

Resolve `ffmpeg/ffmpeg` or `ffmpeg/ffmpeg.exe` relative to Tauri's
`resource_dir()`, never from `PATH`. `generated/ffmpeg/SHA256SUMS` is created
before Cargo builds and contains one line, `<lowercase SHA-256>  <filename>`.
The same file is bundled, but the application must use its compile-time copy as
the authority and verify the executable before launch. A hash cannot stop
replacement after verification or an attacker who can replace the application
itself. The executable directory must not be writable by other users. On
Windows, the child loader still searches the executable directory and may
search the current directory unless the parent establishes an appropriate
inherited DLL search policy (for example `SetDllDirectoryW(L"")`); changing
that process-wide policy must be coordinated with other threads. On Linux,
the child must not inherit untrusted loader variables such as `LD_PRELOAD`
or `LD_LIBRARY_PATH`. System loaders and libraries remain part of the trust
boundary; this does not sandbox hostile media.

CI additionally generates small WAV, MP3, AAC, MP4/MOV/3GP, AVI, Matroska,
WebM/Opus, Ogg/Vorbis, Opus, ASF/WMA and FLAC fixtures and runs the actual
decoder command on both target operating systems before packaging. It compares
the installed executable with the checked build and checks the Windows import
table. These checks do not replace clean-machine and longer real-media tests.

## Prerequisites and limitations

- Rust stable must satisfy the inference crate's minimum Rust 1.94. Both Cargo
  and npm lockfiles must be updated and committed after dependency changes;
  CI deliberately uses `--locked` and `npm ci`.
- The workflow targets the current `gigastt-core` / ort static-link setup and
  fails if `ort/load-dynamic` is enabled. Changing to a dynamic ONNX Runtime
  requires packaging and testing the correct DLL/SO, its loader path, dependent
  libraries, and its version on each platform.
- The ort prebuilt x86-64 ONNX Runtime requires **x86-64-v3** CPUs, not every
  x64 processor. The candidate is built on Ubuntu 24.04; compatibility with
  older glibc releases and other distributions has not been established.
  AppImage execution normally needs FUSE, or its extraction-based alternative.
  Linux also requires host PulseAudio client libraries and an EGL/OpenGL ES
  stack (on Ubuntu, `libpulse0`, `libegl1` and `libgles2`).
  PulseAudio libraries are deliberately excluded from the bundle to avoid a
  loader crash observed with the bundled copy.
- Models are not included. They must be downloaded by the application on first
  use; an installer with offline WebView2 is not an offline model bundle.
- Automated checks inspect extracted packages, native dependencies, CPU
  architecture and Python runtime filenames. They do not prove a clean-machine
  installation, GUI operation, inference, microphone permissions, GPU support,
  full multimedia codec coverage or all transitive dependencies. The Windows
  installer is not code-signed by this workflow.
- A separate one-off CI smoke installed the Windows candidate and launched it
  for 10 seconds; the extracted Linux candidate ran for 10 seconds under Xvfb
  on Ubuntu 24.04 with the host libraries above. Neither check exercised audio,
  inference, or interactive GUI operation.
- On candidate build run 45833159438, a separate UI proof installed the Windows
  package and extracted the Linux AppImage, selected a WAV and output directory
  in the real interface, and verified the saved nonempty TXT against the visible
  transcript on both platforms. This does not cover live audio or diarization.
- Treat these as candidates until both platforms have been tested from the
  actual packages on clean systems, including model download and real audio
  transcription. Review third-party redistribution notices before publication.

## CI verification after integration

Run the workflow on the integrated branch through GitHub Actions, or use:

```sh
gh workflow run rust-liquid.yml --ref feat/rust-liquid-windows-linux
```

The jobs install the platform prerequisites, generate icons, check JavaScript,
run `cargo test --locked`, download the model and perform CPU inference on a
short fixture on each native runner, build with Tauri and `--locked`, then inspect
the extracted NSIS/AppImage before uploading a candidate. The inference check
uses the same engine dependency as the app, not the installed application UI.

For a focused local Linux build on a machine with the packages in the workflow:

```sh
bash packaging/rust-liquid/build-ffmpeg.sh
cp -R packaging/rust-liquid/generated/ffmpeg-linux-x64 packaging/rust-liquid/generated/ffmpeg
cd desktop
npm ci
npx --no-install tauri icon ../assets/icon-liquid.png --output ../packaging/rust-liquid/generated/icons
export TAURI_CONFIG="$(cat ../packaging/rust-liquid/tauri.conf.json)"
cargo test --locked --manifest-path src-tauri/Cargo.toml --target x86_64-unknown-linux-gnu
unset TAURI_CONFIG
npm run build -- --no-bundle --target x86_64-unknown-linux-gnu --config ../packaging/rust-liquid/tauri.conf.json -- --locked
mkdir -p "$HOME/.cache/tauri"
tool="$HOME/.cache/tauri/linuxdeploy-x86_64.AppImage"
curl -fL https://github.com/tauri-apps/binary-releases/releases/download/linuxdeploy-07333c6/linuxdeploy-x86_64.AppImage -o "$tool"
echo "36a2d7e274d12e1050d0e9ecfe11d339ed54720b2bec464c286d53f8b07f5c62  $tool" | sha256sum -c -
chmod +x "$tool"
export LINUXDEPLOY_EXCLUDED_LIBRARIES='libpulse.so.0;libpulsecommon-*.so'
mkdir -p ../packaging/rust-liquid/generated/linux
cp -L /usr/lib/x86_64-linux-gnu/libstdc++.so.6 ../packaging/rust-liquid/generated/linux/libstdc++.so.6
cp -L /usr/lib/x86_64-linux-gnu/libasound.so.2 ../packaging/rust-liquid/generated/linux/libasound.so.2
npx --no-install tauri bundle --target x86_64-unknown-linux-gnu --bundles appimage --config ../packaging/rust-liquid/tauri.conf.json --config ../packaging/rust-liquid/tauri.linux.conf.json
```

Windows packaging additionally stages the DirectML DLL from the compiled ort
output and the VC redistributable from Visual Studio, as specified in the
workflow. Run the Windows job for the matching extraction and import-table
checks; merely compiling the executable does not validate its installer.

## Packaged long-diarization proof

`long-ui-proof.py` is a separate proof. It drives the **installed** Windows
executable or the extracted Linux AppImage through the real UI and native file
dialogs. Python is only an external controller and fixture builder; it is not
an application backend. `rust-liquid-ui-proof.yml` keeps its existing short
proof, then prepares a dedicated model cache and runs this additional proof on
both native targets. Provisioning downloads models; a successful workflow run is
required before claiming a result.

The controller creates a 30-minute-plus 16 kHz WAV from the offline excerpts
in `tests/fixtures/rust_liquid_long_sources.json`. They are `clean/validation`
(`dev-clean`) utterances 2035-147960-0005/0006, 7976-110124-0023/0024 and
1673-143397-0002/0003 from
[OpenSLR LibriSpeech](https://huggingface.co/datasets/openslr/librispeech_asr),
revision `71cacbfb7e2354c4226d01e70d77d5fca3d04ba1`. LibriSpeech is
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/): Vassil Panayotov,
Guoguo Chen, Daniel Povey and Sanjeev Khudanpur, *LibriSpeech: An ASR Corpus
Based on Public Domain Audio Books*, ICASSP 2015,
https://www.danielpovey.com/files/2015_icassp_librispeech.pdf.
The JSON records the original FLAC hashes and the hashes of decoded excerpts;
the controller independently checks the latter. Each excerpt is at most four
seconds, decoded to mono signed 16-bit PCM at 16 kHz and padded to a 20 ms
boundary. The resulting fixture attenuates the solo clips to three-quarters,
adds silence, halves and mixes two excerpts with a 300 ms offset, and repeats
one excerpt after 30 minutes. It is a derivative; retain attribution.

Before running, prepare a **dedicated, proof-owned** application-data root on
the target, with the app's `multilingual_ctc` ASR and WeSpeaker model installed.
The selected model directory is
`<root>/data/ru.gigaam.gui.rustliquid/models/multilingual_ctc` on Linux and
`<root>\roaming\ru.gigaam.gui.rustliquid\models\multilingual_ctc` on Windows.
Also provision the exact
[`powerset_fp32_tract.onnx`](https://github.com/ekhodzitsky/polyvoice/releases/download/models-tract-v1/powerset_fp32_tract.onnx)
there; SHA-256 is
`d5335de334a24470b1f0326f72e513c59e156308a78ba3e1626851f570696ce4`.
The controller checks this hash and the bundled pipeline's pinned WeSpeaker
hash, and asks the application read-only whether its ASR model is installed.
It refuses a pre-existing `gigaam-private-*` scratch directory. It does not
copy or remove models or the supplied app-data root. Provisioning may need
network access; the proof itself needs no fixture download. For an offline
claim, block application outbound access externally while retaining loopback
WebDriver access; preflight checks alone do not enforce network isolation.

On Linux use the same GUI dependencies, `tauri-driver`, extracted `AppRun`, Xvfb
and Openbox as `rust-liquid-ui-proof.yml`. With `PROOF_APP` and
`PROOF_APP_DATA` set to absolute paths:

```sh
dbus-run-session -- xvfb-run -a -s '-screen 0 1600x1200x24' sh -c '
  openbox > "$RUNNER_TEMP/rust-liquid-openbox.log" 2>&1 &
  exec /usr/bin/python3 packaging/rust-liquid/long-ui-proof.py \
    --app "$PROOF_APP" --app-data "$PROOF_APP_DATA" \
    --diagnostics "$GITHUB_WORKSPACE/long-proof-diagnostics" --timeout 3600
'
```

On Windows use the installed NSIS executable, matching Edge WebDriver,
`tauri-driver`, `pywinauto` and an accessible input desktop:

```powershell
python packaging/rust-liquid/long-ui-proof.py `
  --app "$env:PROOF_APP" --app-data "$env:PROOF_APP_DATA" `
  --diagnostics "$env:GITHUB_WORKSPACE/long-proof-diagnostics" --timeout 3600
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

`--sources` may point to the same fixture at the candidate's checked-out source
revision. The controller cancels one real long-path job after seeing private
scratch, checks cancellation and cleanup, then reruns it to completion. It
checks TXT against the visible UI and its listed paths, JSON word chronology
and statuses, two distinct source speakers, the same-speaker return including
after 30 minutes, and SRT late timestamps and ambiguous labels. At least one
recognized word must be marked overlap where both independently sourced signals
are energetic for at least 80% of its duration; such words must not be forced
to one speaker. The energy reference uses 20 ms source frames with RMS at least
600 in unattenuated PCM: it is a proxy, not human speech annotation or DER.
The excerpt and model windows were previously used in development and are not
an independent holdout. Most of the recording is silence: success would not
prove general diarization quality, continuous-hour memory/runtime behavior,
GPU operation, or every cancellation/error cleanup path. A failure is evidence
to investigate, not permission to relax the checks. If forced termination leaves
scratch, inspect it in the dedicated app-data root; the controller does not
silently delete it.

The small oracle-regression checks, which do **not** execute a packaged app, are:

```sh
python -m pytest -q tests/test_rust_liquid_long_proof.py
```

# Rust Liquid build candidates

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

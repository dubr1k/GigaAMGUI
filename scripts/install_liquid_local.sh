#!/usr/bin/env bash
# Build GigaAM Liquid locally the way .github/workflows/build.yml does
# (build-macos-full "Build and archive Liquid worker" + build-macos-swift
# "Assemble and verify offline native Swift archive") and install it into
# /Applications. No waiting for GitHub Actions: the same version checks and
# smoke gates run here.
#
#   scripts/install_liquid_local.sh                # rebuild worker + Swift, install
#   SKIP_WORKER=1 scripts/install_liquid_local.sh  # reuse dist/GigaAMWorker.app
#   NO_INSTALL=1 scripts/install_liquid_local.sh   # stop after the staged bundle
#
# Models for the offline bundle come from MODELS_DIR, else the installed app,
# else offline/models/hf, else scripts/build_offline_models.py downloads them.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
TARGET="${TARGET:-/Applications/GigaAMLiquid.app}"
# Outside the repo: the checkout is a Syncthing folder, a 2 GB stage would sync.
STAGE="${STAGE:-${TMPDIR:-/tmp}/gigaam-liquid-local}"
WORKER="$ROOT/dist/GigaAMWorker.app"

VERSION=$(sed -n 's/^APP_VERSION = "\([^"]*\)"/\1/p' packaging/_spec_common.py)
BUILD_VERSION=$(sed -n 's/^APP_BUILD_VERSION = "\([^"]*\)"/\1/p' packaging/_spec_common.py)
MARKETING_VERSION="${VERSION%%-*}"
[[ "$MARKETING_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Некорректная версия: $VERSION" >&2; exit 1; }
[[ "$BUILD_VERSION" =~ ^[1-9][0-9]{0,3}\.[0-9]{1,2}\.[0-9]{1,2}$ ]] || { echo "Некорректный номер сборки: $BUILD_VERSION" >&2; exit 1; }
echo "==> GigaAM Liquid $VERSION ($BUILD_VERSION)"

plist() { /usr/libexec/PlistBuddy -c "Print :$2" "$1/Contents/Info.plist"; }

if [[ "${SKIP_WORKER:-0}" != 1 ]]; then
  echo "==> Worker (PyInstaller, ~10 min)"
  GIGAAM_BUNDLE_SORTFORMER=1 GIGAAM_NATIVE_WORKER=1 "$PYTHON" -m PyInstaller \
    packaging/gigaam_app_mac.spec --noconfirm --workpath build/gigaam_worker_mac
fi
test -x "$WORKER/Contents/MacOS/GigaAMWorker"
test "$(plist "$WORKER" GigaAMReleaseVersion)" = "$VERSION" || {
  echo "Worker собран как $(plist "$WORKER" GigaAMReleaseVersion), исходники — $VERSION; пересоберите без SKIP_WORKER" >&2
  exit 1
}
test ! -e "$WORKER/Contents/Frameworks/PyQt6"
test ! -e "$WORKER/Contents/Resources/PyQt6"

echo "==> Swift client (release)"
swift build -c release --package-path macos/GigaAMLiquid
EXECUTABLE="macos/GigaAMLiquid/.build/release/GigaAMLiquid"
test -x "$EXECUTABLE"

MODELS="${MODELS_DIR:-}"
if [[ -z "$MODELS" ]]; then
  for candidate in "$TARGET/Contents/Resources/models/hf" "$ROOT/offline/models/hf"; do
    if [[ -d "$candidate" && -n "$(find "$candidate" -type f -print -quit)" ]]; then MODELS="$candidate"; break; fi
  done
fi
if [[ -z "$MODELS" ]]; then
  echo "==> Offline models (download)"
  "$PYTHON" scripts/build_offline_models.py --output "$ROOT/offline/models/hf"
  MODELS="$ROOT/offline/models/hf"
fi
echo "==> Models from $MODELS"

echo "==> Assemble bundle"
APP="$STAGE/GigaAMLiquid.app"
RESOURCES="$APP/Contents/Resources"
rm -rf "$STAGE"
mkdir -p "$APP/Contents/MacOS" "$RESOURCES/models"
cp "$EXECUTABLE" "$APP/Contents/MacOS/GigaAMLiquid"
chmod +x "$APP/Contents/MacOS/GigaAMLiquid"
cp assets/icon-liquid.icns "$RESOURCES/GigaAMLiquid.icns"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>ru</string>
  <key>CFBundleExecutable</key><string>GigaAMLiquid</string>
  <key>CFBundleIdentifier</key><string>ru.dubr1k.gigaam-liquid</string>
  <key>CFBundleIconFile</key><string>GigaAMLiquid.icns</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>GigaAMLiquid</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$MARKETING_VERSION</string>
  <key>GigaAMReleaseVersion</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$BUILD_VERSION</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>GigaAMLiquid uses microphone access for live transcription.</string>
</dict>
</plist>
PLIST
ditto "$WORKER" "$RESOURCES/GigaAMWorker.app"
ditto "$MODELS" "$RESOURCES/models/hf"

COMPANION="$RESOURCES/GigaAMWorker.app/Contents/MacOS/GigaAMWorker"
for key in GigaAMReleaseVersion CFBundleShortVersionString CFBundleVersion; do
  worker_value=$(plist "$RESOURCES/GigaAMWorker.app" "$key")
  liquid_value=$(plist "$APP" "$key")
  test "$worker_value" = "$liquid_value" || { echo "Worker $key ($worker_value) не совпадает с Liquid ($liquid_value)" >&2; exit 1; }
done
test ! -e "$RESOURCES/app.py"
test ! -e "$RESOURCES/src"
test "$(lipo -archs "$APP/Contents/MacOS/GigaAMLiquid")" = "arm64"
test "$(lipo -archs "$COMPANION")" = "arm64"

codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

echo "==> Smoke: ping, file, live (clean HF cache, offline)"
SMOKE="$STAGE/smoke"
mkdir -p "$SMOKE"
printf '{"type":"ping"}\n' | "$COMPANION" --native-worker > "$SMOKE/ping.jsonl"
grep -q '"type": "pong"' "$SMOKE/ping.jsonl"
HF_HOME="$SMOKE/hf" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  "$PYTHON" scripts/native_worker_smoke.py "$COMPANION" tests/fixtures/liquid_smoke.wav "$SMOKE/out"
HF_HOME="$SMOKE/hf" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  "$PYTHON" scripts/native_worker_smoke.py --live "$COMPANION" tests/fixtures/liquid_smoke.wav "$SMOKE/live"
rm -rf "$SMOKE"

if [[ "${NO_INSTALL:-0}" == 1 ]]; then
  echo "==> Staged: $APP"
  exit 0
fi

echo "==> Install into $TARGET"
osascript -e 'tell application id "ru.dubr1k.gigaam-liquid" to quit' >/dev/null 2>&1 || true
if [[ -e "$TARGET" ]]; then
  OLD=$(plist "$TARGET" GigaAMReleaseVersion 2>/dev/null || echo old)
  mv "$TARGET" "$HOME/.Trash/GigaAMLiquid-$OLD-replaced-$(date +%Y%m%d-%H%M%S).app"
fi
ditto "$APP" "$TARGET"
codesign --verify --deep --strict "$TARGET"
rm -rf "$STAGE"
echo "==> Installed GigaAM Liquid $(plist "$TARGET" GigaAMReleaseVersion) ($(plist "$TARGET" CFBundleVersion)), worker $(plist "$TARGET/Contents/Resources/GigaAMWorker.app" GigaAMReleaseVersion)"

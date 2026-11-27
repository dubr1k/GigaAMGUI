#!/usr/bin/env bash
# Build both native x86-64 executables from the same signed source and pinned
# cross-toolchain. Must run on an amd64 Linux machine with Docker.
set -euo pipefail
export LC_ALL=C TZ=UTC
umask 022
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
generated="$here/generated"
mkdir -p "$generated"
work=$(mktemp -d "$generated/.ffmpeg-build.XXXXXXXX")
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/input" "$work/gnupg"
chmod 700 "$work/gnupg"

base=https://ffmpeg.org
curl -fsSL --proto '=https' --tlsv1.2 --retry 3 "$base/ffmpeg-devel.asc" -o "$work/input/ffmpeg-devel.asc"
curl -fsSL --proto '=https' --tlsv1.2 --retry 3 "$base/releases/ffmpeg-9.0.2.tar.xz" -o "$work/input/ffmpeg-9.0.2.tar.xz"
curl -fsSL --proto '=https' --tlsv1.2 --retry 3 "$base/releases/ffmpeg-9.0.2.tar.xz.asc" -o "$work/input/ffmpeg-9.0.2.tar.xz.asc"
(cd "$work/input" && echo '8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e  ffmpeg-9.0.2.tar.xz' | sha256sum -c -)
export GNUPGHOME="$work/gnupg"
gpg --batch --no-auto-key-retrieve --import "$work/input/ffmpeg-devel.asc"
gpg --batch --no-auto-key-retrieve --status-fd=1 --verify \
  "$work/input/ffmpeg-9.0.2.tar.xz.asc" "$work/input/ffmpeg-9.0.2.tar.xz" > "$work/signature-status"
grep -Fq '[GNUPG:] VALIDSIG FCF986EA15E6E293A5644F10B4322F04D67658D8 ' "$work/signature-status"

image=gigaam-rust-liquid-ffmpeg:9.0.2-pinned
mkdir "$work/docker-context"
cp "$here/Dockerfile.ffmpeg" "$work/docker-context/Dockerfile"
docker build --platform=linux/amd64 -f "$work/docker-context/Dockerfile" -t "$image" "$work/docker-context"
for platform in linux-x64 windows-x64; do
  case "$platform" in linux-x64) binary=ffmpeg ;; windows-x64) binary=ffmpeg.exe ;; esac
  for pass in 1 2; do
    out="$work/$platform-$pass"
    mkdir "$out"
    docker run --rm --network=none --read-only --platform=linux/amd64 \
      --user "$(id -u):$(id -g)" \
      --tmpfs /tmp:rw,exec,mode=1777 --tmpfs /work:rw,exec,mode=1777 \
      --env HOME=/tmp --env TMPDIR=/tmp \
      --mount "type=bind,src=$work/input,dst=/input,readonly" \
      --mount "type=bind,src=$here,dst=/scripts,readonly" \
      --mount "type=bind,src=$out,dst=/out" \
      "$image" bash /scripts/ffmpeg-build-inner.sh "$platform"
  done
  cmp "$work/$platform-1/$binary" "$work/$platform-2/$binary"
  (cd "$work/$platform-1" && sha256sum "$binary" > SHA256SUMS)
  destination="$generated/ffmpeg-$platform"
  if [[ -e "$destination" ]]; then
    echo "Refusing to overwrite $destination" >&2
    exit 1
  fi
  mv "$work/$platform-1" "$destination"
done

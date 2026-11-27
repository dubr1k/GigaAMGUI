#!/usr/bin/env bash
# Called in the pinned build container, not on the application host.
set -euo pipefail
export LC_ALL=C TZ=UTC SOURCE_DATE_EPOCH=1789699562
umask 022

case "${1:-}" in
  linux-x64)
    binary=ffmpeg
    target_flags=(--target-os=linux --enable-pthreads --disable-w32threads
      '--extra-ldexeflags=-static-libgcc -Wl,--build-id=none')
    ;;
  windows-x64)
    binary=ffmpeg.exe
    target_flags=(--enable-cross-compile --target-os=mingw32
      --cross-prefix=x86_64-w64-mingw32- --disable-pthreads --enable-w32threads
      '--extra-ldexeflags=-static -static-libgcc -Wl,--no-insert-timestamp')
    ;;
  *) echo 'Expected linux-x64 or windows-x64' >&2; exit 2 ;;
esac

cd /work
tar -xJf /input/ffmpeg-9.0.2.tar.xz
cd ffmpeg-9.0.2
# Native decoders only. FFmpeg's CLI selects its own internal filters; the
# explicitly enabled PCM muxer is named pcm_f32le (its CLI name is f32le).
./configure \
  --prefix=/opt/ffmpeg --arch=x86_64 --cpu=x86-64 \
  --disable-gpl --disable-nonfree --disable-version3 \
  --disable-autodetect --disable-network --disable-everything \
  --disable-doc --disable-debug --disable-programs --enable-ffmpeg \
  --disable-avdevice --disable-shared --enable-static \
  --enable-protocol=file,pipe \
  --enable-demuxer=aac,ac3,aiff,ape,asf,avi,caf,dts,eac3,flac,matroska,mov,mp3,mpegps,mpegts,ogg,wav,wv \
  '--enable-decoder=aac,aac_fixed,aac_latm,ac3,ac3_fixed,alac,ape,dca,eac3,flac,mp3,mp3float,mp3adu,mp3adufloat,mp3on4,mp3on4float,opus,pcm_*,adpcm_*,truehd,vorbis,wmalossless,wmapro,wmav1,wmav2,wmavoice,wavpack' \
  --enable-parser=aac,aac_latm,ac3,dca,flac,mlp,mpegaudio,opus,vorbis \
  --enable-filter=aresample --enable-encoder=pcm_f32le --enable-muxer=pcm_f32le \
  '--extra-cflags=-ffile-prefix-map=/work=. -fdebug-prefix-map=/work=. -fmacro-prefix-map=/work=.' \
  "${target_flags[@]}"
make -j2

mkdir -p /out/source
cp "$binary" /out/"$binary"
cp /input/ffmpeg-9.0.2.tar.xz /input/ffmpeg-9.0.2.tar.xz.asc /input/ffmpeg-devel.asc /out/source/
cp LICENSE.md COPYING.LGPLv2.1 /out/source/
cp /scripts/Dockerfile.ffmpeg /scripts/build-ffmpeg.sh /scripts/ffmpeg-build-inner.sh /out/source/
dpkg-query -W -f='${binary:Package}\t${Version}\n' | LC_ALL=C sort > /out/source/build-packages.txt
(
  cd /usr/share/doc
  find . -name copyright \( -type f -o -type l \) -print0 | LC_ALL=C sort -z |
    tar --dereference --mtime="@$SOURCE_DATE_EPOCH" --owner=0 --group=0 --numeric-owner --null -cJf /out/source/dependency-copyrights.tar.xz -T -
)
if [[ "$binary" == ffmpeg.exe ]]; then
  cp /opt/mingw-source/* /out/source/
  shopt -s nullglob
  sources=(/opt/mingw-source/mingw-w64_*orig.tar.*)
  if [[ ${#sources[@]} -ne 1 ]]; then echo 'Expected the exact MinGW source archive' >&2; exit 1; fi
  tar --wildcards --no-anchored -xOf "${sources[0]}" 'COPYING.MinGW-w64-runtime.txt' > /out/source/COPYING.MinGW-w64-runtime.txt
  test -s /out/source/COPYING.MinGW-w64-runtime.txt
  x86_64-w64-mingw32-objdump -p /out/"$binary" | awk '/DLL Name:/ {print tolower($3)}' | sort -u > /out/source/imports.txt
  test -s /out/source/imports.txt
  while IFS= read -r dll; do
    case "$dll" in
      advapi32.dll|bcrypt.dll|gdi32.dll|kernel32.dll|msvcrt.dll|ntdll.dll|ole32.dll|psapi.dll|shell32.dll|user32.dll|ws2_32.dll) ;;
      *) echo "Unexpected Windows dependency: $dll" >&2; exit 1 ;;
    esac
  done < /out/source/imports.txt
else
  readelf -d /out/"$binary" | awk -F'[][]' '/\(NEEDED\)/ {print $2}' | sort -u > /out/source/imports.txt
  test -s /out/source/imports.txt
  while IFS= read -r lib; do
    case "$lib" in
      libc.so.6|libm.so.6|libpthread.so.0|libdl.so.2|librt.so.1|ld-linux-x86-64.so.2) ;;
      *) echo "Unexpected Linux dependency: $lib" >&2; exit 1 ;;
    esac
  done < /out/source/imports.txt
fi

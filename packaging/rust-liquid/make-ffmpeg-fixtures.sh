#!/usr/bin/env bash
# Fixture generation is CI-only; the system ffmpeg is not shipped.
set -euo pipefail
out=${1:?fixture output directory required}
mkdir -p "$out"
tone='sine=frequency=440:sample_rate=44100:duration=1'
while read -r filename encoder muxer; do
  ffmpeg -nostdin -hide_banner -loglevel error -f lavfi -i "$tone" \
    -map 0:a:0 -c:a "$encoder" -f "$muxer" -y "$out/$filename"
done <<'CASES'
clip.wav pcm_s16le wav
clip.mp3 libmp3lame mp3
clip.m4a aac ipod
clip.aac aac adts
clip.mp4 aac mp4
clip.mov aac mov
clip.qta aac mov
clip.3gp aac 3gp
clip.avi pcm_s16le avi
clip.mkv pcm_s16le matroska
clip.webm libopus webm
clip.ogg libvorbis ogg
clip.opus libopus opus
clip.wma wmav2 asf
clip.flac flac flac
CASES
ffmpeg -nostdin -hide_banner -loglevel error \
  -f lavfi -i 'color=c=black:size=32x32:rate=1:duration=1' \
  -f lavfi -i "$tone" -map 0:v:0 -map 1:a:0 \
  -c:v mpeg4 -c:a aac -shortest -f mp4 -y "$out/video.mp4"

#!/usr/bin/env python3
"""Exercise long diarization in an installed Rust Liquid candidate via its UI.

This controller never provides inference or sets application state. It requires
an already-provisioned, proof-owned application-data root; see README.md.
"""

import argparse
import array
import base64
import hashlib
import importlib.util
import json
import math
import re
import shutil
import sys
import traceback
import wave
import zlib
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("rust_liquid_ui_proof", HERE / "ui-proof.py")
if SPEC is None or SPEC.loader is None:
    raise ImportError("Cannot load the packaged UI proof controller")
UI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UI)

RATE = 16_000
FRAME = 320
ENERGY = 600
MODEL = "multilingual_ctc"
SOURCES = {
    "2035-147960-0005": "01d16fb3fc2f84617bdd250e890f1b74913de731cb874803a4065b46d6785bed",
    "2035-147960-0006": "1a362365fd1b122545fd508b1adfb76fbf3ffca3c64a279901c035b54d09523d",
    "7976-110124-0023": "a152fbe382133954ea9cdab18cbf21c406e65427b0d8fd49a6d7b3a11fecd19d",
    "7976-110124-0024": "d5ddeb6a351f25cd81ade3c6b1a03a470413fb89d4e26225f0b3ef1e222170b7",
    "1673-143397-0002": "3488e3b10940eef9b649712684c8c82a49bf18e9d50f67ffa1e7d27c6325aa82",
    "1673-143397-0003": "48ad9a4b09b06e982c8ab410b726d31b3345f4370bb49f1636b70ef82d2330d8",
}
SPEAKER_HASH = "3955447b0499dc9e0a4541a895df08b03c69098eba4e56c02b5603e9f7f4fcbb"
SEGMENT_HASH = "d5335de334a24470b1f0326f72e513c59e156308a78ba3e1626851f570696ce4"


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_sources(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if (data.get("dataset"), data.get("revision"), data.get("split"), data.get("sample_rate")) != (
        "openslr/librispeech_asr", "71cacbfb7e2354c4226d01e70d77d5fca3d04ba1", "clean/validation", RATE
    ):
        raise ValueError("Unexpected fixture source, revision, split or sample rate")
    clips = {}
    for source in data["sources"]:
        ident = source["id"]
        if ident not in SOURCES or ident in clips or source["speaker"] != ident.split("-")[0]:
            raise ValueError(f"Unexpected or duplicate source {ident}")
        raw = zlib.decompress(base64.b64decode(source["pcm_zlib_base64"], validate=True))
        if (hashlib.sha256(raw).hexdigest() != SOURCES[ident]
                or len(raw) != source["samples"] * 2 or len(raw) % (2 * FRAME)
                or not 0 < len(raw) <= RATE * 8):
            raise ValueError(f"Invalid PCM excerpt: {ident}")
        pcm = array.array("h")
        pcm.frombytes(raw)
        if sys.byteorder != "little":
            pcm.byteswap()
        clips[ident] = pcm
    if set(clips) != set(SOURCES):
        raise ValueError("Fixture is missing one or more pinned source excerpts")
    return clips


def pcm_bytes(samples):
    if sys.byteorder == "little":
        return samples.tobytes()
    little = array.array("h", samples)
    little.byteswap()
    return little.tobytes()


def build_fixture(sources, destination):
    """Construct the independent source-placement oracle and stream a 30+ min WAV."""
    clips = load_sources(sources)
    first = "2035-147960-0005"
    returning = "2035-147960-0006"
    other = "7976-110124-0023"
    second_other = "7976-110124-0024"
    order = (first, other, "1673-143397-0002", returning, second_other, "1673-143397-0003")
    short = array.array("h", [0]) * (RATE * 2 // 5)
    spans = {}
    for ident in order:
        start = len(short)
        short.extend((int(sample) * 3 // 4 for sample in clips[ident]))
        spans[ident] = (start / RATE, len(short) / RATE)
        short.extend(array.array("h", [0]) * (RATE * 2 // 5))

    # Both original signals, offset 300 ms, at half amplitude as in the
    # independent-source development evaluation. Keep energy-derived overlap
    # frames separate from the mixed waveform and from model predictions.
    a = clips[first][:3 * RATE]
    b = clips[second_other][:3 * RATE]
    offset = 15 * FRAME
    mixed = [0] * max(len(a), offset + len(b))
    for index, sample in enumerate(a):
        mixed[index] += sample // 2
    for index, sample in enumerate(b):
        mixed[index + offset] += sample // 2
    if min(mixed) < -32768 or max(mixed) > 32767:
        raise ValueError("Fixture mix clips")
    mix_start = len(short)
    active = []
    for frame in range(len(mixed) // FRAME):
        ai = frame * FRAME
        bi = ai - offset
        if ai + FRAME <= len(a) and bi >= 0 and bi + FRAME <= len(b):
            aa = sum(int(x) ** 2 for x in a[ai:ai + FRAME])
            bb = sum(int(x) ** 2 for x in b[bi:bi + FRAME])
            if aa >= ENERGY * ENERGY * FRAME and bb >= ENERGY * ENERGY * FRAME:
                active.append(((mix_start + ai) / RATE, (mix_start + ai + FRAME) / RATE))
    if not active:
        raise ValueError("No simultaneously energetic frames in fixture mix")
    short.extend(array.array("h", mixed))
    short.extend(array.array("h", [0]) * (RATE * 2 // 5))

    late_start = 1802 * RATE + RATE * 2 // 5
    if len(short) >= late_start:
        raise ValueError("Fixture's early speech exceeds its late placement")
    late_end = late_start + len(clips[returning])
    duration_samples = late_end + RATE * 2 // 5
    with wave.open(str(destination), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframesraw(pcm_bytes(short))
        remaining = late_start - len(short)
        silence = bytes(RATE * 2 * 60)
        while remaining:
            count = min(remaining, RATE * 60)
            output.writeframesraw(silence[:count * 2])
            remaining -= count
        late = array.array("h", (int(sample) * 3 // 4 for sample in clips[returning]))
        output.writeframesraw(pcm_bytes(late))
        output.writeframesraw(bytes(RATE * 2 * 2 // 5))
    return {
        "duration": duration_samples / RATE,
        "solo": {
            "first": spans[first], "other": spans[other], "return": spans[returning],
            "late_return": (late_start / RATE, late_end / RATE),
        },
        "speech_spans": list(spans.values()) + [(mix_start / RATE, len(short) / RATE),
                                                (late_start / RATE, late_end / RATE)],
        "overlap_frames": active,
    }


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def majority(words, interval, name):
    start, end = interval
    ids = Counter(word["speaker"] for word in words
                  if word["speaker_status"] == "known" and start <= (word["start"] + word["end"]) / 2 < end)
    if sum(ids.values()) < 2:
        raise ValueError(f"{name}: fewer than two identified words: {dict(ids)}")
    ranked = ids.most_common()
    if ranked[0][1] * 2 <= sum(ids.values()):
        raise ValueError(f"{name}: no majority speaker: {dict(ids)}")
    return ranked[0][0]


def parse_srt(content, duration):
    stamp = r"(\d{2,}):(\d{2}):(\d{2}),(\d{3})"
    pattern = re.compile(rf"^{stamp} --> {stamp}$", re.MULTILINE)
    matches = list(pattern.finditer(content))
    if not matches:
        raise ValueError("SRT has no timed cues")

    def seconds(parts):
        hours, minutes, whole, millis = map(int, parts)
        if minutes >= 60 or whole >= 60:
            raise ValueError("Malformed SRT timestamp")
        return hours * 3600 + minutes * 60 + whole + millis / 1000

    cues = [(seconds(m.groups()[:4]), seconds(m.groups()[4:])) for m in matches]
    previous = -1.0
    for start, end in cues:
        if start < previous or end < start or end > duration + 0.001:
            raise ValueError("SRT cue timing is unordered or outside the recording")
        previous = start
    return cues


def validate_export(data, transcript, srt, reference):
    duration = data.get("duration")
    if not number(duration) or abs(duration - reference["duration"]) > 0.001:
        raise ValueError(f"Wrong absolute duration: {duration!r}")
    if not isinstance(data.get("text"), str) or not data["text"].strip() or not transcript.strip():
        raise ValueError("Transcript is empty")
    words = data.get("words")
    if not isinstance(words, list) or not words:
        raise ValueError("JSON has no timed words")
    previous = -1.0
    statuses = set()
    for word in words:
        if not isinstance(word, dict):
            raise ValueError("Invalid word")
        start, end = word.get("start"), word.get("end")
        if (not number(start) or not number(end) or start < previous or start < 0
                or end < start or end > duration + 0.001):
            raise ValueError(f"Invalid or unordered word timestamp: {word!r}")
        previous = start
        status, speaker = word.get("speaker_status"), word.get("speaker")
        if status not in ("known", "unknown", "overlap"):
            raise ValueError(f"Missing or invalid speaker status: {status!r}")
        if status == "known":
            if not isinstance(speaker, int) or isinstance(speaker, bool) or speaker < 0:
                raise ValueError("Known word lacks a speaker ID")
        elif speaker is not None:
            raise ValueError(f"{status} word is incorrectly assigned speaker {speaker!r}")
        statuses.add(status)
    if statuses != {"known", "unknown", "overlap"}:
        raise ValueError(f"Expected known, unknown and overlap words, observed {sorted(statuses)}")

    solo = reference["solo"]
    ids = {name: majority(words, interval, name) for name, interval in solo.items()}
    if ids["first"] != ids["return"] or ids["first"] != ids["late_return"] or ids["first"] == ids["other"]:
        raise ValueError(f"Speaker return or separation failed: {ids}")
    if not any(word["speaker_status"] == "unknown" and any(
        left <= (word["start"] + word["end"]) / 2 < right for left, right in reference["speech_spans"]
    ) for word in words):
        raise ValueError("No unknown word on a fixture speech interval")

    simultaneous = []
    for word in words:
        start, end = word["start"], word["end"]
        if end <= start:
            continue
        shared = sum(max(0.0, min(end, right) - max(start, left))
                     for left, right in reference["overlap_frames"])
        if shared >= 0.8 * (end - start):
            simultaneous.append(word)
    if not simultaneous:
        raise ValueError("No recognized word has >=80% simultaneously energetic source frames")
    if any(word["speaker_status"] == "known" for word in simultaneous):
        raise ValueError("A word dominated by two simultaneously energetic sources was forced to one speaker")
    if not any(word["speaker_status"] == "overlap" for word in simultaneous):
        raise ValueError("No such word was marked overlap")

    for tag in (f"[SPEAKER_{ids['first']}]", "[SPEAKER_UNKNOWN]", "[SPEAKER_OVERLAP]"):
        if tag not in transcript:
            raise ValueError(f"TXT/visible transcript lacks {tag}")
    cues = parse_srt(srt, duration)
    late_start, late_end = solo["late_return"]
    if not any(late_start <= start < late_end for start, _ in cues):
        raise ValueError("SRT lacks a cue at the returning speaker's absolute late timestamp")
    if "[SPEAKER_UNKNOWN]" not in srt or "[SPEAKER_OVERLAP]" not in srt:
        raise ValueError("SRT does not distinguish unknown and overlap")
    return ids


def model_directory(app_data):
    data_root = app_data / ("roaming" if sys.platform == "win32" else "data")
    return data_root / "ru.gigaam.gui.rustliquid" / "models" / MODEL


def check_prepared_models(app_data):
    model_dir = model_directory(app_data)
    segmentation = model_dir / "powerset_fp32_tract.onnx"
    if not segmentation.is_file() or digest(segmentation) != SEGMENT_HASH:
        raise RuntimeError(f"Pre-provision the pinned segmentation model in {model_dir}")
    speakers = [path for path in model_dir.iterdir() if path.is_file() and path.stat().st_size == 26_535_549]
    if not any(digest(path) == SPEAKER_HASH for path in speakers):
        raise RuntimeError(f"Pre-provision the pinned WeSpeaker model in {model_dir}")


def scratch_dirs(cache):
    return list(cache.rglob("gigaam-private-*")) if cache.exists() else []


def run_proof(app, sources, app_data, diagnostics, timeout):
    UI.check_display()
    if not app.is_file() or not sources.is_file() or not app_data.is_dir():
        raise RuntimeError("The installed app, pinned sources, or proof-owned app-data root is missing")
    check_prepared_models(app_data)
    cache = app_data / ("local" if sys.platform == "win32" else "cache")
    if scratch_dirs(cache):
        raise RuntimeError(f"Proof app-data already contains private scratch; inspect it first: {cache}")
    native_name = "msedgedriver.exe" if sys.platform == "win32" else "WebKitWebDriver"
    native_driver = shutil.which(native_name)
    if not native_driver:
        raise RuntimeError(f"Required native WebDriver is missing: {native_name}")
    diagnostics.mkdir(parents=True, exist_ok=True)
    with UI.proof_temporary_directory() as temporary:
        root = Path(temporary)
        fixture = root / "long-fixture.wav"
        output = root / "output"
        output.mkdir()
        reference = build_fixture(sources, fixture)
        driver = UI.WebDriver(app, native_driver, app_data, diagnostics / "long-driver.log")
        try:
            driver.start()
            def processing_ready():
                try:
                    return driver.state("#choose-media", "displayed")
                except UI.WebDriverError as error:
                    if error.code == "no such element":
                        return False
                    raise
            UI.wait_for("processing page", processing_ready, 30)
            # Read-only public IPC: fail if the application itself does not regard
            # the selected ASR model as installed. No UI/app state is injected.
            models = driver.command("POST", "/execute/async", {"script": """
                const done = arguments[arguments.length - 1];
                window.__TAURI__.core.invoke('list_models').then(done, e => done({error: String(e)}));
            """, "args": []}, timeout=30)
            if not isinstance(models, list) or not any(m.get("id") == MODEL and m.get("installed") is True for m in models):
                raise RuntimeError(f"The app does not report {MODEL} installed: {models!r}")
            driver.click("#asr-model")
            driver.click(f'#asr-model option[value="{MODEL}"]')
            if driver.property("#asr-model", "value") != MODEL:
                raise RuntimeError("Could not select the English-capable multilingual model")
            if not driver.state("#asr-diarization", "selected"):
                driver.click("label.switch-row")
            if not driver.state("#asr-diarization", "selected"):
                raise RuntimeError("Diarization was not enabled by the UI")
            for fmt in ("txt", "json", "srt"):
                selector = f'.output-card input[value="{fmt}"]'
                if not driver.state(selector, "selected"):
                    driver.click(selector)
                if not driver.state(selector, "selected"):
                    raise RuntimeError(f"Could not select {fmt} export")
            UI.choose(driver, "#choose-media", fixture)
            def fixture_selected():
                try:
                    path = driver.property("#selected-files .selected-file span", "textContent")
                    label = driver.visible_text("#selected-files .selected-file strong")
                    return UI.same_path(path, fixture) and label.get("text") == fixture.name and label.get("visible") is True
                except UI.WebDriverError as error:
                    if error.code == "no such element":
                        return False
                    raise
            UI.wait_for("selected long fixture", fixture_selected, 30)
            UI.choose(driver, "#choose-output", output, folder=True)
            UI.wait_for("selected output directory", lambda: UI.same_path(
                driver.property("#output-directory", "value"), output), 30)
            if list(output.iterdir()):
                raise RuntimeError("Output was not empty before starting")

            def start():
                driver.click("#start-processing")
                UI.wait_for("job to start", lambda: not driver.state("#start-processing", "enabled"), 30)

            start()
            def cancellable_scratch():
                if driver.state('[data-page="result"]', "displayed") or driver.state("#start-processing", "enabled"):
                    raise RuntimeError("Job finished before cancellation could be requested")
                return scratch_dirs(cache) and driver.state("#cancel-processing", "enabled")
            UI.wait_for("real private scratch and an enabled cancel button", cancellable_scratch, min(timeout, 600))
            driver.click("#cancel-processing")
            UI.wait_for("cancelled job to finish", lambda: driver.state("#start-processing", "enabled"), 120)
            status = driver.text("#progress-status")
            if "отмен" not in status.lower() or driver.state('[data-page="result"]', "displayed"):
                raise RuntimeError(f"Cancellation was not acknowledged: {status}")
            if list(output.iterdir()) or scratch_dirs(cache):
                raise RuntimeError("Cancellation left output or private scratch behind")

            start()
            seen_scratch = False
            paths = {fmt: output / f"{fixture.stem}.gigaam.{fmt}" for fmt in ("txt", "json", "srt")}
            def complete():
                nonlocal seen_scratch
                seen_scratch = seen_scratch or bool(scratch_dirs(cache))
                # Do not continuously query WebKitWebDriver while CPU inference
                # is running: its proxy repeatedly resets those connections.
                # Real UI visibility and exported contents are still required.
                if not all(path.is_file() for path in paths.values()):
                    return False
                return driver.state('[data-page="result"]', "displayed")
            UI.wait_for("long diarization result", complete, timeout, interval=1)
            if not seen_scratch or scratch_dirs(cache):
                raise RuntimeError("Success did not create and clean private long-path scratch")
            # Verify the actual files and their visible transcript below.
            if any(not path.is_file() for path in paths.values()):
                raise RuntimeError(f"Application did not save every requested export: {paths}")
            shown = driver.text("#result-content pre").strip()
            transcript = paths["txt"].read_text(encoding="utf-8").strip()
            if not shown or shown != transcript:
                raise RuntimeError("Saved TXT does not match the visible transcript")
            listed = []
            for index in range(1, len(paths) + 1):
                selector = f"#result-content ul li:nth-child({index})"
                driver.command("POST", "/execute/sync", {
                    "script": "arguments[0].scrollIntoView({block: 'center'});",
                    "args": [{UI.ELEMENT: driver.element(selector)}],
                })
                visible = driver.visible_text(selector)
                if not visible["visible"]:
                    raise RuntimeError(f"Saved export row {index} is not visible")
                listed.append(visible["text"])
            if any(not any(UI.same_path(line, path) for line in listed) for path in paths.values()):
                raise RuntimeError(f"The result page did not list all saved exports: {listed}")
            ids = validate_export(json.loads(paths["json"].read_text(encoding="utf-8")), transcript,
                                  paths["srt"].read_text(encoding="utf-8"), reference)
        except Exception:
            traceback.print_exc()
            driver.screenshot(diagnostics / "long-failure.png")
            raise
        finally:
            original = sys.exc_info()[1]
            try:
                driver.close()
            except Exception as error:
                if original is None:
                    raise
                print(f"WebDriver cleanup also failed: {error}", file=sys.stderr, flush=True)
                original.add_note(f"WebDriver cleanup failed: {error}")
            remaining = scratch_dirs(cache)
            if remaining:
                message = f"Private scratch remains for inspection: {remaining}"
                if original is None:
                    raise RuntimeError(message)
                print(message, file=sys.stderr, flush=True)
                original.add_note(message)
    print(f"PASS: packaged UI, cancellation/cleanup and long exports; speaker IDs {ids}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True, help="installed NSIS exe or extracted AppImage AppRun")
    parser.add_argument("--app-data", type=Path, required=True, help="dedicated, pre-provisioned proof data root")
    parser.add_argument("--sources", type=Path, default=HERE.parents[1] / "tests/fixtures/rust_liquid_long_sources.json")
    parser.add_argument("--diagnostics", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=3600, help="seconds per processing attempt")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    run_proof(args.app.resolve(), args.sources.resolve(), args.app_data.resolve(), args.diagnostics.resolve(), args.timeout)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Long UI proof failed: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1) from error

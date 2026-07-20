# Issue 33 Decoder-Overlap Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent duplicated text and overlapping timestamps at adjacent ASR chunk boundaries while preserving the decoder overlap that protects recognition quality.

**Architecture:** Reconcile each decoded chunk once in the shared ASR layer: align its text against the previous chunk, remove only the matched current prefix, clip retained word timestamps to the chunk's nominal ownership interval, and rebuild text from those words. All three ASR backends use the same primitive. Diarization then validates the normalized stream and may merge only exactly touching same-speaker turns; formatters remain pure renderers.

**Tech Stack:** Python 3.11, pytest, Ruff, PyTorch/MLX/ONNX ASR adapters, PyInstaller, GitHub Actions.

## Global Constraints

- Work on the current `main` branch as explicitly requested; do not create a worktree or feature branch.
- Preserve the 2-second decoder overlap and the runtime import boundary.
- Do not edit vendored `src/gigaam/` code or formatter-specific output logic.
- Do not touch the untracked `session-1907.md` file.
- Use `apply_patch` for source edits and lean-ctx tools for reads, searches, and shell commands.
- Follow red-green-refactor for each behavior change and commit only verified increments.
- Before release, run the full suite, Ruff, graph update, macOS bundle build/verification, and inspect the final diff.

## Task 1: Specify and implement shared overlap reconciliation

**Files:**

- Modify: `tests/test_asr_chunking.py`
- Modify: `src/core/asr/chunking.py`

- [ ] Add failing tests for the two exact issue #33 phrase pairs. Assert `stitch_overlapping_text()` removes the repeated semantic prefix despite a short leading divergence or filler-word gap.
- [ ] Add failing tests for a public `normalize_chunk_words()` helper covering: `None`, prefix removal, nominal-boundary clipping, wholly outside words, zero-duration words, non-finite timestamps, and monotonic output.
- [ ] Run `.venv/bin/python -m pytest tests/test_asr_chunking.py -q`; expect failures because the issue phrases return zero overlap and the helper does not exist.
- [ ] Enhance `_aligned_tail_prefix_overlap()` with a bounded alignment search over the previous tail and current prefix. Require alignment to reach the previous tail and current selected-prefix end; accept either at least three meaningful matches with no more than two gaps per side, or two meaningful words of length at least four with at most one total gap. Prefer the latest valid previous occurrence and then the strongest alignment.
- [ ] Implement `normalize_chunk_words(words, *, start_sec, end_sec, trim_prefix_words=0)`. Return `None` for missing/invalid timestamp data; remove the chosen current prefix; clip retained words into the nominal interval; drop outside or zero-duration items; preserve token order and enforce nondecreasing timestamps without inventing tokens.
- [ ] Run `.venv/bin/python -m pytest tests/test_asr_chunking.py -q`; expect all tests to pass.
- [ ] Run `.venv/bin/python -m ruff check src/core/asr/chunking.py tests/test_asr_chunking.py`.
- [ ] Commit as `fix: reconcile overlapping ASR chunk words`.

## Task 2: Integrate reconciliation into the PyTorch backend

**Files:**

- Modify: `tests/test_pytorch_backend.py`
- Modify: `src/core/asr/pytorch_backend.py`

- [ ] Extend `test_jittered_boundary_word_is_emitted_once` to assert every retained word lies inside its returned segment's nominal `[start, end]` range and mapped output cannot move backward across the boundary.
- [ ] Add a regression test that exercises a matched multiword prefix and asserts the current segment text is rebuilt from only retained words.
- [ ] Run `.venv/bin/python -m pytest tests/test_pytorch_backend.py -q`; expect the new boundary assertions to fail with current unbounded timestamps.
- [ ] Reorder PyTorch chunk handling: decode absolute words, stitch against the previous text, normalize current words using the returned trim count, and rebuild text from normalized words. Preserve text-only fallback when usable word timestamps are unavailable.
- [ ] Run `.venv/bin/python -m pytest tests/test_pytorch_backend.py tests/test_asr_chunking.py -q`; expect all tests to pass.
- [ ] Run `.venv/bin/python -m ruff check src/core/asr/pytorch_backend.py tests/test_pytorch_backend.py`.
- [ ] Commit as `fix: bound PyTorch words to chunk ownership`.

## Task 3: Integrate reconciliation into MLX and ONNX backends

**Files:**

- Modify: `tests/test_mlx_backend.py`
- Modify: `tests/test_onnx_backend.py`
- Modify: `src/core/asr/mlx_backend.py`
- Modify: `src/core/asr/onnx_backend.py`

- [ ] Update the MLX boundary fixture so the genuinely new retained word occurs after the nominal cut, retain the jittered duplicate case separately, and assert all word timestamps are inside their segment interval.
- [ ] Add an ONNX regression with adjacent nominal chunks and decoder-overlap words; assert one duplicate copy, rebuilt text, and bounded timestamps.
- [ ] Run `.venv/bin/python -m pytest tests/test_mlx_backend.py tests/test_onnx_backend.py -q`; expect new boundary assertions to fail.
- [ ] Apply the same decode → stitch → trim → clip → rebuild order in MLX and ONNX, importing only the shared lightweight helper.
- [ ] Run `.venv/bin/python -m pytest tests/test_mlx_backend.py tests/test_onnx_backend.py tests/test_asr_chunking.py -q`; expect all tests to pass.
- [ ] Run `.venv/bin/python -m ruff check src/core/asr/mlx_backend.py src/core/asr/onnx_backend.py tests/test_mlx_backend.py tests/test_onnx_backend.py`.
- [ ] Commit as `fix: normalize MLX and ONNX chunk boundaries`.

## Task 4: Defend the diarization contract and output timeline

**Files:**

- Modify: `tests/test_diarization_mapping.py`
- Modify: `src/core/diarization/mapping.py`
- Modify if an existing suitable integration test is present: `tests/test_formatters.py`

- [ ] Add a two-segment regression using adjacent ASR ownership windows. Assert mapped turns never overlap and touching same-speaker turns merge without merging across a real pause.
- [ ] Add a contract test that passes already-corrupt overlapping ASR word timelines and expects an explicit `ValueError` instead of silent overlapping output.
- [ ] If `tests/test_formatters.py` already has a concise multi-format fixture, add one assertion each for MD/SRT/VTT chronological output; otherwise keep the regression at the shared mapped-turn level.
- [ ] Run `.venv/bin/python -m pytest tests/test_diarization_mapping.py tests/test_formatters.py -q`; expect the new contract test to fail.
- [ ] Add one append/validation path in `mapping.py`: reject backward/overlapping turns beyond a small floating-point epsilon, merge only same-speaker turns that exactly touch, and retain pauses as separate turns.
- [ ] Run `.venv/bin/python -m pytest tests/test_diarization_mapping.py tests/test_formatters.py -q`; expect all tests to pass.
- [ ] Run `.venv/bin/python -m ruff check src/core/diarization/mapping.py tests/test_diarization_mapping.py tests/test_formatters.py`.
- [ ] Commit as `fix: enforce non-overlapping diarized turns`.

## Task 5: Verify the implementation as a whole

**Files:**

- Review: all modified source and test files
- Update generated graph: `graphify-out/` (gitignored)

- [ ] Run focused ASR and diarization tests together: `.venv/bin/python -m pytest tests/test_asr_chunking.py tests/test_pytorch_backend.py tests/test_mlx_backend.py tests/test_onnx_backend.py tests/test_diarization_mapping.py tests/test_formatters.py -q`.
- [ ] Run the full suite: `.venv/bin/python -m pytest tests/ -q`. If any of the three documented display/DPI-sensitive GUI tests fail, compare the same test against a clean tree before attributing it to this change.
- [ ] Run `.venv/bin/python -m ruff check .`.
- [ ] Run `graphify update .` and confirm it completes; generated graph files remain untracked/ignored.
- [ ] Inspect `git diff --check`, `git status --short`, and the complete source/test diff for accidental scope changes.

## Task 6: Prepare release 1.3.2 metadata

**Files:**

- Modify: `packaging/gigaam_app_mac.spec`
- Modify: `docs/CHANGELOG.md`
- Create: `docs/RELEASE_NOTES_1.3.2.md`
- Review: `.github/workflows/build.yml`

- [ ] Add a `1.3.2` changelog entry describing boundary de-duplication, bounded timestamps, and the unchanged recognition overlap.
- [ ] Add concise Russian release notes for issue #33 and its regression coverage.
- [ ] Set both `CFBundleShortVersionString` and `CFBundleVersion` to `1.3.2` in the macOS spec.
- [ ] Verify `.github/workflows/build.yml` still triggers on `v*` tags and does not require a workflow edit.
- [ ] Run `.venv/bin/python -m pytest tests/test_macos_packaging_config.py tests/test_packaging_runtime_deps.py -q`.
- [ ] Run `git diff --check` and Ruff on any changed Python/spec files.
- [ ] Force-add ignored release documentation by exact path and commit as `chore: prepare release 1.3.2`.

## Task 7: Build and verify the macOS application

**Files:**

- Build output: `dist/GigaAMTranscriber.app` (gitignored)

- [ ] Confirm the host is Apple Silicon macOS and the working tree contains only intended release changes plus the untouched `session-1907.md`.
- [ ] Run `bash packaging/build_exe_mac.sh` from the repository root and monitor it to completion.
- [ ] Run `.venv/bin/python scripts/verify_macos_bundle.py dist/GigaAMTranscriber.app` if the build script's final verification is not already conclusive.
- [ ] Inspect bundle metadata with `/usr/libexec/PlistBuddy` and assert both bundle versions are `1.3.2`.
- [ ] Launch `dist/GigaAMTranscriber.app/Contents/MacOS/GigaAMTranscriber`, wait for successful startup, then terminate only that launched process cleanly. Capture startup logs and confirm there is no immediate import/runtime failure.
- [ ] Confirm the `.app` remains present in `dist/` and report its size.

## Task 8: Publish the tag and start GitHub Actions

**Files:**

- No further source edits expected

- [ ] Re-run the focused regression tests, full suite summary, Ruff, `git diff --check`, and `git status --short` immediately before publication.
- [ ] Confirm `v1.3.2` does not exist locally or on `origin` and inspect the commits that will be pushed.
- [ ] Push the current `main` branch to `origin`.
- [ ] Create annotated tag `v1.3.2` at the verified release commit with message `GigaAM Transcriber 1.3.2`.
- [ ] Push only tag `v1.3.2` to `origin`.
- [ ] Use `gh run list`/`gh run watch` to verify the tag-triggered build workflow starts; report the run URL and current/final status.
- [ ] Leave `session-1907.md` untracked and unchanged.

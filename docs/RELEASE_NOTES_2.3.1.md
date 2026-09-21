# GigaAM Transcriber 2.3.1

Исправительный релиз поверх 2.3.0 (у 2.3.0 сборка в CI не дошла до
публикации — её артефакты не выкладывались). Содержит всё из 2.3.0: реестр
LLM-провайдеров в TUI, стриминг ответа LLM, общие с десктопом настройки,
headless-режим `gigaam transcribe` / `gigaam llm`, `gigaam --update` — см.
[заметки 2.3.0](RELEASE_NOTES_2.3.0.md). Плюс два исправления ниже.

## Исправлено

- **Сборка офлайн-моделей больше не падает из-за одной неудачной загрузки с
  HuggingFace.** В CI 2.3.0 Intel-сборка упала на транзиентной ошибке HF при
  скачивании silero VAD (`LocalEntryNotFoundError` на shared-раннере), и
  релиз целиком не вышел. `scripts/build_offline_models.py` теперь делает до
  четырёх попыток с паузами 10/30/60 с для ASR, VAD и моделей диаризации.
- **Кэш torch, скачанный сборкой на другом Python, не считается
  установленным.** Кэш `~/Library/Caches/GigaAMGUICash/torch/<variant>` общий
  для всех сборок приложения на машине, а проверка «установлен» сравнивала
  только версии пакетов. PyQt-сборка на Python 3.12 клала туда cp312-колёса,
  а Liquid-компаньон из CI (Python 3.11) активировал их и падал на
  `import torch` — «Failed to load PyTorch C extensions» в `--selfcheck` и
  на любом torch-пути (backend `pytorch`, pyannote). Теперь рантайм считается
  установленным только с C-расширением под текущий интерпретатор, чужой ABI
  получает отдельную папку `<variant>-cp311`, а сборка со встроенным torch
  продолжает пользоваться им.
- `gigaam --version` показывает тег релиза (`v2.3.1`), а не хеш: установщик
  дотягивает теги после поверхностного `fetch`.

## Что проверить

- В `/Applications` одновременно PyQt-сборка (Python 3.12) и Liquid из CI
  (Python 3.11): `GigaAMTranscriber --selfcheck` из обоих — `SELFCHECK PASS`,
  в `~/Library/Caches/GigaAMGUICash/torch/` могут появиться две папки
  (`default`, `default-cp311`) — так и задумано.
- `gigaam --update` → `gigaam --version` печатает `gigaam-tui v2.3.1`.

---

## English

A patch release on top of 2.3.0 (whose CI build never reached publication —
no 2.3.0 artifacts were released). It carries everything from 2.3.0: the
LLM provider registry in the TUI, streamed LLM output, settings shared with
the desktop app, headless `gigaam transcribe` / `gigaam llm`, `gigaam
--update` — see the [2.3.0 notes](RELEASE_NOTES_2.3.0.md) — plus two fixes.

### Fixed

- **The offline model bundle no longer fails on a single HuggingFace
  hiccup.** The 2.3.0 Intel job died on a transient HF error while fetching
  silero VAD (`LocalEntryNotFoundError` on a shared runner), taking the whole
  release with it. `scripts/build_offline_models.py` now retries up to four
  times with 10/30/60 s pauses for ASR, VAD and the diarization models.
- **A torch cache built for another Python is no longer treated as
  installed.** `~/Library/Caches/GigaAMGUICash/torch/<variant>` is shared by
  every build of the app on the machine, and the "installed" check only
  compared package versions. A PyQt build on Python 3.12 put cp312 wheels
  there; the CI-built Liquid companion (Python 3.11) activated them and
  `import torch` failed with "Failed to load PyTorch C extensions" — in
  `--selfcheck` and on every torch-backed path (`pytorch` backend,
  pyannote). A runtime now counts as installed only with a C extension for
  the running interpreter, a foreign ABI gets its own `<variant>-cp311`
  directory, and a build with bundled torch keeps using it.
- `gigaam --version` prints the release tag (`v2.3.1`) instead of a hash:
  the installer fetches tags after its shallow `fetch`.

### What to check

- With both the PyQt build (Python 3.12) and the CI Liquid build (Python
  3.11) in `/Applications`, `GigaAMTranscriber --selfcheck` from each prints
  `SELFCHECK PASS`; `~/Library/Caches/GigaAMGUICash/torch/` may hold two
  directories (`default`, `default-cp311`) — by design.
- `gigaam --update` → `gigaam --version` prints `gigaam-tui v2.3.1`.

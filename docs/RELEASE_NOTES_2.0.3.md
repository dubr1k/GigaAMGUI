# GigaAM Transcriber 2.0.3

Исправляющий релиз нативного macOS-клиента GigaAMLiquid: три поломки, из-за
которых архивы 2.0.1/2.0.2 не транскрибировали, и CI-гейт, который их теперь
ловит.

## Исправления

- **Liquid падал на первой транскрибации.** В релизной раскладке
  (`GigaAMLiquid.app` рядом с `GigaAMTranscriber.app`, исходников нет) клиент
  до запуска worker'а требовал `src/tui_worker.py` и сообщал «The Python
  project is missing src/tui_worker.py». Проверка осталась от режима
  `python -m src.tui_worker` и не была снята, когда 2.0.1 убрал исходники из
  архива. Теперь в режиме frozen companion она не выполняется.
- **macOS 27 не грузит `scipy==1.15.3`.** dyld отклоняет
  `scipy/sparse/linalg/_propack/*.so` («`__thread_bss` … offset field is not
  zero»), и любая транскрибация в companion падала на импорте
  `scipy.sparse.linalg`. Пин поднят до `scipy==1.16.3` (колёса cp311 есть под
  arm64 и macOS x86_64).
- **Офлайн-архив на чистом Mac с `auto`.** `auto` на Apple Silicon выбирал
  MLX, модели которого в `models/hf` нет, а `HF_HUB_OFFLINE=1` (его ставит
  Liquid при наличии офлайн-моделей) запрещал докачку; PyTorch-fallback офлайн
  тоже не работал. Теперь при `HF_HUB_OFFLINE` и отсутствии MLX-модели в кэше
  `auto` берёт ONNX-цепочку из офлайн-набора, с пояснением в журнале.

## Packaging / CI

- `scripts/native_worker_smoke.py` гоняет реальный файл через JSONL-worker
  companion (держит stdin открытым до `completed`, как это делает Liquid).
- Release job запускает его в офлайн-архиве Liquid с пустым пользовательским
  кэшем, backend `auto` и `HF_HUB_OFFLINE=1`: `ping`/`pong` эти поломки не
  ловил.
- Версии PyQt, AppKit, Tauri, npm и Cargo синхронизированы на `2.0.3`.

---

This patch release fixes three failures that stopped the GigaAMLiquid 2.0.1/2.0.2
archives from transcribing: a leftover source-tree check that rejected the frozen
companion layout, a `scipy==1.15.3` wheel that macOS 27's dyld refuses to load
(pinned to 1.16.3), and `auto` backend selection that reached for the absent MLX
model in the offline archive (now falls back to the bundled ONNX chain). The
release job now runs a real file through the companion in a clean-cache offline
layout.

# GigaAM Transcriber 2.5.3-2

Нативное GigaAMLiquid теперь поставляется с отдельным headless `GigaAMWorker.app` внутри приложения вместо полной копии PyQt GUI. Обычное `GigaAMTranscriber.app` остаётся отдельным архивом.

## Изменено

- Обработка файлов, Live, LLM и загрузка медиа используют тот же протокол Python worker; в worker не входит PyQt.
- Online-архив Liquid содержит встроенный worker. Offline-архив дополнительно содержит ONNX-модели рядом с ним внутри `Contents/Resources/models/hf`.

## Исправлено

- Liquid и встроенный worker имеют общий номер релиза `2.5.3-2` (он показан в разделе «О приложении»). Системные поля macOS в обоих бандлах: версия `2.5.3`, номер сборки `2.5.32`. GitHub Actions проверяет все три значения в online- и offline-приложениях и не публикует несовпадающие архивы.

## Проверка

- Запустить Liquid из распакованного архива и обработать аудиофайл; выходной TXT должен содержать транскрипцию.
- Для offline-архива повторить обработку и Live без сети и с пустым пользовательским кэшем моделей.
- Убедиться, что `GigaAMWorker.app` находится в `GigaAMLiquid.app/Contents/Resources/` и в Dock не появляется второе приложение.

---

## English

Native GigaAMLiquid now embeds a dedicated headless `GigaAMWorker.app` instead of the complete PyQt GUI. The classic `GigaAMTranscriber.app` remains a separate download.

### Changed

- File processing, Live, LLM and media downloads use the same Python worker protocol; the worker does not contain PyQt.
- The online Liquid archive embeds the worker. The offline archive also includes ONNX models in `Contents/Resources/models/hf`.

### Fixed

- Liquid and its embedded worker share release identifier `2.5.3-2`, shown in About. Both bundles use macOS marketing version `2.5.3` and build number `2.5.32`. GitHub Actions checks all three values in online and offline apps and blocks publication on a mismatch.

### Verify

- Launch Liquid from the extracted archive and transcribe an audio file; the resulting TXT should contain recognized text.
- In the offline archive, repeat processing and Live without network access or a pre-existing model cache.
- Verify that `GigaAMWorker.app` is under `GigaAMLiquid.app/Contents/Resources/` and no second application appears in the Dock.

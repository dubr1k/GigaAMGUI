# GigaAM Transcriber 1.4.2

## Русский

### Исправления

- Исправлена [#42](https://github.com/dubr1k/GigaAMGUI/issues/42): live-сессия больше не завершается молча без расшифровки.
  - Остановка сессии дожидается всех незавершённых декодирований и только потом пишет экспорты. Раньше очередь распознавания обрывалась через одну секунду, поэтому короткие сессии сохранялись пустыми, а окно вывода оставалось чистым.
  - Исключение у потребителя аудио-чанков больше не убивает поток захвата. В оконной сборке такой traceback уходил в никуда, и запись «продолжалась» без распознавания.
  - Сбой записи FLAC больше не останавливает распознавание: дорожка помечается как проблемная, а расшифровка продолжается.
- Исправлен выбор устройства системного звука на Windows: устройство по умолчанию теперь определяется по текущему устройству воспроизведения WASAPI, а не по устройству ввода. Раньше выбирался произвольный loopback-эндпоинт (например, неактивный S/PDIF), который не отдавал звук вообще.
- Молчащий второй источник больше не отключает запись общей дорожки. Сообщение «Запись смешанного аудио отключена… превышен лимит ожидающих кадров» через пару секунд после старта заменено на конкретную подсказку о том, какой источник не отдаёт звук; общая дорожка продолжает писаться с активным источником.
- Реплики короче полутора секунд больше не отбрасываются: финальное распознавание больше не требует, чтобы для фразы успел выйти промежуточный результат.
- Модель распознавания загружается до старта захвата, с индикацией и понятной ошибкой, вместо тихой ленивой загрузки на первом декодировании.

### Диагностика

- Каждая сессия пишет `live.log` в свою папку, а события захвата, сбои и ошибки ASR теперь попадают на вкладку «Журнал обработки». Раньше вкладка оставалась пустой, и у live-тракта не было никакой диагностики.
- Проблемы показываются в отдельной строке под статусом и не затираются следующим обновлением состояния.
- Чекпойнт сессии пишется не чаще раза в две секунды вместо записи на каждый чанк (около 50 записей в секунду на источник).

### Оверлей

- Кнопка «Оверлей» стала переключателем: повторное нажатие скрывает окно. Состояние синхронизировано с кнопкой закрытия самого оверлея и сохраняется между запусками.
- Оверлей закрывается по Esc; кнопка закрытия стала заметнее.

### Обновление

- Для Windows скачайте новый portable или offline-архив этого релиза.
- Если live-расшифровка всё ещё не появляется, приложите `live.log` из папки сессии к отчёту — теперь в нём видно, на каком шаге обрывается тракт.

## English

### Fixes

- Fixed [#42](https://github.com/dubr1k/GigaAMGUI/issues/42): a live session no longer ends silently with no transcript.
  - Stopping now drains every queued decode before writing exports. The recognition queue used to be abandoned after one second, so short sessions were saved empty and the output pane stayed blank.
  - An exception in the audio-chunk consumer no longer kills the capture thread. In a windowed build that traceback went nowhere, and recording appeared to continue without recognition.
  - A FLAC recording failure no longer stops recognition: the track is reported as failed and transcription continues.
- Fixed Windows system-audio device selection: the default is now the loopback that belongs to the current WASAPI playback endpoint instead of being matched against the default input device. Previously an arbitrary loopback endpoint (an inactive S/PDIF output, for instance) was chosen and delivered no audio at all.
- A silent second source no longer disables combined-track recording. The "Mixed audio recording disabled… pending input frame limit exceeded" message that appeared seconds after start is replaced by a specific hint naming the source that delivers no audio, while the combined track keeps recording the live source.
- Utterances shorter than 1.5 seconds are no longer dropped: a final result no longer requires that a partial was scheduled for the phrase first.
- The recognition model is loaded before capture starts, with progress and a clear error, instead of loading lazily inside the first decode.

### Diagnostics

- Every session writes `live.log` into its session folder, and capture events, failures, and ASR errors now reach the Processing log tab. The tab used to stay empty, leaving the live path with no diagnostics at all.
- Problems are shown on a dedicated line under the status label and are no longer overwritten by the next state update.
- The session checkpoint is written at most once every two seconds instead of once per chunk (roughly 50 writes per second per source).

### Overlay

- The Overlay button is now a toggle: pressing it again hides the window. Its state is synchronized with the overlay's own close button and persists across restarts.
- The overlay closes with Esc, and its close button is more visible.

### Upgrade

- Download the new Windows portable or offline archive for this release.
- If live transcription still produces nothing, attach `live.log` from the session folder to your report — it now shows where the pipeline stops.

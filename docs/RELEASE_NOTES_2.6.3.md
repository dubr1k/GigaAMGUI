# GigaAM Transcriber 2.6.3

Патч-релиз Liquid: управление записью стало мгновенным и понятным.

## Исправлено

- **Остановка Live-записи больше не выглядит как продолжающаяся запись.** По нажатию «Стоп» Liquid сразу прекращает захват микрофона и системного звука, фиксирует таймер и показывает «Остановка…». Очередь уже отправленных фрагментов больше не удерживает команду остановки. Распознавание, сохранение аудио и экспорт продолжаются в фоне до безопасного завершения сессии.
- **Кнопки записи соответствуют состоянию сессии.** До начала видна только запись; во время записи — только пауза и стоп; при паузе — возобновление и стоп. Кнопка папки появляется после сохранения сессии.
- **Вместо полосы уровня появился живой сигнал.** В Live теперь движется линия из вертикальных штрихов: она реагирует на громкость микрофона или системного звука, как в «Диктофоне» macOS.

## Что проверить

- В Live начните запись, скажите несколько фраз и нажмите «Стоп»: таймер и волна должны остановиться сразу, а интерфейс не должен снова показывать активную запись, пока файлы сохраняются.
- До записи доступна только круглая кнопка записи; при записи доступны пауза и стоп; после сохранения можно открыть папку сессии.
- Во время речи штрихи волны становятся выше, в тишине возвращаются к центральной линии.

PyQt, web, TUI и протокол worker не менялись: исправление касается нативного Swift-клиента Liquid.

---

## English

Liquid patch release: recording controls now respond immediately and reflect the actual session state.

### Fixed

- **Stopping a Live session no longer looks like recording continues.** When Stop is pressed, Liquid immediately stops microphone and system-audio capture, freezes the timer and shows “Stopping…”. Previously queued audio no longer holds up the stop command. Recognition, audio finalization and exports continue safely in the background until the session is complete.
- **Recording controls now match the session state.** Before a session, only Record is shown; while recording, only Pause and Stop; while paused, Resume and Stop. The folder button appears after the session has been saved.
- **The level bar is now a live waveform.** Live now shows a rolling line of vertical strokes that responds to microphone or system-audio volume, like macOS Voice Memos.

### What to check

- In Live, start a recording, say a few words, then press Stop: the timer and waveform must stop immediately, and the UI must not return to an active-recording state while files are saved.
- Before recording, only the round Record button is available; while recording, Pause and Stop are available; after saving, the session folder can be opened.
- While speaking, waveform strokes grow; in silence, they return to the centre line.

PyQt, web, the TUI and the worker protocol are unchanged: this fix is limited to the native Liquid Swift client.

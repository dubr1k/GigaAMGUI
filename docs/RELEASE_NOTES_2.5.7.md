# GigaAM Transcriber 2.5.7

Патч-релиз Live: текст появляется через секунды, а не через ~15 с, короткие фразы больше не теряются, а место сохранения сессии видно сразу.

## Исправлено

- **Короткие фразы пропадали, а первый текст появлялся только через ~15 с.** Детектор речи усреднял громкость по чанку 100 мс, и тихий голос не дотягивал до порога, а распознанные короткие фразы отбрасывались фильтром «меньше секунды речи и меньше двух слов». На реальной записи так терялись «Привет! Как у тебя дела?», «Чем занимаешься?» и «Что делаешь?», и пользователь повторял их снова. Теперь речь определяется по кадрам 20 мс, начало слова не обрезается, а короткая фраза показывается черновиком уже через 0,4 с паузы. На той же записи первый текст появляется через 4 с вместо 18 с.
- **Запись начинается, когда модель готова.** После нажатия «Запись» приложение сначала загружает модель распознавания и показывает «Загрузка модели распознавания… Запись начнётся, когда она будет готова». Микрофон и таймер включаются только после этого. Раньше запись шла сразу, а модель грузилась на первой фразе: сказанное в эти секунды (в Liquid — от секунды до ~10 с при первом запуске после установки) распознавалось с опозданием или терялось. Начинайте говорить, когда пошёл таймер. Если модель не загрузилась, вы увидите ошибку, а пустая сессия не создаётся.
- **Черновики больше не дублируют слова** («Как у тебя? тебя дела?»), в том числе в длинных монологах.
- **Двойное нажатие «Пауза» в Liquid** завершало сессию без сохранения. Теперь лишнее нажатие просто игнорируется.
- **PyQt:** приложение аварийно закрывалось, если ответ ассистента приходил после «Остановить», и дублировало текст, когда финальная расшифровка отличалась от черновика.
- **Liquid:** после остановки на экране не остаётся черновик, которого нет в файлах; финал микрофона не стирает черновик системного звука; при ошибке сохранения статус сообщает об ошибке, а не «Сессия сохранена».
- **Общая дорожка mix.flac:** системный звук, стартовавший позже микрофона, больше не сдвигается в начало записи.

## Изменено

- **Где лежит запись, видно сразу.** Каждая сессия сохраняется в папку с датой и временем начала, например `~/Documents/GigaAM/live/2026-10-01_17-21-14`, а не `session-9e6564f7…`. В ней `transcript.txt` / `.srt` и прочие выбранные форматы, а также `mic.flac` (и `system.flac` / `mix.flac`, если они включены).
- **Liquid:** под таймером записи показан путь: до записи — куда будет сохранено, во время — текущая папка, после — сохранённая. Кнопка с папкой открывает её в Finder, а рядом с полем «Папка сессий» есть кнопка «Выбрать».
- **PyQt:** папка Live по умолчанию — `~/Documents/GigaAM/live` (раньше это была папка результатов пакетной обработки), и создаётся сама. Кнопка «Открыть» открывает последнюю сессию, а статус после остановки показывает её имя.

## Что проверить

- Нажмите «Запись»: сначала появится «Загрузка модели распознавания…», затем пойдёт таймер. Сразу скажите короткую фразу («Привет, как дела?»): текст должен появиться примерно через секунду после неё, без повтора.
- После остановки нажмите кнопку с папкой (Liquid) или «Открыть» (PyQt): откроется папка `ГГГГ-ММ-ДД_ЧЧ-ММ-СС` с транскриптом и аудио.
- Длинный монолог на 30+ секунд: черновик растёт без повторов слов.

TUI не менялся: Live в TUI нет, команды `live_*` воркера использует только Liquid.

---

## English

Live patch release: text appears within seconds instead of ~15 s, short phrases are no longer lost, and where a session is saved is visible right away.

### Fixed

- **Short phrases disappeared and the first text took ~15 s.** The speech detector averaged loudness over 100 ms chunks, so a quiet voice never crossed the threshold, and short phrases that were recognised got dropped by a "less than a second of speech and fewer than two words" filter. In a real recording this lost "Привет! Как у тебя дела?", "Чем занимаешься?" and "Что делаешь?", and the user had to repeat them. Speech is now detected per 20 ms frame, word onsets are no longer clipped, and a short phrase shows up as a draft after a 0.4 s pause. On the same recording the first text appears after 4 s instead of 18 s.
- **Recording starts once the model is ready.** After Record, the app first loads the recognition model and shows "Loading the recognition model… Recording starts once it is ready". The microphone and the timer start only then. Recording used to start immediately while the model loaded on the first phrase, so what you said in those seconds (in Liquid, from one second up to ~10 s on the first launch after installing) was recognised late or lost. Start speaking once the timer runs. If the model cannot load, you get an error and no empty session is created.
- **Drafts no longer repeat words** ("Как у тебя? тебя дела?"), including in long monologues.
- **A double click on Pause in Liquid** ended the session without saving it. The extra click is now ignored.
- **PyQt:** the app crashed when the assistant's answer arrived after Stop, and duplicated text when the final transcript differed from the draft.
- **Liquid:** after stopping, no draft stays on screen that is missing from the files; a microphone final no longer clears the system-audio draft; a failed save is reported as an error instead of "Session saved".
- **Combined mix.flac:** system audio that started after the microphone is no longer shifted to the start of the recording.

### Changed

- **Where the recording goes is visible right away.** Each session is saved to a folder named after its start time, e.g. `~/Documents/GigaAM/live/2026-10-01_17-21-14`, instead of `session-9e6564f7…`. It holds `transcript.txt` / `.srt` and the other selected formats, plus `mic.flac` (and `system.flac` / `mix.flac` when enabled).
- **Liquid:** a path under the recording timer shows where the session will go before recording, the current folder while recording, and the saved folder afterwards. The folder button opens it in Finder, and a Choose button sits next to the Sessions folder field.
- **PyQt:** the Live folder defaults to `~/Documents/GigaAM/live` (it used to be the batch output folder) and is created automatically. The Open button opens the last session, and the status after Stop shows its name.

### What to check

- Press Record: "Loading the recognition model…" shows first, then the timer starts. Say a short phrase right away ("Привет, как дела?"): the text should appear about a second after it, without having to repeat it.
- After stopping, press the folder button (Liquid) or Open (PyQt): the `YYYY-MM-DD_HH-MM-SS` folder with the transcript and audio opens.
- Speak for 30+ seconds: the draft grows without repeated words.

The TUI is unchanged: it has no Live mode, and only Liquid uses the worker's `live_*` commands.

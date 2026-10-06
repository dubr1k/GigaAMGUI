# GigaAM Transcriber 2.6.0

Релиз надёжности: исправлены десятки ошибок во всех версиях приложения — от потерянных слов на стыках длинных записей до зависавшей остановки Live и уязвимостей веб-панели. TUI обновляется до 2.2.0.

## Исправлено

### Распознавание (все версии)

- **Длинные записи больше не теряют слова на стыках фрагментов.** Например, «…составила 3,5 миллиона рублей» раньше превращалось в «…3,5 миллиона», а тире могло задвоиться. Многоточие на стыке больше не сбивает тайминги субтитров.
- **ONNX работает с выбранной папкой данных и в Docker.** Раньше распознавание и разметка спикеров на ONNX падали с «модель не найдена», если в приложении была выбрана своя папка данных.
- **Понятная причина ошибки.** Если файл не обработан, приложение показывает почему (а не «Обработка не удалась» или «смотрите лог»). Сбой разметки спикеров больше не выдаётся за проблему с HF_TOKEN.
- Новая папка результатов, указанная в CLI, больше не приводит к ошибке каждого файла; недописанные временные WAV не остаются в папке результатов.
- Ответы LLM от локальных серверов больше не превращаются в кракозябры.

### Live

- **Остановка всегда завершается.** Если при сохранении что-то шло не так, запись могла застрять в «Остановка…» без файлов. Теперь сессия сохраняется, а проблемы показываются отдельно. Индикатор записи экрана на macOS гаснет, даже если источник отвалился.
- **Длинная непрерывная речь** (вебинар, музыка) теперь даёт финальный текст не реже чем раз в 25 секунд, а не одним куском при остановке.
- Распознаётся речь с любого канала микрофона, а не только с первого; одна фраза больше не появляется дважды (черновиком после финала).
- **Windows:** системный звук, который молчит в паузах, больше не выключает общую дорожку и не сдвигает реплики во времени. **macOS (PyQt):** системный звук читается без искажений.
- Записи длиннее ~15 минут сохраняются целиком: в списке файлов и в разметке спикеров учитываются все части записи.

### PyQt

- «Очистить всё» во время обработки больше не запускает вторую обработку параллельно первой.
- Закрытие приложения во время Live или запроса к LLM спрашивает подтверждение и сохраняет сессию, а не обрывает её.
- Старт Live больше не подвешивает окно на время загрузки модели.
- Английский интерфейс переведён полностью; файлы, открытые через Finder или Dock, попадают в очередь (macOS).
- Исправлены: дубли строк в результате, переход на вкладку LLM, список транскриптов LLM после обработки, очистка сохранённого ключа API, синхронизация вкладки «Настройки», тёмная тема примеров API.

### Liquid (macOS)

- Пакетная обработка больше не обрывается с «malformed JSON» после ошибки в одном файле, и большие пакеты не сообщают о сбое в самом конце.
- Закрытие приложения во время Live спрашивает подтверждение и сохраняет сессию.
- Ответ ассистента в Live показывается полностью; приложение не накапливает память с каждым запросом.
- «Системная» тема следует macOS; исправлены недостающие переводы и списки, уходившие за край экрана.

### TUI 2.2.0

- **Windows:** имена файлов на кириллице и пути с `\` в кавычках больше не ломают очередь; результаты открываются в Проводнике.
- Разметка спикеров через pyannote снова работает: `gigaam --update` поставит проверенные версии pyannote.audio 3.1.1 и transformers 4.57.
- Закрытие терминала больше не оставляет работающие процессы; настройки, изменённые в настольном приложении, не затираются TUI.

### Веб-панель и Docker

- Закрыты уязвимости: загрузка до входа могла заполнить диск сервера; через настройки LLM-провайдера с действующей сессией можно было запустить любую команду; не было защиты от CSRF и ограничения попыток входа.
- Повторный вход без перезагрузки страницы больше не загружает каждый файл дважды; провайдер oh-my-pi снова выбирается.
- Образ Docker больше не может унести локальные файлы ключей и бэкапы `.env`; папки `uploads/`, `results/`, `logs/` доступны приложению, даже если Docker создал их сам.

## Изменено

- **«Остановить» прерывает текущий файл** (TUI, Liquid, PyQt) и не ждёт его окончания; результаты прерванного файла не сохраняются.
- **Веб-панель:** путь и аргументы LLM CLI берутся из настроек сервера. Прежнее поведение включается переменной `WEB_ALLOW_CLIENT_LLM_CLI=1`. Новые настройки: `WEB_TRUSTED_ORIGINS`, `WEB_LOGIN_RATE_LIMIT`, `WEB_MAX_CONCURRENT_LLM`, `WEB_MAX_LLM_BODY_SIZE`.
- **CLI** завершается с кодом 1, если хотя бы один файл не обработан, и с кодом 130 по Ctrl-C.
- **Промпты LLM** («Выжимка», «Задачи») одинаковые во всех версиях; в TUI, Liquid и MCP они стали подробнее.
- **Live:** «Оценка спикеров вживую» убрана из вариантов — ни один движок её не поддерживал; спикеры размечаются после остановки.
- `ONNX_MODEL_DIR` теперь корневая папка с подпапкой на каждую модель; старая раскладка по-прежнему читается.

## Что проверить

- Длинная запись (30+ минут) с диаризацией в любой версии: на стыках фрагментов нет пропавших или задвоенных слов.
- PyQt / Liquid: начните Live, скажите пару фраз и закройте приложение — появится вопрос, а после подтверждения в папке сессии будут транскрипт и аудио.
- Остановите пакет из нескольких файлов: текущий файл прерывается сразу, следующие не начинаются.
- Веб-панель: войдите, выйдите и войдите снова без перезагрузки — один файл загружается один раз.
- TUI: `gigaam --update`, затем обработка файла с диаризацией pyannote.
- Windows (TUI): файл с кириллицей в имени из папки с пробелами, затем открыть результат из списка результатов.

---

## English

A reliability release: dozens of bugs fixed across every version of the app — from words lost at the seams of long recordings to a Live stop that could hang and security holes in the web panel. The TUI moves to 2.2.0.

## Fixed

### Recognition (all versions)

- **Long recordings no longer lose words where fragments join.** For example, "…составила 3,5 миллиона рублей" used to become "…3,5 миллиона", and a dash could be doubled. An ellipsis at a seam no longer throws off subtitle timings.
- **ONNX works with a chosen data folder and in Docker.** ONNX recognition and speaker labelling used to fail with "model not found" when the app used a custom data folder.
- **A clear reason for failures.** When a file is not processed, the app shows why (instead of "Processing failed" or "see the log"). A speaker-labelling failure is no longer reported as an HF_TOKEN problem.
- A new output folder given to the CLI no longer makes every file fail; half-written temporary WAV files no longer stay in the output folder.
- LLM answers from local servers are no longer garbled.

### Live

- **Stopping always finishes.** If something went wrong while saving, a recording could hang in "Stopping…" with no files. The session is now saved and problems are reported separately. The macOS screen-recording indicator goes off even when a source failed.
- **Long continuous speech** (a webinar, music) now produces final text at least every 25 seconds instead of one block at stop.
- Speech on any microphone channel is recognised, not only on the first one; a phrase no longer appears twice (as a draft after its final).
- **Windows:** system audio that goes silent in pauses no longer turns off the combined track or shifts lines in time. **macOS (PyQt):** system audio is read without distortion.
- Recordings longer than ~15 minutes are kept whole: every part of the recording is listed and used for speaker labelling.

### PyQt

- "Clear all" during processing no longer starts a second run next to the first one.
- Closing the app during Live or an LLM request asks first and saves the session instead of cutting it off.
- Starting Live no longer freezes the window while the model loads.
- The English interface is fully translated; files opened from Finder or the Dock are queued (macOS).
- Fixed: duplicated rows in the result view, switching to the LLM tab, the LLM transcript list after a batch, clearing a saved API key, syncing the Settings tab, the dark theme of the API examples.

### Liquid (macOS)

- A batch no longer aborts with "malformed JSON" after one file fails, and large batches no longer report a failure at the very end.
- Closing the app during Live asks first and saves the session.
- The assistant's Live answer is shown in full; the app no longer accumulates memory with every request.
- The "System" theme follows macOS; missing translations and lists running off the screen are fixed.

### TUI 2.2.0

- **Windows:** Cyrillic file names and quoted paths with `\` no longer break the queue; results open in Explorer.
- Speaker labelling with pyannote works again: `gigaam --update` installs the tested pyannote.audio 3.1.1 and transformers 4.57.
- Closing the terminal no longer leaves processes running; settings changed in the desktop app are no longer overwritten by the TUI.

### Web panel and Docker

- Security fixes: uploads were read before login and could fill the server's disk; with a valid session, the LLM provider settings could run any command; there was no CSRF protection and no limit on login attempts.
- Logging in again without reloading the page no longer uploads every file twice; the oh-my-pi provider can be selected again.
- The Docker image can no longer pick up local key files and `.env` backups; the `uploads/`, `results/` and `logs/` folders are writable by the app even when Docker created them.

## Changed

- **Stop interrupts the current file** (TUI, Liquid, PyQt) instead of waiting for it to finish; nothing is saved for the interrupted file.
- **Web panel:** the LLM CLI path and arguments come from the server's settings. The previous behaviour is available with `WEB_ALLOW_CLIENT_LLM_CLI=1`. New settings: `WEB_TRUSTED_ORIGINS`, `WEB_LOGIN_RATE_LIMIT`, `WEB_MAX_CONCURRENT_LLM`, `WEB_MAX_LLM_BODY_SIZE`.
- **CLI** exits with code 1 when any file failed and with 130 on Ctrl-C.
- **LLM prompts** ("Summary", "Tasks") are the same in every version; in the TUI, Liquid and MCP they are now more detailed.
- **Live:** the "live speaker estimate" choice is removed — no engine supported it; speakers are labelled after stop.
- `ONNX_MODEL_DIR` is now a root folder with one subfolder per model; the old layout is still read.

## What to check

- A long recording (30+ minutes) with diarization in any version: no lost or doubled words where fragments join.
- PyQt / Liquid: start Live, say a couple of phrases and close the app — you are asked first, and after confirming the session folder holds the transcript and audio.
- Stop a batch of several files: the current file is interrupted at once and the next ones do not start.
- Web panel: log in, log out and log in again without reloading — one file is uploaded once.
- TUI: `gigaam --update`, then process a file with pyannote diarization.
- Windows (TUI): a file with a Cyrillic name from a folder with spaces, then open the result from the results list.

# GigaAM Transcriber 2.3.0

Релиз про терминальный TUI. Главное: TUI наконец пользуется реестром
LLM-провайдеров воркера, показывает ответ LLM по мере генерации, делит
настройки с десктопным приложением, умеет работать без экрана — из скриптов и
от имени агентов (`gigaam transcribe`, `gigaam llm`) — и обновляется одной
командой `gigaam --update`. Плюс небольшое исправление в PyQt-оболочке.

## Добавлено

- **TUI: реестр LLM-провайдеров из воркера.** `/settings` показывает
  провайдера с версией установленного CLI (`Claude Code · 2.1.278`,
  `oh-my-pi · 18.2.6`, `Codex · not installed`) вместо статичного списка.
  Новые команды: `/llm-path` (путь к бинарю), `/llm-provider-name`
  (внутренний provider для Pi/oh-my-pi), `/llm-args` (дополнительные
  аргументы CLI), `/llm-tools on|off` (разрешить агенту инструменты и
  сессию). Провайдер «Other» теперь действительно запускается.
- **TUI: стриминг ответа LLM.** Текст появляется по мере поступления,
  результат остаётся на экране (`r` — показать/скрыть). `Esc` во время LLM
  отменяет только LLM-запрос (`llm_cancel`), второй `Esc` — по-прежнему
  аварийный перезапуск воркера.
- **`/llm-file <путь>`** — любой `.txt`/`.md`/`.srt`/`.vtt` в очередь LLM, не
  только результаты текущей сессии.
- **`/audio-mode auto|off|light|denoise`** — режим предобработки звука, как в
  десктопном приложении; активный режим виден в шапке.
- **TUI делит настройки с десктопным приложением.** Если установлена основная
  программа (есть её `user_settings.json`), общие ключи — backend и модель,
  форматы, диаризация, число спикеров, настройки LLM — читаются и пишутся в
  тот же файл, а `LLM_API_KEY` — в `.env` рядом; ключ никогда не попадает в
  `tui_settings.json`. Без десктопной программы всё остаётся в
  `tui_settings.json`. Выигрывает тот, кто сохранил последним; другая
  программа подхватывает изменения при следующем запуске.
- **Headless-режим для скриптов и агентов.**
  `gigaam transcribe FILE... [--formats txt,srt] [--diarize] [--output DIR]
  [--json]` и `gigaam llm FILE... --mode summary|tasks|terms|custom [--json]`
  используют те же воркер и настройки, что интерактивный TUI. Одна строка
  на файл на stdout, прогресс на stderr (`--quiet`), `--json` — поток событий
  воркера построчно. Коды выхода: `0` успех, `1` хотя бы один файл с ошибкой,
  `2` неверные аргументы или нет входного файла, `3` воркер не запустился.
- **Skill для агентов** — `skills/gigaam/SKILL.md` описывает команды, формат
  `--json`, коды выхода и куда ложатся результаты. Установщик кладёт его в
  `~/.claude/skills`, `~/.codex/skills`, `~/.agents/skills` (только в
  существующие каталоги); `gigaam --install-skill` — переустановить.
- **`gigaam --update [--ref REF]`** обновляет TUI и окружение воркера,
  прописывает `~/.local/bin` в PATH (fish `conf.d`, `.zshrc`, `.bashrc`);
  `gigaam --version` — установленная ревизия. Логика живёт в репозитории
  (`scripts/tui/gigaam-launcher.sh`) и обновляется вместе с ним.
- **Установщик** сохраняет выбранную модель и `.venv`/Rust-таргет между
  обновлениями (`--fresh` — с нуля), ставит MLX-зависимости на Apple Silicon,
  чтобы синхронизированный из десктопа backend `mlx` работал и в TUI
  (`--no-mlx`), не трогает rc-файлы по `--no-path`, не ставит skill по
  `--no-skill`.

## Исправлено

- CHANGELOG 2.2.0 обещал, что TUI спрашивает у воркера `llm_tools`, — до
  этого релиза ответ игнорировался и в `llm_start` уходили заглушечные имена
  бинарей. Теперь TUI использует найденные воркером пути.
- Тесты TUI писали в реальный `tui_settings.json` разработчика; теперь они
  изолированы через `GIGAAM_CONFIG_DIR`.
- Битый `user_settings.json` десктопа больше не стирает `LLM_API_KEY` из
  `.env`: признак «десктоп установлен» один и тот же для чтения и записи, а
  пустой ключ в `.env` никогда не пишется.
- `gigaam --update` не сбрасывает модель и не пересобирает окружение на каждом
  запуске; каталог настроек на macOS совпадает с тем, что читают TUI и Python
  (`~/Library/Application Support/GigaAMTranscriber`, `XDG_CONFIG_HOME`
  игнорируется, `GIGAAM_CONFIG_DIR` — единственное переопределение).
- PyQt: главная панель вкладок отцентрована — раньше шесть вкладок прижимались
  влево, оставляя пустую полосу справа.

## Что проверить

- `gigaam --update`, затем `gigaam --version` — ревизия `v2.3.0`; модель в
  настройках не сброшена.
- `/settings` в TUI показывает версии установленных CLI.
- `gigaam transcribe запись.wav --formats txt,srt` из любого каталога — файлы
  рядом с записью, код выхода `0`; `gigaam transcribe нет.wav` — код `2`.
- При установленном десктопном приложении смена `/backend` в TUI видна в
  десктопе после его перезапуска, и наоборот.

---

## English

A terminal-TUI release. Highlights: the TUI finally uses the worker's LLM
provider registry, streams LLM answers as they are generated, shares its
settings with the desktop app, works headless — from scripts and on behalf of
agents (`gigaam transcribe`, `gigaam llm`) — and updates itself with a single
`gigaam --update`. Plus a small PyQt shell fix.

### Added

- **TUI: LLM provider registry from the worker.** `/settings` shows each
  provider with the installed CLI version (`Claude Code · 2.1.278`,
  `oh-my-pi · 18.2.6`, `Codex · not installed`) instead of a static list. New
  commands: `/llm-path` (binary path), `/llm-provider-name` (internal
  provider for Pi/oh-my-pi), `/llm-args` (extra CLI arguments),
  `/llm-tools on|off` (let the agent use tools and sessions). The "Other"
  provider actually runs now.
- **TUI: streamed LLM output.** Text appears as it arrives and the result
  stays on screen (`r` toggles it). `Esc` during an LLM run cancels only the
  LLM request (`llm_cancel`); a second `Esc` still force-restarts the worker.
- **`/llm-file <path>`** — feed any `.txt`/`.md`/`.srt`/`.vtt` transcript to
  the LLM, not only this session's results.
- **`/audio-mode auto|off|light|denoise`** — audio preprocessing mode, as in
  the desktop app; the active mode is shown in the header.
- **TUI shares settings with the desktop app.** When the desktop app is
  installed (its `user_settings.json` exists), the shared keys — backend and
  model, formats, diarization, speaker count, LLM settings — are read from
  and written to that file, and `LLM_API_KEY` lives in the `.env` next to it;
  the key never lands in `tui_settings.json`. Without the desktop app
  everything stays in `tui_settings.json`. Last writer wins; the other
  program picks changes up on its next start.
- **Headless mode for scripts and agents.**
  `gigaam transcribe FILE... [--formats txt,srt] [--diarize] [--output DIR]
  [--json]` and `gigaam llm FILE... --mode summary|tasks|terms|custom
  [--json]` use the same worker and saved settings as the interactive TUI.
  One line per file on stdout, progress on stderr (`--quiet`), `--json` —
  one worker event per line. Exit codes: `0` success, `1` at least one file
  failed, `2` bad arguments or a missing input file, `3` worker unavailable.
- **Agent skill** — `skills/gigaam/SKILL.md` documents the commands, the
  `--json` stream, exit codes and where results go. The installer copies it
  into `~/.claude/skills`, `~/.codex/skills` and `~/.agents/skills` (only
  where those directories exist); `gigaam --install-skill` reinstalls it.
- **`gigaam --update [--ref REF]`** updates the TUI and the worker
  environment and registers `~/.local/bin` in PATH (fish `conf.d`, `.zshrc`,
  `.bashrc`); `gigaam --version` prints the installed revision. The logic
  lives in the repository (`scripts/tui/gigaam-launcher.sh`) and updates
  with it.
- **Installer** keeps the selected model and the `.venv`/Rust target across
  updates (`--fresh` for a clean reinstall), installs the MLX dependencies on
  Apple Silicon so the `mlx` backend synced from the desktop app works in the
  TUI too (`--no-mlx`), leaves rc files alone with `--no-path`, skips the
  skill with `--no-skill`.

### Fixed

- The 2.2.0 changelog promised the TUI queries the worker's `llm_tools`; until
  this release the reply was ignored and placeholder binary names were sent
  in `llm_start`. The TUI now uses the paths the worker discovered.
- TUI tests were writing to the developer's real `tui_settings.json`; they are
  now isolated through `GIGAAM_CONFIG_DIR`.
- A corrupt desktop `user_settings.json` no longer wipes `LLM_API_KEY` from
  `.env`: load and save use the same "desktop installed" predicate, and an
  empty key is never written to `.env`.
- `gigaam --update` no longer resets the model or rebuilds the environment on
  every run; the macOS settings directory matches what the TUI and the Python
  side read (`~/Library/Application Support/GigaAMTranscriber`;
  `XDG_CONFIG_HOME` is ignored, `GIGAAM_CONFIG_DIR` is the only override).
- PyQt: the main tab bar is centred — six tabs used to sit flush-left with an
  empty strip on the right.

### What to check

- `gigaam --update`, then `gigaam --version` shows `v2.3.0`; the model in
  settings is unchanged.
- `/settings` in the TUI shows the installed CLI versions.
- `gigaam transcribe recording.wav --formats txt,srt` from any directory
  writes next to the recording and exits `0`; `gigaam transcribe missing.wav`
  exits `2`.
- With the desktop app installed, changing `/backend` in the TUI is visible
  in the desktop app after it restarts, and vice versa.

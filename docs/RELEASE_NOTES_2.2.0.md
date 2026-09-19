# GigaAM Transcriber 2.2.0

Релиз про LLM-провайдеры: добавлен oh-my-pi, а поиск и настройка CLI-инструментов
переписаны с нуля. Главная боль, которую он закрывает: приложение, запущенное из
Finder/Dock, не видело `claude`/`codex`/`omp` из homebrew или npm и требовало
вписывать абсолютные пути руками.

## Добавлено

- **oh-my-pi (`omp`)** — новый провайдер во всех интерфейсах (PyQt, Liquid, web,
  TUI). Печатный режим, внутренний provider, модель (нечёткое совпадение:
  `opus`, `gpt-5.2`, `openai/gpt-5.2`) и доп. аргументы.
- **Автопоиск CLI.** Помимо `PATH` проверяются типичные каталоги установки
  (homebrew, `~/.local/bin`, bun, npm-global, volta, cargo, все версии nvm, pnpm;
  на Windows — `%APPDATA%\npm`, scoop, bun, pnpm). Найденный бинарь проверяется
  запуском `--version`. Тот же расширенный `PATH` получает дочерний процесс, так
  что npm-шимы `claude`/`opencode` находят `node`.
- **Экран «Инструменты».** Страница LLM показывает выбранного провайдера с бейджем
  `● oh-my-pi 18.2.5` / `○ Pi — не найден` / `⚠ не запускается`. В настройках —
  таблица всех CLI: статус, версия, путь, «Обзор…», «Проверить»,
  «Пересканировать»; для отсутствующего инструмента подсказка с командой
  установки. Пустой путь = автопоиск.
- **«Разрешить инструменты и сессии агента»** — выключено по умолчанию. Выжимка
  транскрипта запускается как чистый запрос к модели: `claude --tools ""
  --no-session-persistence`, `opencode run --pure`, `pi`/`omp --no-tools
  --no-session`. Агент не пойдёт читать файлы и не засорит свою историю сессий.
- **Retry для API-провайдера** на 429/5xx с `Retry-After` и бэкоффом; ожидание
  прерывается отменой. Плюс `start_gigaam.bat` для Windows. Спасибо
  @kotovasia5120 (PR #55).

## Исправлено

- Gemini через OpenAI-совместимый endpoint (`…/v1beta/openai/`) — лишний `/v1`
  больше не добавляется (PR #55).
- Промпт CLI-провайдерам передаётся через stdin, а не argv: на Linux один
  аргумент ограничен 128 KiB, длинные транскрипты падали с `E2BIG`. Для
  провайдера «Другое» поведение прежнее; маркер `{stdin}` в аргументах
  переключает на stdin.
- OpenCode вызывался без `run` и открывал TUI вместо ответа.
- Путь к CLI больше не «замораживается» в настройках при первом запуске.
- Диалог настроек LLM (PyQt) прокручивается на невысоких экранах.

## Для разработчиков

- `src/services/cli_tools.py` — реестр провайдеров и резолвер. Новый провайдер =
  одна запись `ProviderSpec` + билдер команды в `llm_service.py`; фронтенды
  список не хардкодят (web — `GET /api/llm/tools`, Liquid/TUI — команды воркера
  `llm_tools` / `llm_tool_check`).
- Правило проекта в `AGENTS.md`: никакой AI-атрибуции в коммитах, PR,
  комментариях и релиз-нотах.

## Что проверить после обновления

1. Запустить приложение из Finder/Dock (не из терминала), открыть LLM: бейдж у
   провайдера должен показать версию, а не «не найден».
2. Настройки → LLM → «Пересканировать»: все установленные CLI зелёные.
3. Выбрать oh-my-pi, ввести модель (например `opus`), нажать «Обработать».

---

## English

An LLM-provider release: oh-my-pi is added, and CLI tool discovery and
configuration are rewritten from scratch. The main pain it removes: an app
launched from Finder/Dock could not see `claude`/`codex`/`omp` installed via
Homebrew or npm and required absolute paths typed by hand.

**Added**

- **oh-my-pi (`omp`)** — a new provider in every interface (PyQt, Liquid, web,
  TUI): print mode, inner provider, model (fuzzy match: `opus`, `gpt-5.2`,
  `openai/gpt-5.2`) and extra arguments.
- **CLI auto-discovery.** Besides `PATH`, the usual install locations are
  checked (Homebrew, `~/.local/bin`, bun, npm-global, volta, cargo, every nvm
  version, pnpm; on Windows `%APPDATA%\npm`, scoop, bun, pnpm). A found binary
  is verified by running `--version`. The child process gets the same extended
  `PATH`, so npm shims for `claude`/`opencode` find `node`.
- **"Tools" screen.** The LLM page shows the selected provider with a badge
  (`● oh-my-pi 18.2.5` / `○ Pi — not found` / `⚠ fails to start`). Settings list
  every CLI with status, version, path, "Browse…", "Check", "Rescan", and an
  install hint for a missing tool. An empty path means auto-discovery.
- **"Allow agent tools and sessions"** — off by default. Transcript
  summarisation runs as a plain model call: `claude --tools ""
  --no-session-persistence`, `opencode run --pure`, `pi`/`omp --no-tools
  --no-session`. The agent will not read files or pollute its session history.
- **Retries for the API provider** on 429/5xx with `Retry-After` and backoff;
  the wait is interrupted by Cancel. Plus `start_gigaam.bat` for Windows.
  Thanks to @kotovasia5120 (PR #55).

**Fixed**

- Gemini through the OpenAI-compatible endpoint (`…/v1beta/openai/`) — the extra
  `/v1` is no longer appended (PR #55).
- Prompts are passed to CLI providers via stdin rather than argv: on Linux a
  single argument is capped at 128 KiB and long transcripts failed with
  `E2BIG`. The "Other" provider keeps the old behaviour; a `{stdin}` marker in
  its arguments switches to stdin.
- OpenCode was invoked without `run` and opened its TUI instead of answering.
- The CLI path is no longer frozen into settings on first launch.
- The LLM settings dialog (PyQt) scrolls on short screens.

**For developers**

- `src/services/cli_tools.py` is the provider registry and resolver. A new
  provider is one `ProviderSpec` entry plus a command builder in
  `llm_service.py`; front-ends do not hard-code the list (web —
  `GET /api/llm/tools`, Liquid/TUI — worker commands `llm_tools` /
  `llm_tool_check`).
- Project rule in `AGENTS.md`: no AI attribution in commits, PRs, comments or
  release notes.

**What to check after updating:** launch the app from Finder/Dock (not a
terminal) and open LLM — the provider badge should show a version, not "not
found"; Settings → LLM → "Rescan" — every installed CLI is green; pick
oh-my-pi, enter a model (e.g. `opus`) and press "Process".

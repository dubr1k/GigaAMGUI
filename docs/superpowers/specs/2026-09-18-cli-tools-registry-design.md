# CLI-провайдеры LLM: реестр, резолвер и единый UX (2.2)

Дата: 2026-09-18. Статус: утверждено (подход A).

## Проблема

Список LLM-провайдеров захардкожен в шести местах (сервис, четыре PyQt-миксина,
web html/js/py, TUI, Liquid + тест на строку popup). Поиск бинарей — голый
`shutil.which()` по `PATH` процесса; GUI-приложения из Finder/Dock получают
`PATH=/usr/bin:/bin:/usr/sbin:/sbin` и не видят homebrew/bun/nvm. Путь к CLI
замораживается в настройках при первом старте. Промпт передаётся argv-аргументом
(Linux: лимит 128 KiB на аргумент → `E2BIG` на длинных транскриптах). Агентные CLI
запускаются с инструментами и сохранением сессий. `opencode` вызывается без `run`.
Нет oh-my-pi.

## Цели

1. Один источник правды: `src/services/cli_tools.py` — реестр провайдеров и
   резолвер бинарей. Новый провайдер = одна запись в реестре.
2. Поиск инструментов: `PATH` + известные каталоги установки (macOS/Linux/Windows)
   → проверка запуска `--version` → версия. Кэш на процесс, `rescan()`.
3. Запуск CLI: промпт через stdin, safe-флаги по умолчанию
   (галочка «Разрешить инструменты и сессии» снимает), окружение дочернего процесса
   с расширенным `PATH` (npm-шимы `claude`/`opencode` требуют `node` в `PATH`).
4. oh-my-pi (`omp`) как провайдер.
5. UX во всех фронтендах: провайдер выбирается из **найденных** инструментов с
   бейджами статуса/версии; отдельная секция «Инструменты» со статусом, путём,
   «Обзор…», «Проверить», «Пересканировать».
6. PR #55 (Gemini endpoint, retry 429/5xx, `start_gigaam.bat`) входит в 2.2.

## Не цели

Автоустановка CLI; перечисление моделей у провайдеров; смена формата настроек
(ключи `claude_path` и т.д. сохраняются).

## Архитектура

### `src/services/cli_tools.py`

```python
@dataclass(frozen=True)
class ProviderSpec:
    id: str                 # "api" | "claude" | "codex" | "opencode" | "pi" | "omp" | "other"
    name: str               # каноническое имя в настройках: "API", "Claude Code", "Codex",
                            # "OpenCode", "Pi", "oh-my-pi", "Other"
    binary: str | None      # "claude", "codex", "opencode", "pi", "omp"; None для api/other
    settings_prefix: str    # "claude" → ключи claude_path / claude_args / (pi|omp)_provider
    install_hint: str       # "brew install can1357/tap/omp" / "npm i -g @anthropic-ai/claude-code"
    has_provider_field: bool  # pi, omp
    version_args: tuple[str, ...] = ("--version",)

PROVIDERS: tuple[ProviderSpec, ...]       # порядок = порядок в UI
def provider_by_name(name) -> ProviderSpec   # "Другое"/"Other" → other
def canonical_provider_names() -> list[str]

@dataclass(frozen=True)
class ToolStatus:
    provider: str          # spec.name
    id: str
    status: str            # "found" | "missing" | "broken" | "not_applicable"
    path: str | None
    version: str | None
    detail: str | None     # текст ошибки для broken
    install_hint: str
    def to_dict(self) -> dict

def search_dirs() -> list[str]            # PATH + platform extra dirs, дедуп, только существующие
def child_environment(base=None) -> dict  # env с PATH=search_dirs()
def resolve_tool(spec, override: str | None = None) -> ToolStatus
def scan(overrides: dict[str, str] | None = None, *, fresh=False) -> list[ToolStatus]
```

Известные каталоги (в дополнение к `PATH`):

- macOS/Linux: `/opt/homebrew/bin`, `/usr/local/bin`, `~/.local/bin`, `~/.bun/bin`,
  `~/.npm-global/bin`, `~/.volta/bin`, `~/.cargo/bin`, `~/.omp/bin`, `~/.pi/bin`,
  `~/.nvm/versions/node/*/bin` (все версии, новые первыми), `/snap/bin` (Linux),
  `~/.local/share/pnpm`, `~/Library/pnpm` (macOS).
- Windows: `%APPDATA%\npm`, `%LOCALAPPDATA%\pnpm`, `%USERPROFILE%\.bun\bin`,
  `%USERPROFILE%\scoop\shims`, `%LOCALAPPDATA%\Programs\*\` не сканируем
  (только известные имена), `%USERPROFILE%\.cargo\bin`.

`override`: явный путь из настроек. Если он абсолютный/содержит разделитель —
проверяется только он; если голое имя — ищется по `search_dirs()`.

Проверка запуска: `[path, "--version"]`, таймаут 10 с, `startupinfo` для Windows,
`env=child_environment()`. Версия — первое совпадение `\d+\.\d+(\.\d+)?` в
stdout+stderr. `OSError`/таймаут/ненулевой код → `broken` с `detail`.

`scan()` — параллельно (ThreadPool, ≤6) по всем CLI-провайдерам; результат кэшируется
на процесс с ключом `overrides`; `fresh=True` сбрасывает.

### `src/services/llm_service.py`

- Команды строятся через реестр; промпт всегда через stdin (`input_text`).
  - claude: `[-p, --output-format, text, (--model M), safe: --tools "" --no-session-persistence, args]`
  - codex: без изменений (уже stdin `-`); safe: `--sandbox read-only` не добавляем
    (ломает `exec` на части версий) — оставляем как есть.
  - opencode: `[run, --pure(safe), (-m M), args]` — **добавлен `run`**.
  - pi / omp: `[-p, --mode, text, (--provider P), (--model M), safe: --no-tools --no-session, args]`
  - other: argv как раньше **плюс** stdin (обратная совместимость: промпт остаётся
    последним аргументом, если в `other_args` нет маркера `{stdin}`; при маркере —
    промпт только в stdin, маркер убирается).
- `settings["llm_allow_tools"]` (bool, default False) — если True, safe-флаги не
  добавляются.
- `_run_command` получает `env=child_environment()`; путь бинаря — из
  `resolve_tool(spec, settings[f"{prefix}_path"])`, при `missing/broken` —
  `RuntimeError` с человекочитаемым текстом и `install_hint`.
- `run_provider` принимает `"oh-my-pi"`.

### `src/utils/llm_client.py`

Из PR #55 (cherry-pick с авторством) + доработка: ожидание в `_post_with_retry`
через `_sleep_with_cancel(delay, cancel_check)` шагами 0.1 с; тест на `Retry-After`
числовой/мусорный.

### Транспорт для фронтендов

- PyQt: прямой импорт, скан в `QThread` (не блокировать UI).
- web: `GET /api/llm/tools` → `{"providers": [ToolStatus...]}`;
  `POST /api/llm/tools/check` `{provider, path}` → один `ToolStatus`.
- TUI/Liquid: команды воркера `{"type": "llm_tools", "overrides": {...}, "fresh": bool}`
  → `{"type": "llm_tools", "providers": [...]}`; `{"type": "llm_tool_check", "provider", "path"}`
  → `{"type": "llm_tool_check", "tool": {...}}`.

### PyQt

- Страница LLM: строка «Провайдер: [combo] Модель: [edit]» над блоком «3. Что
  сделать» (combo = `self.combo_llm_provider`, перенесён из диалога; элементы —
  `● oh-my-pi 18.2.5` / `○ Pi — не найден`, недоступные серые, но выбираемые с
  подсказкой). Модель = `self.entry_llm_model` (перенесён).
- Диалог «Настройки LLM»: группа «Инструменты» — `QTableWidget` (Статус, Инструмент,
  Версия, Путь, [Обзор…], [Проверить]) + кнопка «Пересканировать»; под таблицей —
  поля выбранного провайдера (аргументы, provider для pi/omp) и API-поля для API;
  чекбокс «Разрешить инструменты и сессии агента». Промпты — без изменений.
- Валидация `_collect_llm_settings` через `resolve_tool`; текст ошибки включает
  `install_hint`.
- Настройки: `llm_omp_path/args/provider`, `llm_allow_tools`; путь **не**
  замораживается — в `user_settings` пишем только то, что пользователь ввёл сам.
- i18n через `_apply_language`/`_t`.

### Liquid

- Страница LLM: popup провайдера с бейджами (`● Claude Code 2.1.3`), данные от
  воркера при открытии страницы (`llm_tools`), кэш последнего ответа в
  `UserDefaults` (`llm.toolsCache`) для мгновенного первого рендера.
- Настройки → LLM: вместо пяти строк «путь/аргументы» — список инструментов:
  статус, версия, путь (editable), «Обзор…» (`NSOpenPanel`), «Проверить»
  (`llm_tool_check`), «Пересканировать». Аргументы/provider — для выбранного.
  Тоггл «Разрешить инструменты и сессии».
- `llmSettings()` добавляет `omp_*` и `llm_allow_tools`; `llmProviders` берётся из
  ответа воркера (fallback — статический список с oh-my-pi).

### web

`index.html`/`app.js`: `<select>` заполняется из `/api/llm/tools` с бейджами;
блок omp; чекбокс allow tools; `web_app.py` принимает `omp_path/omp_args/omp_provider`,
`llm_allow_tools`.

### TUI (Rust)

Списки провайдеров дополняются `oh-my-pi`; `omp_path: "omp"`; путь не настраивается
(как и раньше) — резолвер Python найдёт по известным каталогам.

## Тесты

- `tests/test_cli_tools.py`: реестр (omp есть, порядок, `provider_by_name("Другое")`),
  `search_dirs` (PATH + extra, дедуп, только существующие; monkeypatch HOME/platform),
  `resolve_tool` (found/missing/broken с поддельным `subprocess.run`), override
  абсолютный/голый, `scan` кэш и `fresh`, `child_environment`.
- `tests/test_llm_service.py`: команды для omp/pi/claude/opencode со safe-флагами и без,
  промпт в stdin (не в argv), `opencode run`, `{stdin}` для other, ошибка при
  `missing` содержит `install_hint`.
- `tests/test_llm_client.py`: PR #55 + `Retry-After` парсинг + отмена во время ожидания.
- `tests/test_tui_worker.py`: `llm_tools` / `llm_tool_check`.
- `tests/test_web_app_persistence.py` или новый: `/api/llm/tools`.
- `tests/test_gui_llm_mixin.py`: combo на странице, таблица инструментов, omp в
  настройках, `llm_allow_tools`.
- `tests/test_macos_swift_packaging.py`: строка popup заменяется проверкой, что
  `llmProviders` содержит `oh-my-pi` и что страница использует `llm_tools`.

## Релиз 2.2.0

Bump по девяти точкам (`test_release_hardening`), `docs/CHANGELOG.md`,
`docs/RELEASE_NOTES_2.2.0.md` (`git add -f`), squash-merge PR #55 с благодарностью
и финальным комментарием, тег `v2.2.0`.

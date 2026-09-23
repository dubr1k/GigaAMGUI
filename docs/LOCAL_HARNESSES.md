# Локальный GigaAM для агентских harness на macOS

Практическая настройка Claude Code, Codex, Oh My Pi (OMP), Pi, OpenCode и
Hermes: распознавание на этом Mac, без удалённого сервера GigaAM и без
управления экраном TUI. Полный контракт инструментов — в [MCP.md](MCP.md).

## 1. Что именно подключается

Есть два независимых интерфейса к общему движку:

- **Headless CLI:** `gigaam transcribe` и `gigaam llm`. Процесс выполняет задание
  и завершается; `--json` отдаёт JSON Lines. TTY, окно приложения и MCP не нужны.
- **MCP stdio:** harness запускает `gigaam mcp`, общается через stdin/stdout и
  вызывает инструменты. При подключении веса не загружаются: каждый `transcribe`
  загружает свою ASR-модель и освобождает её после задания, в том числе при ошибке.
  Сам MCP-процесс остаётся подключённым; `connected` не означает «модель в памяти».

Это не удалённое управление уже открытым TUI: slash-команды TUI и его экран
не являются API для агента. Не запускайте просто `gigaam` из автоматизации.

Для локального MCP не нужны порт, HTTP-сервер или API-ключ GigaAM. Источник
`path` — путь на этом Mac. Для удалённого MCP это путь на удалённой машине,
а не локальный файл пользователя.

Название **`gigaam-local`** отделяет локальную регистрацию от существующей
удалённой `gigaam`. Не заменяйте удалённый сервер, если он ещё нужен.

> Локальный ASR не делает весь harness офлайн: его основная LLM и инструмент
> `summarize` могут обращаться к внешнему провайдеру. Текст результата MCP
> может попасть в контекст облачной LLM. Для чувствительных записей учитывайте
> настройки и политику выбранного harness.

## 2. Установка и изолированное окружение

### Новая установка

Из checkout этого репозитория:

```bash
bash scripts/install_tui.sh
~/.local/bin/gigaam --version
~/.local/bin/gigaam --help
```

Установщик создаёт собственный checkout и venv:

```text
~/.local/bin/gigaam                         launcher
~/.local/share/gigaam-tui/repo/              установленная копия исходников
~/.local/share/gigaam-tui/repo/.venv/        Python-окружение worker и MCP
```

Рабочий checkout разработчика и установленная копия — разные каталоги.
Установка пакета в `.venv` рабочего checkout не исправляет окружение команды
`~/.local/bin/gigaam`.

На Apple Silicon установщик добавляет MLX. Для другого оборудования не
копируйте `ASR_BACKEND=mlx`: выберите доступный backend и проверьте его отдельно.
Первый запуск модели требует сети и места на диске; следующие запуски используют
кэш. Наличие приложения или ONNX-моделей не означает наличие MLX-весов.

### Восстановление существующей установки через uv

`uv` умеет работать с уже существующим venv; создавать второй параллельный
venv для одного launcher не требуется. Не используйте системный Python.

```bash
PY="$HOME/.local/share/gigaam-tui/repo/.venv/bin/python"
uv pip install --python "$PY" 'yt-dlp==2026.3.17'
uv pip check --python "$PY"
~/.local/bin/gigaam mcp --help
```

Это исправляет известный случай старой TUI-установки: MCP установлен, но импорт
`src.utils.media_downloader` завершается с `No module named 'yt_dlp'`.
`requirements-tui.txt` теперь включает эту зависимость; установщик проверяет
`python -m src.mcp_server --help` до сообщения об успешной установке.

`uv pip install` сохраняет посторонние установленные пакеты, если они не
конфликтуют; **не используйте `uv pip sync` только с одним пакетом** — sync
удаляет зависимости, не перечисленные в переданном наборе.

Для штатного обновления установки:

```bash
~/.local/bin/gigaam --update
```

Не выполняйте `--fresh` ради одной недостающей зависимости: он пересоздаёт
окружение и кэш сборки.

## 3. Сначала проверьте headless-путь

Пример для macOS с установленным русским системным голосом Milena:

```bash
WORK=$(mktemp -d)
say -v Milena -o "$WORK/speech.wav" \
  --file-format=WAVE --data-format=LEI16@16000 \
  'Локальная интеграция работает. Проверяем распознавание речи через сервер.'
~/.local/bin/gigaam transcribe "$WORK/speech.wav" \
  --backend mlx --audio-mode off --formats txt,srt \
  --output "$WORK/out" --json
cat "$WORK/out/speech.txt"
```

Если голоса нет, используйте собственный короткий WAV. Успех — код завершения
`0`, событие `completed` с `success: true` и настоящие файлы TXT/SRT.
`--audio-mode off` здесь исключает дополнительную предобработку из smoke check,
а не задаёт обязательную настройку обычной транскрибации.

Коды завершения CLI:

| Код | Значение |
|---|---|
| 0 | Все файлы успешно обработаны |
| 1 | Ошибка обработки хотя бы одного файла |
| 2 | Неверные аргументы или отсутствующий входной файл |
| 3 | Worker недоступен / неожиданно завершился |

При `--json` диагностический stderr worker скрыт. Если причина ошибки
непонятна, повторите диагностический запуск без `--json` и без `--quiet`.

## 4. Общие правила MCP-конфигурации

1. Сделайте приватную резервную копию изменяемого конфига (`chmod 600`).
2. Добавляйте одну регистрацию, не заменяйте весь файл примером ниже.
3. В JSON/TOML/YAML указывайте **абсолютный путь** к launcher. `~` и `$HOME`
   в поле `command` обычно не раскрываются shell, потому что shell не запускается.
4. В примерах замените `/Users/you` своим домашним каталогом.
5. На Apple Silicon используйте `ASR_BACKEND=mlx`, если headless-проверка MLX
   прошла. Это переменная окружения дочернего MCP, а не изменение общих настроек.
6. После изменения настроек перезапустите harness или используйте его штатную
   команду перезагрузки MCP. Уже открытая сессия может хранить старый список tools.
7. Не выдавайте автоматическое разрешение всем инструментам ради подключения;
   оставьте существующие правила подтверждения действий.

Каждый harness обычно запускает **свой MCP-процесс**, а каждое распознавание
получает отдельную модель на время задания. Шесть простаивающих клиентов не
держат шесть ASR-моделей. Одновременные задания всё же загружают отдельные веса.
Лимит `server_status().busy` относится к конкретному серверу. Не запускайте
большие транскрибации одновременно во всех клиентах.

### Claude Code

Пользовательская регистрация через штатный CLI:

```bash
claude mcp add --scope user --transport stdio \
  --env ASR_BACKEND=mlx gigaam-local -- "$HOME/.local/bin/gigaam" mcp
```

В `~/.claude.json` соответствующий элемент `mcpServers`:

```json
{
  "gigaam-local": {
    "type": "stdio",
    "command": "/Users/you/.local/bin/gigaam",
    "args": ["mcp"],
    "env": {"ASR_BACKEND": "mlx"},
    "timeout": 3600000
  }
}
```

`timeout` — миллисекунды, здесь один час на вызов. Отдельного per-server
startup-поля нет; для долгой первой загрузки можно запустить клиент как
`MCP_TIMEOUT=120000 claude`. Эта переменная влияет на запуск всех MCP данного
процесса, поэтому не добавляйте её глобально без необходимости.

Проверка только этой регистрации:

```bash
claude mcp get gigaam-local
```

Ожидается `Status: Connected`. Интерактивная панель — `/mcp`.

### Codex

Добавьте в `~/.codex/config.toml`:

```toml
[mcp_servers.gigaam-local]
command = "/Users/you/.local/bin/gigaam"
args = ["mcp"]
startup_timeout_sec = 120
tool_timeout_sec = 3600

[mcp_servers.gigaam-local.env]
ASR_BACKEND = "mlx"
```

```bash
codex mcp get gigaam-local --json
```

Эта команда проверяет чтение регистрации, **не живое выполнение инструмента**.
Подключение проверяйте в новой сессии через `/mcp` и `server_status`, либо
протокольным smoke check из раздела 6.

### Oh My Pi (OMP)

В `~/.omp/agent/mcp.json`, внутри `mcpServers`:

```json
{
  "gigaam-local": {
    "type": "stdio",
    "command": "/Users/you/.local/bin/gigaam",
    "args": ["mcp"],
    "env": {"ASR_BACKEND": "mlx"},
    "timeout": 3600000
  }
}
```

`timeout` — миллисекунды. Не добавляйте выдуманный `startup_timeout_sec`:
это поле Codex, не OMP.

В интерактивной сессии OMP:

```text
/mcp test gigaam-local
/mcp reload
```

Проверить доступность скиллов без LLM-вызова:

```bash
omp read skill://gigaam
omp read skill://gigaam-mcp
```

### Pi

Pi использует MCP через расширение. В проверенной конфигурации установлен
`pi-mcp-adapter@2.37.0` (Pi 0.87.1). Если подходящий MCP-адаптер уже установлен,
не подключайте второй, дублирующий инструменты.

```bash
pi install npm:pi-mcp-adapter@2.37.0
```

В `~/.pi/agent/mcp.json`, внутри `mcpServers`:

```json
{
  "gigaam-local": {
    "command": "/Users/you/.local/bin/gigaam",
    "args": ["mcp"],
    "env": {"ASR_BACKEND": "mlx"},
    "requestTimeoutMs": 3600000
  }
}
```

Перезапустите Pi. `/mcp` открывает панель адаптера; регистрация должна быть
подключена, а список содержать инструменты GigaAM. По умолчанию адаптер может
вызывать их через общий MCP proxy tool: ноль *direct tools* не означает, что
сервер недоступен. `/mcp reconnect gigaam-local` повторяет подключение.

`pi --offline` отключает стартовые сетевые операции Pi, но не является
сетевой песочницей для расширений и MCP-серверов.

### OpenCode

В `~/.config/opencode/opencode.json` добавьте элемент в `mcp`:

```json
{
  "gigaam-local": {
    "type": "local",
    "command": ["/Users/you/.local/bin/gigaam", "mcp"],
    "environment": {"ASR_BACKEND": "mlx"},
    "enabled": true,
    "timeout": 120000
  }
}
```

У OpenCode `command` — **массив**, `environment` — не `env`, `type` — `local`.
Документированный `timeout` относится к получению инструментов; не считайте
его подтверждённым часовым таймаутом длительной транскрибации.

```bash
opencode mcp list
opencode debug skill
```

`mcp list` проверяет все настроенные серверы; ошибка постороннего MCP не
означает ошибку GigaAM. Для очень долгой записи, если клиент прерывает MCP-вызов,
предпочтите headless CLI, а не повторный запуск того же задания.

### Hermes

В `~/.hermes/config.yaml` объедините с существующими разделами:

```yaml
mcp_servers:
  gigaam-local:
    command: /Users/you/.local/bin/gigaam
    args: [mcp]
    env:
      ASR_BACKEND: mlx
    connect_timeout: 120
    timeout: 3600

skills:
  external_dirs:
    - /Users/you/.agents/skills
```

Оба таймаута — **секунды**. Не перезаписывайте другие `external_dirs`.

```bash
hermes mcp test gigaam-local
hermes skills list --source local
```

Ожидается подключение и обнаружение пяти MCP-инструментов.

## 5. Скиллы: инструкция не равна подключению

`gigaam` описывает headless CLI; `gigaam-mcp` — источники аудио, инструменты,
форматы, ограничения, таймауты и ошибки. Наличие этих файлов само по себе не
регистрирует сервер в MCP-клиенте.

```bash
mkdir -p "$HOME/.agents/skills"
~/.local/bin/gigaam --install-skill
```

Установщик копирует скиллы только в существующие каталоги `~/.claude/skills`,
`~/.codex/skills`, `~/.agents/skills`. OMP и Pi умеют читать общий
`~/.agents/skills`; Hermes подключает его через `skills.external_dirs`.

Обнаружение OpenCode зависит от версии и локальных настроек. Если
`opencode debug skill` не показывает оба скилла, используйте его собственный
каталог, не размножая независимые копии:

```bash
mkdir -p "$HOME/.config/opencode/skills"
ln -s "$HOME/.agents/skills/gigaam" "$HOME/.config/opencode/skills/gigaam"
ln -s "$HOME/.agents/skills/gigaam-mcp" "$HOME/.config/opencode/skills/gigaam-mcp"
```

Не используйте `ln -sf` поверх существующих пользовательских скиллов:
сначала проверьте имеющийся каталог/ссылку. При обновлении GigaAM повторите
`--install-skill`; символические ссылки продолжат читать обновлённые файлы.

Не устанавливайте новый harness только потому, что на диске сохранился его
старый каталог. Для Gemini CLI, Cursor и других клиентов сначала проверьте
само приложение и поддерживаемый им формат MCP.

## 6. Проверка протокола без обращения к LLM

Успешный `--help` доказывает только импорт зависимостей. У локального stdio
`server_status().asr.loader_loaded=false` и `error=null` — нормальное состояние:
это состояние постоянного загрузчика, а модели отдельных заданий им не учитываются.
Ход работы проверяйте по `busy.active` и progress; после задания `busy.active=0`.
`server_status`, `list_models` и `summarize` не загружают ASR-веса.
Режим `--http`, REST и веб-панель сохраняют постоянную серверную модель.

Проверка через Python MCP SDK из того же venv; первый запуск может скачивать
веса, последующие читают их с диска. Замените путь к WAV реальным локальным файлом:

```bash
PY="$HOME/.local/share/gigaam-tui/repo/.venv/bin/python"
"$PY" - /absolute/path/speech.wav <<'PY'
import asyncio
import os
import sys
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    audio = Path(sys.argv[1]).expanduser().resolve(strict=True)
    params = StdioServerParameters(
        command=str(Path.home() / '.local/bin/gigaam'),
        args=['mcp'],
        env={**os.environ, 'ASR_BACKEND': 'mlx'},
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(
            reader, writer, read_timeout_seconds=3600
        ) as session:
            await session.initialize()
            tools = await session.list_tools()
            print('Tools:', ', '.join(tool.name for tool in tools.tools))
            for name, arguments in (
                ('server_status', {}),
                ('transcribe', {'path': str(audio), 'format': 'verbose',
                                'audio_preprocessing': 'off'}),
            ):
                result = await session.call_tool(name, arguments)
                if result.is_error:
                    raise RuntimeError(str(result.content))
                print(name, result.structured_content or result.content)

asyncio.run(main())
PY
```

Ожидаются пять инструментов: `transcribe`, `summarize`, `list_models`,
`list_llm_providers`, `server_status`. Дополнительно сервер предоставляет
два ресурса (`gigaam://models`, `gigaam://status`) и два промпта
(`meeting_notes`, `subtitles_review`).

Проверьте не только статус, но и фактический текст/сегменты транскрибации.
Для негативной проверки отсутствующий `path` должен вернуть MCP tool error
с `[file_not_found]`, а не успешный пустой результат.

`summarize` — отдельная проверка: нужен настроенный LLM-провайдер. Она может
стоить денег и передавать текст внешней модели; успешная транскрибация не
доказывает работоспособность всех LLM-провайдеров и диаризации.

## 7. Диагностика и эксплуатация

| Симптом | Действие |
|---|---|
| `No module named 'yt_dlp'` | Установите `yt-dlp` в venv именно установленного launcher через `uv pip install --python ...`; проверьте `gigaam mcp --help` |
| MCP есть в конфиге, но нет в текущей сессии | Перезапустите harness / перезагрузите MCP, проверьте профиль и scope |
| `command not found` при запуске из приложения | Задайте абсолютный `command`, не полагайтесь на PATH shell |
| MLX недоступен | Проверьте Apple Silicon и зависимости `requirements-macos-mlx.txt`; не маскируйте ошибку случайной сменой backend |
| ONNX сообщает `encoder.onnx not found` | Проверьте настроенный путь и полный комплект ONNX-весов; успешный MLX не проверяет ONNX |
| Скиллы есть, инструментов нет | MCP-регистрация и discovery скиллов независимы; проверьте обе части |
| `paths_not_allowed` | Вы обратились к удалённому серверу; используйте `gigaam-local` для локального пути |
| Таймаут на большой записи | Дождитесь завершения, проверьте `busy`; не запускайте дубликат вслепую |
| `pyannote` требует HF token / лицензию | Настройте токен и лицензии моделей либо явно выберите поддерживаемый backend диаризации |
| `summarize` не работает | Проверьте `list_llm_providers`, LLM-настройки и авторизацию соответствующего провайдера |

Не публикуйте домашние конфиги harness, `.env`, API-ключи, пользовательские
транскрипты и backup-файлы. Для rollback удалите только регистрацию
`gigaam-local` либо восстановите её фрагмент из приватного backup; полный
rollback файла после дальнейших изменений может стереть новые настройки.

## 8. Источники форматов конфигурации

- [Claude Code MCP](https://code.claude.com/docs/en/mcp)
- [Codex MCP](https://developers.openai.com/codex/mcp/)
- [OMP MCP schema](https://github.com/can1357/oh-my-pi/blob/main/packages/coding-agent/src/config/mcp-schema.json)
- [OMP skills](https://github.com/can1357/oh-my-pi/blob/main/docs/skills.md)
- [Pi MCP adapter](https://github.com/nicobailon/pi-mcp-adapter)
- [OpenCode MCP](https://opencode.ai/docs/mcp-servers/)
- [OpenCode skills](https://opencode.ai/docs/skills/)
- [Hermes Agent](https://github.com/NousResearch/hermes-agent)
- [uv: Python environments](https://docs.astral.sh/uv/pip/environments/)

Поля и единицы таймаутов различаются между клиентами; переносить один JSON
во все harness без адаптации нельзя.

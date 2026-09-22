# Начните отсюда — GigaAM v3 Transcriber

Короткий маршрут для тех, кто разворачивает REST API на сервере или хочет
быстро прогнать файл через CLI. Полный обзор проекта — [README.md](../README.md).

## Запуск API на сервере

### Шаг 1: установка

```bash
cd /path/to/GigaAMv3
sudo ./deploy/install_api.sh
```

Скрипт настраивает systemd-сервис `gigaam-api`, nginx и сертификат Let's
Encrypt, запускает сервер и печатает API-ключ.

### Шаг 2: сохраните ключ

Ключ вида `gam_xxxxxxxx…` показывается один раз — в `.api_keys` хранится
только SHA-256 хэш, восстановить его из файла нельзя. Если пропустили:

```bash
journalctl -u gigaam-api | grep 'ПЕРВЫЙ API КЛЮЧ'
```

### Шаг 3: проверьте

```bash
# Без ключа
curl https://your-domain.com/health

# С ключом: список моделей
curl -H "Authorization: Bearer $GIGAAM_API_KEY" https://your-domain.com/v1/models
```

### Шаг 4: документация

Интерактивная OpenAPI-документация — `https://your-domain.com/docs`; здесь
можно загрузить файл прямо из браузера. Справочник по API — [API.md](API.md).

## Использование CLI (локально)

```bash
# Интерактивное меню
python cli.py

# Директория целиком
python cli.py -d /path/to/audio -o /path/to/output

# Конкретные файлы
python cli.py -f audio1.mp3 -f audio2.wav -o /output
```

Подробнее — [CLI_GUIDE.md](CLI_GUIDE.md).

## Как отправить файл в API

API совместим с OpenAI Audio API: ответ приходит синхронно, в одном запросе.

### curl

```bash
curl https://your-domain.com/v1/audio/transcriptions \
  -H "Authorization: Bearer $GIGAAM_API_KEY" \
  -F "file=@audio.mp3" -F "model=whisper-1"
```

Субтитры — `-F "response_format=srt"`; кто говорит —
`-F "response_format=diarized_json"`; длинные файлы за прокси —
`-F "stream=true"` (сервер шлёт прогресс, соединение не засыпает).

### Python (SDK OpenAI)

```python
from openai import OpenAI

client = OpenAI(base_url="https://your-domain.com/v1", api_key="gam_ваш_ключ")

with open("audio.mp3", "rb") as audio:
    result = client.audio.transcriptions.create(model="whisper-1", file=audio)
print(result.text)
```

Все параметры, форматы ответов, стрим и ошибки — в [API.md](API.md).
Готовая коллекция Postman — `postman/GigaAM_API.postman_collection.json`.

## Управление сервисом

```bash
systemctl status gigaam-api          # статус
journalctl -u gigaam-api -f          # логи в реальном времени
journalctl -u gigaam-api -n 50       # последние 50 строк
sudo systemctl restart gigaam-api    # перезапуск
sudo systemctl stop gigaam-api       # остановка
sudo systemctl start gigaam-api      # запуск
```

## Конфигурация

Все настройки — в `.env` в корне проекта (образец — `.env.example`):

```bash
# Сервер
API_HOST=127.0.0.1
API_PORT=8000

# Лимиты
MAX_FILE_SIZE=2147483648   # 2 ГБ
MAX_CONCURRENT_TASKS=3

# Диаризация pyannote
HF_TOKEN=hf_...
```

Полный список переменных API — раздел «Переменные окружения» в [API.md](API.md).
После изменения: `sudo systemctl restart gigaam-api`.

## Документация

- [API.md](API.md) — REST API: быстрый старт, параметры, форматы, стрим, ошибки
- [MCP.md](MCP.md) — MCP-сервер для ИИ-агентов: `gigaam mcp`, удалённый `/mcp`, инструменты, скиллы
- [CLI_GUIDE.md](CLI_GUIDE.md) — командная строка
- [QUICK_START.md](QUICK_START.md) — быстрый старт с приложением
- [INSTALL_LINUX.md](INSTALL_LINUX.md), [INSTALL_MACOS.md](INSTALL_MACOS.md), [INSTALL_WINDOWS.md](INSTALL_WINDOWS.md) — установка
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — решение проблем
- [LOGGING.md](LOGGING.md) — журналы

## Что дальше?

1. **Начинающим** — прогоните тестовый файл через `python cli.py`.
2. **Опытным** — поставьте API (`sudo ./deploy/install_api.sh`), откройте
   `/docs` и загрузите файл из браузера.
3. **Разработчикам** — изучите [API.md](API.md) и подключите GigaAM к своему
   коду через SDK OpenAI, поменяв `base_url` и ключ.
4. **Агентам** — `claude mcp add gigaam -- gigaam mcp` (или удалённый `/mcp`
   с тем же ключом, что у API): [MCP.md](MCP.md).

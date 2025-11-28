# GigaAM v3 Transcriber - REST API Руководство

Полное руководство по использованию REST API для транскрибации аудио и видео файлов с доступом из любой точки интернета.

## Содержание

- [Введение](#введение)
- [Установка и настройка](#установка-и-настройка)
- [Аутентификация](#аутентификация)
- [API Endpoints](#api-endpoints)
- [Примеры использования](#примеры-использования)
- [Безопасность](#безопасность)
- [Ограничения и лимиты](#ограничения-и-лимиты)
- [Обработка ошибок](#обработка-ошибок)
- [Интеграция](#интеграция)

## Введение

REST API GigaAM v3 Transcriber предоставляет программный доступ к сервису транскрибации:

- 🌍 **Доступ из любой точки** - работает через HTTPS из любого места
- 🔒 **Безопасность** - SSL шифрование + API ключи
- ⚡ **Асинхронная обработка** - загрузка файла и получение результата разделены
- 📊 **Отслеживание прогресса** - проверка статуса в реальном времени
- 🎯 **RESTful архитектура** - простота интеграции
- 📝 **Интерактивная документация** - Swagger UI встроен

## Установка и настройка

### Автоматическая установка

```bash
cd /mnt/storage10tb/syncthing/development/GigaAMv3
sudo ./deploy/install_api.sh
```

Скрипт автоматически:
1. Настроит systemd сервис
2. Настроит nginx reverse proxy
3. Получит SSL сертификат от Let's Encrypt
4. Запустит API сервер
5. Создаст первый API ключ

### Ручная установка

#### 1. Установка зависимостей

```bash
# Активируйте окружение
source /mnt/storage10tb/anaconda/bin/activate /mnt/storage10tb/anaconda/envs/gigaam

# Установите зависимости API
pip install fastapi uvicorn python-multipart aiofiles python-jose[cryptography] passlib[bcrypt] slowapi
```

#### 2. Настройка systemd

```bash
# Копируйте файл сервиса
sudo cp deploy/systemd/gigaam-api.service /etc/systemd/system/

# Перезагрузите systemd
sudo systemctl daemon-reload

# Запустите сервис
sudo systemctl enable gigaam-api
sudo systemctl start gigaam-api

# Проверьте статус
sudo systemctl status gigaam-api
```

#### 3. Настройка nginx

```bash
# Установите nginx и certbot
sudo apt install nginx certbot python3-certbot-nginx

# Копируйте конфигурацию
sudo cp deploy/nginx/gigaam-api.conf /etc/nginx/sites-available/gigaam-api.dubr1k.space

# Получите SSL сертификат
sudo certbot certonly --nginx -d gigaam-api.dubr1k.space

# Активируйте сайт
sudo ln -s /etc/nginx/sites-available/gigaam-api.dubr1k.space /etc/nginx/sites-enabled/

# Проверьте и перезагрузите nginx
sudo nginx -t
sudo systemctl reload nginx
```

#### 4. Получение API ключа

API ключ автоматически создается при первом запуске. Найдите его в логах:

```bash
journalctl -u gigaam-api | grep "ПЕРВЫЙ API КЛЮЧ"
```

Или посмотрите в файле:

```bash
cat /mnt/storage10tb/syncthing/development/GigaAMv3/.api_keys
```

## Аутентификация

Все запросы (кроме `/health`) требуют API ключ в заголовке:

```
X-API-Key: gam_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### Пример с curl:

```bash
curl -H "X-API-Key: your_api_key_here" https://gigaam-api.dubr1k.space/health
```

### Пример с Python:

```python
import requests

headers = {
    "X-API-Key": "your_api_key_here"
}

response = requests.get("https://gigaam-api.dubr1k.space/health", headers=headers)
```

## API Endpoints

### Base URL

```
https://gigaam-api.dubr1k.space
```

### Документация

Интерактивная документация Swagger UI:

```
https://gigaam-api.dubr1k.space/docs
```

### Список эндпоинтов

| Метод | Endpoint | Описание | Auth |
|-------|----------|----------|------|
| GET | `/` | Информация об API | ❌ |
| GET | `/health` | Проверка здоровья | ❌ |
| POST | `/api/v1/transcribe` | Загрузить файл | ✅ |
| GET | `/api/v1/tasks/{task_id}` | Статус задачи | ✅ |
| GET | `/api/v1/tasks/{task_id}/result` | Результат транскрибации | ✅ |
| GET | `/api/v1/tasks/{task_id}/download` | Скачать файл результата | ✅ |
| GET | `/api/v1/tasks` | Список задач | ✅ |
| DELETE | `/api/v1/tasks/{task_id}` | Удалить задачу | ✅ |

## Примеры использования

### 1. Загрузка файла на транскрибацию

#### curl:

```bash
curl -X POST "https://gigaam-api.dubr1k.space/api/v1/transcribe" \
  -H "X-API-Key: your_api_key_here" \
  -F "file=@/path/to/audio.mp3"
```

#### Python:

```python
import requests

url = "https://gigaam-api.dubr1k.space/api/v1/transcribe"
headers = {"X-API-Key": "your_api_key_here"}
files = {"file": open("audio.mp3", "rb")}

response = requests.post(url, headers=headers, files=files)
result = response.json()
task_id = result["task_id"]
print(f"Task ID: {task_id}")
```

#### JavaScript (Node.js):

```javascript
const FormData = require('form-data');
const fs = require('fs');
const axios = require('axios');

const form = new FormData();
form.append('file', fs.createReadStream('audio.mp3'));

const response = await axios.post(
  'https://gigaam-api.dubr1k.space/api/v1/transcribe',
  form,
  {
    headers: {
      ...form.getHeaders(),
      'X-API-Key': 'your_api_key_here'
    }
  }
);

console.log('Task ID:', response.data.task_id);
```

**Ответ:**

```json
{
  "task_id": "a1b2c3d4e5f6",
  "message": "Файл успешно загружен и отправлен на обработку",
  "filename": "audio.mp3",
  "file_size": 12345678,
  "estimated_time": "Оценка будет доступна после начала обработки"
}
```

### 2. Проверка статуса задачи

#### curl:

```bash
curl -H "X-API-Key: your_api_key_here" \
  "https://gigaam-api.dubr1k.space/api/v1/tasks/a1b2c3d4e5f6"
```

#### Python:

```python
import requests
import time

url = f"https://gigaam-api.dubr1k.space/api/v1/tasks/{task_id}"
headers = {"X-API-Key": "your_api_key_here"}

while True:
    response = requests.get(url, headers=headers)
    status = response.json()
    
    print(f"Status: {status['status']} - Progress: {status['progress']}%")
    
    if status['status'] in ['completed', 'failed']:
        break
    
    time.sleep(5)  # Проверяем каждые 5 секунд
```

**Ответ (обработка):**

```json
{
  "task_id": "a1b2c3d4e5f6",
  "status": "processing",
  "created_at": "2025-11-29T00:00:00",
  "started_at": "2025-11-29T00:00:05",
  "completed_at": null,
  "progress": 45,
  "filename": "audio.mp3",
  "file_size": 12345678,
  "message": "Транскрибация в процессе..."
}
```

**Ответ (завершено):**

```json
{
  "task_id": "a1b2c3d4e5f6",
  "status": "completed",
  "created_at": "2025-11-29T00:00:00",
  "started_at": "2025-11-29T00:00:05",
  "completed_at": "2025-11-29T00:02:30",
  "progress": 100,
  "filename": "audio.mp3",
  "file_size": 12345678,
  "message": "Транскрибация успешно завершена"
}
```

### 3. Получение результата

#### curl:

```bash
curl -H "X-API-Key: your_api_key_here" \
  "https://gigaam-api.dubr1k.space/api/v1/tasks/a1b2c3d4e5f6/result"
```

#### Python:

```python
url = f"https://gigaam-api.dubr1k.space/api/v1/tasks/{task_id}/result"
headers = {"X-API-Key": "your_api_key_here"}

response = requests.get(url, headers=headers)
result = response.json()

print("Транскрибация:")
print(result["transcription"])

print("\nС таймкодами:")
print(result["transcription_with_timecodes"])
```

**Ответ:**

```json
{
  "task_id": "a1b2c3d4e5f6",
  "status": "completed",
  "filename": "audio.mp3",
  "transcription": "Добрый день, это тестовая запись...",
  "transcription_with_timecodes": "[00:00:00 - 00:00:05] Добрый день, это тестовая запись...",
  "processing_time": 145.3,
  "media_duration": 600.0
}
```

### 4. Скачивание файла результата

#### curl:

```bash
# Скачать текст без таймкодов
curl -H "X-API-Key: your_api_key_here" \
  "https://gigaam-api.dubr1k.space/api/v1/tasks/a1b2c3d4e5f6/download?format=txt" \
  -o result.txt

# Скачать текст с таймкодами
curl -H "X-API-Key: your_api_key_here" \
  "https://gigaam-api.dubr1k.space/api/v1/tasks/a1b2c3d4e5f6/download?format=timecodes" \
  -o result_timecodes.txt
```

#### Python:

```python
url = f"https://gigaam-api.dubr1k.space/api/v1/tasks/{task_id}/download"
headers = {"X-API-Key": "your_api_key_here"}

# Скачать текст
response = requests.get(url, headers=headers, params={"format": "txt"})
with open("result.txt", "w", encoding="utf-8") as f:
    f.write(response.text)

# Скачать с таймкодами
response = requests.get(url, headers=headers, params={"format": "timecodes"})
with open("result_timecodes.txt", "w", encoding="utf-8") as f:
    f.write(response.text)
```

### 5. Полный пример: загрузка и ожидание результата

```python
import requests
import time
from pathlib import Path

class GigaAMClient:
    def __init__(self, api_key, base_url="https://gigaam-api.dubr1k.space"):
        self.api_key = api_key
        self.base_url = base_url
        self.headers = {"X-API-Key": api_key}
    
    def transcribe_file(self, file_path, poll_interval=5):
        """
        Загружает файл и ждет завершения транскрибации
        
        Args:
            file_path: путь к файлу
            poll_interval: интервал проверки статуса (секунды)
        
        Returns:
            dict: результат транскрибации
        """
        # 1. Загрузка файла
        print(f"Загрузка файла: {file_path}")
        with open(file_path, "rb") as f:
            files = {"file": f}
            response = requests.post(
                f"{self.base_url}/api/v1/transcribe",
                headers=self.headers,
                files=files
            )
        
        if response.status_code != 202:
            raise Exception(f"Ошибка загрузки: {response.text}")
        
        task_id = response.json()["task_id"]
        print(f"Task ID: {task_id}")
        
        # 2. Ожидание завершения
        while True:
            response = requests.get(
                f"{self.base_url}/api/v1/tasks/{task_id}",
                headers=self.headers
            )
            
            status = response.json()
            print(f"Статус: {status['status']} - {status['progress']}%")
            
            if status['status'] == 'completed':
                break
            elif status['status'] == 'failed':
                raise Exception(f"Ошибка обработки: {status.get('error')}")
            
            time.sleep(poll_interval)
        
        # 3. Получение результата
        response = requests.get(
            f"{self.base_url}/api/v1/tasks/{task_id}/result",
            headers=self.headers
        )
        
        result = response.json()
        print(f"✓ Готово! Время обработки: {result['processing_time']:.1f}с")
        
        return result

# Использование
client = GigaAMClient(api_key="your_api_key_here")
result = client.transcribe_file("audio.mp3")

print("\n=== РЕЗУЛЬТАТ ===")
print(result["transcription"])

# Сохранение в файл
with open("transcription.txt", "w", encoding="utf-8") as f:
    f.write(result["transcription"])
```

### 6. Список всех задач

#### curl:

```bash
# Все задачи
curl -H "X-API-Key: your_api_key_here" \
  "https://gigaam-api.dubr1k.space/api/v1/tasks"

# Только завершенные
curl -H "X-API-Key: your_api_key_here" \
  "https://gigaam-api.dubr1k.space/api/v1/tasks?status_filter=completed"

# Только обрабатываемые
curl -H "X-API-Key: your_api_key_here" \
  "https://gigaam-api.dubr1k.space/api/v1/tasks?status_filter=processing"
```

#### Python:

```python
url = "https://gigaam-api.dubr1k.space/api/v1/tasks"
headers = {"X-API-Key": "your_api_key_here"}

response = requests.get(url, headers=headers, params={"limit": 50})
tasks = response.json()

print(f"Всего задач: {tasks['total']}")
for task in tasks['tasks']:
    print(f"  {task['task_id']}: {task['status']} - {task['filename']}")
```

### 7. Удаление задачи

#### curl:

```bash
curl -X DELETE -H "X-API-Key: your_api_key_here" \
  "https://gigaam-api.dubr1k.space/api/v1/tasks/a1b2c3d4e5f6"
```

#### Python:

```python
url = f"https://gigaam-api.dubr1k.space/api/v1/tasks/{task_id}"
headers = {"X-API-Key": "your_api_key_here"}

response = requests.delete(url, headers=headers)
print(response.json()["message"])
```

## Безопасность

### 1. API ключи

- Храните API ключи в безопасности (переменные окружения, секретные хранилища)
- Никогда не коммитьте ключи в Git
- Регулярно обновляйте ключи

### 2. HTTPS

Все запросы должны идти через HTTPS:
- ✅ `https://gigaam-api.dubr1k.space`
- ❌ `http://gigaam-api.dubr1k.space`

### 3. Rate Limiting

API защищен от злоупотреблений:
- **Загрузка файлов**: 10 запросов/минуту
- **Другие эндпоинты**: 30 запросов/минуту
- При превышении лимита: HTTP 429

### 4. Защита данных

- Файлы хранятся 24 часа, затем автоматически удаляются
- Никогда не передавайте конфиденциальные данные без шифрования
- Используйте VPN для дополнительной безопасности

## Ограничения и лимиты

| Параметр | Значение |
|----------|----------|
| Максимальный размер файла | 2 GB |
| Максимальное количество одновременных задач | 3 |
| Время хранения результатов | 24 часа |
| Rate limit (загрузка) | 10/минуту |
| Rate limit (API) | 30/минуту |
| Поддерживаемые форматы | mp3, wav, m4a, mp4, avi, mov, mkv, webm, flac, ogg, wma |

## Обработка ошибок

### Коды ответов

| Код | Описание |
|-----|----------|
| 200 | Успешный запрос |
| 202 | Принято на обработку |
| 400 | Неверный запрос |
| 401 | Неверный API ключ |
| 404 | Задача не найдена |
| 413 | Файл слишком большой |
| 429 | Превышен лимит запросов |
| 500 | Внутренняя ошибка сервера |

### Примеры обработки ошибок

```python
import requests

try:
    response = requests.post(url, headers=headers, files=files)
    response.raise_for_status()  # Вызывает исключение для 4xx/5xx
    result = response.json()
    
except requests.exceptions.HTTPError as e:
    if e.response.status_code == 401:
        print("Неверный API ключ")
    elif e.response.status_code == 413:
        print("Файл слишком большой")
    elif e.response.status_code == 429:
        print("Превышен лимит запросов, подождите")
    else:
        print(f"HTTP ошибка: {e}")
        
except requests.exceptions.RequestException as e:
    print(f"Ошибка сети: {e}")
```

## Интеграция

### Telegram Bot

```python
from telegram import Update
from telegram.ext import Application, MessageHandler, filters

async def handle_audio(update: Update, context):
    """Обработка аудио файла в Telegram"""
    # Скачать файл
    file = await update.message.audio.get_file()
    await file.download_to_drive("audio.mp3")
    
    # Отправить на транскрибацию
    client = GigaAMClient(api_key="your_key")
    result = client.transcribe_file("audio.mp3")
    
    # Отправить результат
    await update.message.reply_text(result["transcription"])

app = Application.builder().token("telegram_token").build()
app.add_handler(MessageHandler(filters.AUDIO, handle_audio))
app.run_polling()
```

### Web приложение (Flask)

```python
from flask import Flask, request, jsonify, render_template
import requests

app = Flask(__name__)
API_KEY = "your_api_key_here"

@app.route('/transcribe', methods=['POST'])
def transcribe():
    file = request.files['file']
    
    # Отправляем на GigaAM API
    response = requests.post(
        "https://gigaam-api.dubr1k.space/api/v1/transcribe",
        headers={"X-API-Key": API_KEY},
        files={"file": file}
    )
    
    return jsonify(response.json())

@app.route('/status/<task_id>')
def status(task_id):
    response = requests.get(
        f"https://gigaam-api.dubr1k.space/api/v1/tasks/{task_id}",
        headers={"X-API-Key": API_KEY}
    )
    return jsonify(response.json())

if __name__ == '__main__':
    app.run()
```

### Bash скрипт

```bash
#!/bin/bash
# transcribe.sh - Простой скрипт для транскрибации

API_KEY="your_api_key_here"
BASE_URL="https://gigaam-api.dubr1k.space"
FILE="$1"

if [ -z "$FILE" ]; then
    echo "Использование: ./transcribe.sh <audio_file>"
    exit 1
fi

echo "Загрузка файла: $FILE"
RESPONSE=$(curl -s -X POST "$BASE_URL/api/v1/transcribe" \
    -H "X-API-Key: $API_KEY" \
    -F "file=@$FILE")

TASK_ID=$(echo $RESPONSE | jq -r '.task_id')
echo "Task ID: $TASK_ID"

echo "Ожидание обработки..."
while true; do
    STATUS=$(curl -s -H "X-API-Key: $API_KEY" \
        "$BASE_URL/api/v1/tasks/$TASK_ID" | jq -r '.status')
    
    PROGRESS=$(curl -s -H "X-API-Key: $API_KEY" \
        "$BASE_URL/api/v1/tasks/$TASK_ID" | jq -r '.progress')
    
    echo "Статус: $STATUS - $PROGRESS%"
    
    if [ "$STATUS" == "completed" ]; then
        break
    fi
    
    sleep 5
done

echo "Получение результата..."
curl -s -H "X-API-Key: $API_KEY" \
    "$BASE_URL/api/v1/tasks/$TASK_ID/result" | jq -r '.transcription' > result.txt

echo "✓ Готово! Результат сохранен в result.txt"
```

## Мониторинг и обслуживание

### Проверка здоровья

```bash
curl https://gigaam-api.dubr1k.space/health
```

### Логи

```bash
# Просмотр логов
journalctl -u gigaam-api -f

# Последние 100 строк
journalctl -u gigaam-api -n 100

# Логи за сегодня
journalctl -u gigaam-api --since today
```

### Управление сервисом

```bash
# Статус
systemctl status gigaam-api

# Перезапуск
sudo systemctl restart gigaam-api

# Остановка
sudo systemctl stop gigaam-api

# Запуск
sudo systemctl start gigaam-api

# Автозапуск
sudo systemctl enable gigaam-api
```

## Производительность

### Рекомендации

1. **Пакетная обработка**: отправляйте файлы параллельно (до 3 одновременно)
2. **Кэширование результатов**: сохраняйте результаты локально
3. **Компрессия**: сжимайте аудио перед отправкой (если качество позволяет)
4. **Оптимизация формата**: используйте MP3 или M4A вместо WAV

### Примерное время обработки

| Длительность аудио | CPU | GPU (CUDA) |
|--------------------|-----|------------|
| 5 минут | ~2-3 мин | ~1 мин |
| 30 минут | ~15-20 мин | ~5-7 мин |
| 1 час | ~30-40 мин | ~10-15 мин |

## Поддержка

При возникновении проблем:

1. Проверьте статус сервиса: `curl https://gigaam-api.dubr1k.space/health`
2. Изучите документацию: `https://gigaam-api.dubr1k.space/docs`
3. Проверьте логи: `journalctl -u gigaam-api`
4. Создайте issue на GitHub

---

**GigaAM v3 Transcriber API** - мощный инструмент для интеграции транскрибации в ваши приложения!


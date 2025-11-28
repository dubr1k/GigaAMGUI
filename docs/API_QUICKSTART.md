# GigaAM API - Быстрый старт

## 🚀 Установка за 5 минут

### 1. Автоматическая установка

```bash
cd /mnt/storage10tb/syncthing/development/GigaAMv3
sudo ./deploy/install_api.sh
```

✅ Готово! API доступен по адресу: `https://gigaam-api.dubr1k.space`

### 2. Получение API ключа

Ключ создается автоматически. Найдите его:

```bash
journalctl -u gigaam-api | grep "ПЕРВЫЙ API КЛЮЧ"
# или
cat /mnt/storage10tb/syncthing/development/GigaAMv3/.api_keys
```

### 3. Тестирование

```bash
# Проверка работы API
curl https://gigaam-api.dubr1k.space/health

# Проверка с API ключом
curl -H "X-API-Key: ваш_ключ_здесь" \
  https://gigaam-api.dubr1k.space/api/v1/tasks
```

## 📝 Первая транскрибация

### Вариант 1: curl

```bash
# 1. Загрузить файл
TASK_ID=$(curl -X POST "https://gigaam-api.dubr1k.space/api/v1/transcribe" \
  -H "X-API-Key: ваш_ключ" \
  -F "file=@audio.mp3" | jq -r '.task_id')

echo "Task ID: $TASK_ID"

# 2. Проверить статус
curl -H "X-API-Key: ваш_ключ" \
  "https://gigaam-api.dubr1k.space/api/v1/tasks/$TASK_ID"

# 3. Получить результат (когда статус = completed)
curl -H "X-API-Key: ваш_ключ" \
  "https://gigaam-api.dubr1k.space/api/v1/tasks/$TASK_ID/result" | jq -r '.transcription'
```

### Вариант 2: Python

```python
import requests
import time

API_KEY = "ваш_ключ_здесь"
BASE_URL = "https://gigaam-api.dubr1k.space"

# 1. Загрузить файл
with open("audio.mp3", "rb") as f:
    response = requests.post(
        f"{BASE_URL}/api/v1/transcribe",
        headers={"X-API-Key": API_KEY},
        files={"file": f}
    )
task_id = response.json()["task_id"]
print(f"Task ID: {task_id}")

# 2. Ждать завершения
while True:
    response = requests.get(
        f"{BASE_URL}/api/v1/tasks/{task_id}",
        headers={"X-API-Key": API_KEY}
    )
    status = response.json()
    print(f"{status['status']} - {status['progress']}%")
    
    if status['status'] == 'completed':
        break
    time.sleep(5)

# 3. Получить результат
response = requests.get(
    f"{BASE_URL}/api/v1/tasks/{task_id}/result",
    headers={"X-API-Key": API_KEY}
)
print("\n" + response.json()["transcription"])
```

## 📚 Документация

- **Полное руководство**: `docs/API_GUIDE.md`
- **Swagger UI**: `https://gigaam-api.dubr1k.space/docs`
- **Примеры кода**: `docs/API_GUIDE.md#примеры-использования`

## 🔧 Управление

```bash
# Статус сервиса
systemctl status gigaam-api

# Просмотр логов
journalctl -u gigaam-api -f

# Перезапуск
sudo systemctl restart gigaam-api
```

## 🌐 Интерактивная документация

Откройте в браузере:
```
https://gigaam-api.dubr1k.space/docs
```

Здесь вы можете:
- Просмотреть все эндпоинты
- Протестировать API прямо в браузере
- Посмотреть схемы запросов и ответов

## ⚡ Быстрый тест

Используйте готовый скрипт:

```bash
./examples/test_api.py audio.mp3
```

## 💡 Советы

1. **Сохраняйте API ключ**: положите его в переменную окружения
   ```bash
   export GIGAAM_API_KEY="ваш_ключ"
   ```

2. **Используйте правильный формат**: MP3, WAV, M4A работают лучше всего

3. **Проверяйте размер**: максимум 2GB на файл

4. **Мониторьте прогресс**: поле `progress` показывает 0-100%

5. **Не забывайте удалять**: старые задачи удаляются автоматически через 24ч

## 🆘 Проблемы?

1. API не отвечает:
   ```bash
   systemctl status gigaam-api
   journalctl -u gigaam-api -n 50
   ```

2. Ошибка 401 (Unauthorized):
   - Проверьте API ключ
   - Убедитесь что заголовок называется `X-API-Key`

3. Ошибка 413 (File too large):
   - Файл больше 2GB
   - Разбейте на части или сожмите

4. Долгая обработка:
   - Нормально для длинных записей
   - Проверяйте поле `progress`

## 🎯 Готово!

Теперь вы можете интегрировать транскрибацию в свои приложения!


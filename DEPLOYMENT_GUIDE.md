# GigaAM v3 Transcriber - Руководство по развертыванию REST API

## Краткое описание

Полностью настроенный REST API для транскрибации аудио и видео файлов с доступом из интернета через HTTPS.

## Что было создано

### 1. REST API Server (`api.py`)
- **FastAPI приложение** с асинхронной обработкой
- **Аутентификация** через API ключи
- **Rate limiting** (защита от злоупотреблений)
- **Автоматическая очистка** старых файлов (24ч)
- **Встроенная документация** Swagger UI
- **Поддержка больших файлов** до 2GB
- **Отслеживание прогресса** 0-100%

### 2. Конфигурация nginx (`deploy/nginx/gigaam-api.conf`)
- **Reverse proxy** на FastAPI
- **SSL/TLS** с Let's Encrypt
- **Безопасные заголовки** (HSTS, CSP)
- **Поддержка больших загрузок** 2GB
- **Оптимизированные таймауты**
- **Rate limiting** на уровне nginx

### 3. systemd Service (`deploy/systemd/gigaam-api.service`)
- **Автозапуск** при старте системы
- **Автоматический перезапуск** при сбоях
- **Логирование** в systemd journal
- **Изоляция** процесса

### 4. Скрипт установки (`deploy/install_api.sh`)
- **Автоматическая установка** всех компонентов
- **Настройка SSL** сертификатов
- **Создание API ключа**
- **Проверка всех зависимостей**

### 5. Документация
- `docs/API_GUIDE.md` - полное руководство (40+ страниц)
- `docs/API_QUICKSTART.md` - быстрый старт
- `examples/test_api.py` - тестовый скрипт

## Установка

### Вариант A: Автоматическая установка (рекомендуется)

```bash
cd /mnt/storage10tb/syncthing/development/GigaAMv3
sudo ./deploy/install_api.sh
```

Скрипт выполнит:
1. ✅ Установку systemd сервиса
2. ✅ Настройку nginx reverse proxy
3. ✅ Получение SSL сертификата (Let's Encrypt)
4. ✅ Запуск API сервера
5. ✅ Создание первого API ключа

### Вариант B: Ручная установка

#### Шаг 1: Установка зависимостей

```bash
source /mnt/storage10tb/anaconda/bin/activate /mnt/storage10tb/anaconda/envs/gigaam
pip install fastapi uvicorn python-multipart aiofiles python-jose[cryptography] passlib[bcrypt] slowapi
```

#### Шаг 2: Настройка systemd

```bash
sudo cp deploy/systemd/gigaam-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable gigaam-api
sudo systemctl start gigaam-api
```

#### Шаг 3: Настройка nginx

```bash
sudo apt install nginx certbot python3-certbot-nginx

# Получить SSL сертификат
sudo certbot certonly --nginx -d gigaam-api.dubr1k.space

# Установить конфигурацию
sudo cp deploy/nginx/gigaam-api.conf /etc/nginx/sites-available/gigaam-api.dubr1k.space
sudo ln -s /etc/nginx/sites-available/gigaam-api.dubr1k.space /etc/nginx/sites-enabled/

# Перезагрузить nginx
sudo nginx -t
sudo systemctl reload nginx
```

#### Шаг 4: Получение API ключа

```bash
journalctl -u gigaam-api | grep "ПЕРВЫЙ API КЛЮЧ"
# или
cat /mnt/storage10tb/syncthing/development/GigaAMv3/.api_keys
```

## Настройка DNS

Для работы API необходимо настроить DNS запись:

```
gigaam-api.dubr1k.space -> A -> IP_адрес_вашего_сервера
```

## Проверка работы

### 1. Проверка API

```bash
# Healthcheck (без авторизации)
curl https://gigaam-api.dubr1k.space/health

# С API ключом
curl -H "X-API-Key: ваш_ключ" https://gigaam-api.dubr1k.space/api/v1/tasks
```

### 2. Тест транскрибации

```bash
# Используйте готовый скрипт
cd /mnt/storage10tb/syncthing/development/GigaAMv3
./examples/test_api.py audio.mp3
```

### 3. Swagger UI

Откройте в браузере:
```
https://gigaam-api.dubr1k.space/docs
```

## Управление сервисом

```bash
# Статус
systemctl status gigaam-api

# Логи (в реальном времени)
journalctl -u gigaam-api -f

# Логи (последние 100 строк)
journalctl -u gigaam-api -n 100

# Перезапуск
sudo systemctl restart gigaam-api

# Остановка
sudo systemctl stop gigaam-api

# Запуск
sudo systemctl start gigaam-api
```

## Мониторинг

### Системные ресурсы

```bash
# CPU и память
ps aux | grep uvicorn

# Сетевые подключения
netstat -tulpn | grep 8000
```

### Nginx логи

```bash
# Access log
tail -f /var/log/nginx/gigaam-api_access.log

# Error log
tail -f /var/log/nginx/gigaam-api_error.log
```

### API метрики

```bash
# Healthcheck
curl https://gigaam-api.dubr1k.space/health | jq
```

Ответ покажет:
- Статус модели
- Количество активных задач
- Общее количество задач

## Обновление

### Обновление кода

```bash
cd /mnt/storage10tb/syncthing/development/GigaAMv3
git pull
sudo systemctl restart gigaam-api
```

### Обновление зависимостей

```bash
source /mnt/storage10tb/anaconda/bin/activate /mnt/storage10tb/anaconda/envs/gigaam
pip install --upgrade -r requirements.txt
sudo systemctl restart gigaam-api
```

### Обновление конфигурации

```bash
# Nginx
sudo cp deploy/nginx/gigaam-api.conf /etc/nginx/sites-available/gigaam-api.dubr1k.space
sudo nginx -t
sudo systemctl reload nginx

# systemd
sudo cp deploy/systemd/gigaam-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart gigaam-api
```

## Безопасность

### Firewall

```bash
# Разрешить только нужные порты
sudo ufw allow 22/tcp    # SSH
sudo ufw allow 80/tcp    # HTTP (для Let's Encrypt)
sudo ufw allow 443/tcp   # HTTPS
sudo ufw enable
```

### Обновление API ключей

```bash
# Добавить новый ключ
echo "gam_new_key_here" >> /mnt/storage10tb/syncthing/development/GigaAMv3/.api_keys

# Удалить старый ключ
nano /mnt/storage10tb/syncthing/development/GigaAMv3/.api_keys

# Перезапустить API
sudo systemctl restart gigaam-api
```

### SSL сертификаты

Let's Encrypt сертификаты обновляются автоматически через certbot timer.

Проверка:

```bash
sudo certbot renew --dry-run
```

## Производительность

### Текущая конфигурация

- **Workers**: 2 (uvicorn)
- **Одновременных задач**: 3 максимум
- **Максимальный размер файла**: 2GB
- **Rate limit**: 10 загрузок/минуту

### Оптимизация

Для увеличения производительности отредактируйте:

```bash
# systemd service
sudo nano /etc/systemd/system/gigaam-api.service

# Изменить количество workers:
ExecStart=/mnt/storage10tb/anaconda/envs/gigaam/bin/python -m uvicorn api:app --host 127.0.0.1 --port 8000 --workers 4

# Применить изменения
sudo systemctl daemon-reload
sudo systemctl restart gigaam-api
```

Или в коде `api.py` изменить:

```python
MAX_CONCURRENT_TASKS = 5  # Больше одновременных задач
```

## Резервное копирование

### Что нужно бэкапить:

1. **API ключи**: `/mnt/storage10tb/syncthing/development/GigaAMv3/.api_keys`
2. **Конфигурация**: `deploy/` директория
3. **SSL сертификаты**: `/etc/letsencrypt/`
4. **nginx конфиг**: `/etc/nginx/sites-available/gigaam-api.dubr1k.space`

```bash
#!/bin/bash
# backup.sh

BACKUP_DIR="/backup/gigaam-api/$(date +%Y-%m-%d)"
mkdir -p "$BACKUP_DIR"

# API ключи
cp /mnt/storage10tb/syncthing/development/GigaAMv3/.api_keys "$BACKUP_DIR/"

# Конфигурация
cp -r /mnt/storage10tb/syncthing/development/GigaAMv3/deploy "$BACKUP_DIR/"

# nginx
cp /etc/nginx/sites-available/gigaam-api.dubr1k.space "$BACKUP_DIR/"

# SSL (если нужно)
# sudo tar -czf "$BACKUP_DIR/letsencrypt.tar.gz" /etc/letsencrypt/
```

## Troubleshooting

### API не отвечает

```bash
# Проверить статус
systemctl status gigaam-api

# Посмотреть логи
journalctl -u gigaam-api -n 50

# Перезапустить
sudo systemctl restart gigaam-api
```

### Ошибка 502 Bad Gateway

```bash
# API сервер не запущен или не отвечает
systemctl start gigaam-api

# Проверить порт
netstat -tulpn | grep 8000

# Проверить nginx
sudo nginx -t
```

### Ошибка 413 (File too large)

Увеличьте лимит в nginx:

```bash
sudo nano /etc/nginx/sites-available/gigaam-api.dubr1k.space

# Изменить
client_max_body_size 5G;  # Было 2G

sudo systemctl reload nginx
```

### SSL сертификат истек

```bash
sudo certbot renew
sudo systemctl reload nginx
```

### Медленная обработка

1. Проверить нагрузку:
   ```bash
   htop
   nvidia-smi  # Для GPU
   ```

2. Увеличить количество workers (см. раздел Производительность)

3. Проверить место на диске:
   ```bash
   df -h
   ```

## Интеграция

### Примеры кода

См. `docs/API_GUIDE.md` для примеров интеграции с:
- Python
- curl/bash
- Node.js/JavaScript
- Telegram Bot
- Flask/Django

### Client библиотека

Создайте файл `gigaam_client.py`:

```python
import requests
import time

class GigaAMClient:
    def __init__(self, api_key, base_url="https://gigaam-api.dubr1k.space"):
        self.api_key = api_key
        self.base_url = base_url
        self.headers = {"X-API-Key": api_key}
    
    def transcribe(self, file_path, poll_interval=5):
        """Загружает файл и ждет результата"""
        # Загрузка
        with open(file_path, "rb") as f:
            response = requests.post(
                f"{self.base_url}/api/v1/transcribe",
                headers=self.headers,
                files={"file": f}
            )
        response.raise_for_status()
        task_id = response.json()["task_id"]
        
        # Ожидание
        while True:
            response = requests.get(
                f"{self.base_url}/api/v1/tasks/{task_id}",
                headers=self.headers
            )
            status = response.json()
            
            if status['status'] == 'completed':
                break
            elif status['status'] == 'failed':
                raise Exception(status.get('error'))
            
            time.sleep(poll_interval)
        
        # Результат
        response = requests.get(
            f"{self.base_url}/api/v1/tasks/{task_id}/result",
            headers=self.headers
        )
        return response.json()

# Использование
client = GigaAMClient("your_api_key")
result = client.transcribe("audio.mp3")
print(result["transcription"])
```

## Полезные ссылки

- **API документация**: https://gigaam-api.dubr1k.space/docs
- **Healthcheck**: https://gigaam-api.dubr1k.space/health
- **Полное руководство**: `docs/API_GUIDE.md`
- **Быстрый старт**: `docs/API_QUICKSTART.md`
- **Официальный GigaAM**: https://github.com/salute-developers/GigaAM

## Поддержка

При возникновении проблем:

1. Проверьте логи: `journalctl -u gigaam-api -n 100`
2. Проверьте healthcheck: `curl https://gigaam-api.dubr1k.space/health`
3. Проверьте документацию: `docs/API_GUIDE.md`
4. Создайте issue на GitHub

---

**GigaAM v3 Transcriber REST API** - профессиональное решение для транскрибации!


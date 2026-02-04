#!/bin/bash
# Скрипт для исправления конфигурации службы gigaam-api

echo "=========================================="
echo "Исправление конфигурации службы gigaam-api"
echo "=========================================="
echo ""

# Правильный путь к проекту
PROJECT_DIR="/mnt/storage10tb/docker-volumes/syncthing/data/Development/GigaAMv3"
SERVICE_FILE="/etc/systemd/system/gigaam-api.service"
TEMP_FILE="/tmp/gigaam-api.service"

# Проверка существования директории проекта
if [ ! -d "$PROJECT_DIR" ]; then
    echo "❌ Ошибка: Директория проекта не найдена: $PROJECT_DIR"
    exit 1
fi

# Проверка существования api.py
if [ ! -f "$PROJECT_DIR/api.py" ]; then
    echo "❌ Ошибка: Файл api.py не найден в $PROJECT_DIR"
    exit 1
fi

# Проверка существования .env
if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "⚠️  Предупреждение: Файл .env не найден в $PROJECT_DIR"
    echo "   Создайте его на основе .env.example"
fi

# Создание исправленного файла службы
cat > "$TEMP_FILE" << 'EOF'
[Unit]
Description=GigaAM v3 Transcriber API
After=network.target
Documentation=https://github.com/salute-developers/GigaAM

[Service]
Type=simple
User=dubr1k
Group=dubr1k
WorkingDirectory=/mnt/storage10tb/docker-volumes/syncthing/data/Development/GigaAMv3

# Переменные окружения
Environment="PATH=/mnt/storage10tb/anaconda/envs/gigaam/bin:/usr/local/bin:/usr/bin:/bin"
EnvironmentFile=/mnt/storage10tb/docker-volumes/syncthing/data/Development/GigaAMv3/.env

# Запуск с параметрами из .env
ExecStart=/mnt/storage10tb/anaconda/envs/gigaam/bin/python api.py

# Автоматический перезапуск при сбое
Restart=always
RestartSec=10

# Ограничения ресурсов (опционально)
# LimitNOFILE=65535
# MemoryLimit=8G
# CPUQuota=400%

# Логирование
StandardOutput=journal
StandardError=journal
SyslogIdentifier=gigaam-api

# Безопасность
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

echo "✓ Исправленный файл службы создан: $TEMP_FILE"
echo ""

# Копирование файла службы (требует sudo)
echo "Копирование файла службы в /etc/systemd/system/..."
sudo cp "$TEMP_FILE" "$SERVICE_FILE"
if [ $? -eq 0 ]; then
    echo "✓ Файл службы обновлен"
else
    echo "❌ Ошибка при копировании файла службы"
    exit 1
fi

# Перезагрузка конфигурации systemd
echo ""
echo "Перезагрузка конфигурации systemd..."
sudo systemctl daemon-reload
if [ $? -eq 0 ]; then
    echo "✓ Конфигурация systemd перезагружена"
else
    echo "❌ Ошибка при перезагрузке конфигурации systemd"
    exit 1
fi

echo ""
echo "=========================================="
echo "✅ Конфигурация службы исправлена!"
echo "=========================================="
echo ""
echo "Теперь можно запустить службу:"
echo "  sudo systemctl start gigaam-api"
echo "  sudo systemctl status gigaam-api"
echo ""
echo "Или использовать алиасы:"
echo "  gigaam-start"
echo "  gigaam-status"
echo ""


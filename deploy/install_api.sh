#!/bin/bash
# Скрипт установки и настройки GigaAM v3 Transcriber API
set -e

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=================================${NC}"
echo -e "${GREEN}GigaAM v3 Transcriber API${NC}"
echo -e "${GREEN}Скрипт установки${NC}"
echo -e "${GREEN}=================================${NC}\n"

# Проверка прав root
if [ "$EUID" -ne 0 ]; then 
   echo -e "${RED}Этот скрипт должен быть запущен с правами root${NC}"
   echo "Используйте: sudo ./install_api.sh"
   exit 1
fi

# Переменные
PROJECT_DIR="/mnt/storage10tb/syncthing/development/GigaAMv3"
CONDA_ENV="/mnt/storage10tb/anaconda/envs/gigaam"
DOMAIN="gigaam-api.dubr1k.space"
USER="dubr1k"

echo -e "${YELLOW}1. Проверка зависимостей...${NC}"

# Проверка nginx
if ! command -v nginx &> /dev/null; then
    echo -e "${RED}nginx не установлен!${NC}"
    echo "Установите nginx: sudo apt install nginx"
    exit 1
fi
echo -e "${GREEN}✓ nginx установлен${NC}"

# Проверка certbot
if ! command -v certbot &> /dev/null; then
    echo -e "${YELLOW}certbot не установлен. Устанавливаю...${NC}"
    apt update
    apt install -y certbot python3-certbot-nginx
fi
echo -e "${GREEN}✓ certbot установлен${NC}"

echo -e "\n${YELLOW}2. Настройка systemd сервиса...${NC}"

# Копируем файл сервиса
cp "$PROJECT_DIR/deploy/systemd/gigaam-api.service" /etc/systemd/system/
chmod 644 /etc/systemd/system/gigaam-api.service

# Перезагружаем systemd
systemctl daemon-reload

echo -e "${GREEN}✓ systemd сервис установлен${NC}"

echo -e "\n${YELLOW}3. Настройка nginx...${NC}"

# Копируем конфигурацию nginx
cp "$PROJECT_DIR/deploy/nginx/gigaam-api.conf" "/etc/nginx/sites-available/$DOMAIN"

# Проверяем, нужно ли получить SSL сертификат
if [ ! -d "/etc/letsencrypt/live/$DOMAIN" ]; then
    echo -e "${YELLOW}SSL сертификат не найден. Получаю Let's Encrypt сертификат...${NC}"
    echo -e "${YELLOW}Убедитесь, что домен $DOMAIN указывает на этот сервер!${NC}"
    echo -e "${YELLOW}Нажмите Enter для продолжения или Ctrl+C для отмены${NC}"
    read

    # Временно создаем простую конфигурацию для получения сертификата
    cat > "/etc/nginx/sites-available/$DOMAIN" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location / {
        return 200 'OK';
        add_header Content-Type text/plain;
    }
}
EOF

    # Активируем конфигурацию
    ln -sf "/etc/nginx/sites-available/$DOMAIN" /etc/nginx/sites-enabled/
    
    # Проверяем и перезагружаем nginx
    nginx -t
    systemctl reload nginx

    # Получаем сертификат
    certbot certonly --nginx -d "$DOMAIN" --non-interactive --agree-tos --email admin@dubr1k.space

    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ SSL сертификат получен${NC}"
        
        # Теперь копируем полную конфигурацию
        cp "$PROJECT_DIR/deploy/nginx/gigaam-api.conf" "/etc/nginx/sites-available/$DOMAIN"
    else
        echo -e "${RED}Не удалось получить SSL сертификат${NC}"
        echo "Продолжаю без SSL..."
        
        # Создаем конфигурацию без SSL
        cat > "/etc/nginx/sites-available/$DOMAIN" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    client_max_body_size 2G;
    
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        proxy_buffering off;
        proxy_request_buffering off;
    }

    location /health {
        proxy_pass http://127.0.0.1:8000/health;
        access_log off;
    }
}
EOF
    fi
else
    echo -e "${GREEN}✓ SSL сертификат уже существует${NC}"
fi

# Активируем сайт
ln -sf "/etc/nginx/sites-available/$DOMAIN" /etc/nginx/sites-enabled/

# Проверяем конфигурацию nginx
echo -e "${YELLOW}Проверка конфигурации nginx...${NC}"
if nginx -t; then
    echo -e "${GREEN}✓ Конфигурация nginx корректна${NC}"
    systemctl reload nginx
    echo -e "${GREEN}✓ nginx перезагружен${NC}"
else
    echo -e "${RED}Ошибка в конфигурации nginx!${NC}"
    exit 1
fi

echo -e "\n${YELLOW}4. Запуск API сервиса...${NC}"

# Запускаем сервис
systemctl enable gigaam-api.service
systemctl start gigaam-api.service

# Проверяем статус
sleep 3
if systemctl is-active --quiet gigaam-api.service; then
    echo -e "${GREEN}✓ API сервис успешно запущен${NC}"
else
    echo -e "${RED}Ошибка запуска API сервиса!${NC}"
    echo "Проверьте логи: journalctl -u gigaam-api.service -n 50"
    exit 1
fi

echo -e "\n${YELLOW}5. Получение API ключа...${NC}"

# Ждем инициализации API
sleep 5

# API ключ создается автоматически при первом запуске
API_KEY_FILE="$PROJECT_DIR/.api_keys"
if [ -f "$API_KEY_FILE" ]; then
    API_KEY=$(head -n 1 "$API_KEY_FILE")
    echo -e "${GREEN}✓ API ключ найден${NC}"
else
    echo -e "${YELLOW}API ключ будет создан при первом запуске${NC}"
    echo "Проверьте логи: journalctl -u gigaam-api.service | grep 'ПЕРВЫЙ API КЛЮЧ'"
fi

echo -e "\n${GREEN}=================================${NC}"
echo -e "${GREEN}✅ Установка завершена!${NC}"
echo -e "${GREEN}=================================${NC}\n"

echo -e "${YELLOW}Информация:${NC}"
echo -e "  Домен: https://$DOMAIN"
echo -e "  API документация: https://$DOMAIN/docs"
echo -e "  Проверка здоровья: https://$DOMAIN/health"
echo ""
echo -e "${YELLOW}API ключ:${NC}"
if [ -f "$API_KEY_FILE" ]; then
    echo -e "  ${GREEN}$API_KEY${NC}"
    echo -e "  ${YELLOW}Сохраните его в безопасном месте!${NC}"
else
    echo -e "  ${YELLOW}Получите из логов: journalctl -u gigaam-api.service | grep 'ПЕРВЫЙ API КЛЮЧ'${NC}"
fi
echo ""
echo -e "${YELLOW}Управление сервисом:${NC}"
echo -e "  Статус:  systemctl status gigaam-api"
echo -e "  Логи:    journalctl -u gigaam-api -f"
echo -e "  Стоп:    systemctl stop gigaam-api"
echo -e "  Старт:   systemctl start gigaam-api"
echo -e "  Рестарт: systemctl restart gigaam-api"
echo ""
echo -e "${YELLOW}Тестирование:${NC}"
echo -e "  curl https://$DOMAIN/health"
echo ""
echo -e "${YELLOW}Полная документация:${NC}"
echo -e "  $PROJECT_DIR/docs/API_GUIDE.md"
echo ""
echo -e "${GREEN}Готово! 🎉${NC}\n"


# Исправление службы gigaam-api

## Проблема
Служба не запускается из-за неправильного пути в конфигурации.

## Решение

Запустите скрипт исправления:

```bash
cd /mnt/storage10tb/docker-volumes/syncthing/data/Development/GigaAMv3
sudo ./fix_service.sh
```

Скрипт автоматически:
1. Проверит существование директории проекта
2. Обновит конфигурацию службы с правильными путями
3. Перезагрузит конфигурацию systemd

## После исправления

Запустите службу:
```bash
sudo systemctl start gigaam-api
```

Или используйте алиасы:
```bash
gigaam-start
gigaam-status
```

## Проверка

Проверьте статус службы:
```bash
sudo systemctl status gigaam-api
```

Просмотрите логи при необходимости:
```bash
sudo journalctl -u gigaam-api -n 50
```

## Что было исправлено

**Старый путь (неправильный):**
- `/mnt/storage10tb/syncthing/development/GigaAMv3`

**Новый путь (правильный):**
- `/mnt/storage10tb/docker-volumes/syncthing/data/Development/GigaAMv3`


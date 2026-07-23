#!/bin/sh
set -eu

# Docker создаёт новый bind mount как root:root. Перед запуском приложения
# отдаём только корень data volume непривилегированному пользователю; все
# дочерние каталоги затем создаёт сам GigaAM.
if [ "$(id -u)" = "0" ]; then
    mkdir -p /data
    chown gigaam:gigaam /data
    exec gosu gigaam "$@"
fi

exec "$@"
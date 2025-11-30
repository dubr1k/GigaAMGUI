#!/bin/bash
# Быстрый запуск CLI интерфейса

source /mnt/storage10tb/anaconda/bin/activate /mnt/storage10tb/anaconda/envs/gigaam
cd /mnt/storage10tb/syncthing/development/GigaAMv3
python cli.py "$@"


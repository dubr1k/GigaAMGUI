# Установка GigaAM v3 Transcriber на Linux

Полное руководство по установке приложения на Linux-дистрибутивах.

## Системные требования

### Минимальные
- Ubuntu 20.04+ / Debian 11+ / Fedora 35+ / другой современный дистрибутив
- Python 3.10 или выше
- 8 GB RAM
- 10 GB свободного места на диске
- Процессор: Intel Core i5 или аналогичный AMD

### Рекомендуемые
- Ubuntu 22.04+ / Debian 12+
- Python 3.11
- 16 GB RAM
- 20 GB свободного места на диске
- Процессор: Intel Core i7 или аналогичный AMD
- NVIDIA GPU с 6+ GB VRAM и CUDA 12.x

## Установка для Ubuntu/Debian

### Шаг 1. Обновление системы

```bash
sudo apt update
sudo apt upgrade -y
```

### Шаг 2. Установка Python 3.10+

#### Ubuntu 22.04+ (Python 3.10 уже установлен)
```bash
python3 --version
```

#### Ubuntu 20.04 (требуется добавить PPA)
```bash
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update
sudo apt install python3.10 python3.10-venv python3.10-dev
```

### Шаг 3. Установка системных зависимостей

```bash
sudo apt install -y \
    python3-pip \
    python3-venv \
    ffmpeg \
    git \
    build-essential \
    libsndfile1 \
    portaudio19-dev \
    tk-dev \
    python3-tk
```

### Шаг 4. Клонирование репозитория

```bash
cd ~
git clone https://github.com/ваш_username/GigaAMv3.git
cd GigaAMv3
```

### Шаг 5. Создание виртуального окружения

```bash
python3.10 -m venv venv
source venv/bin/activate
```

После активации в начале строки появится `(venv)`.

### Шаг 6. Обновление pip

```bash
pip install --upgrade pip setuptools wheel
```

### Шаг 7. Установка PyTorch

#### С NVIDIA GPU и CUDA

Проверьте версию CUDA:
```bash
nvcc --version
# или
nvidia-smi
```

Для CUDA 12.x:
```bash
pip install torch==2.6.0 torchvision==0.21.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124
```

Для CUDA 11.8:
```bash
pip install torch==2.6.0 torchvision==0.21.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu118
```

#### Без GPU (только CPU)

```bash
pip install torch==2.6.0 torchvision==0.21.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cpu
```

### Шаг 8. Установка зависимостей проекта

```bash
pip install -r requirements.txt
```

### Шаг 9. Установка GigaAM

```bash
cd GigaAM
pip install -e .
cd ..
```

### Шаг 10. Настройка HuggingFace токена

1. Получите токен на [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
2. Примите условия на [huggingface.co/pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0)
3. Скопируйте пример конфигурации:
```bash
cp .env.example .env
```
4. Отредактируйте `.env`:
```bash
nano .env
```
5. Замените `your_huggingface_token_here` на свой токен
6. Сохраните (Ctrl+O, Enter, Ctrl+X)

Примечание: Файл `.env` содержит конфиденциальные данные и не должен публиковаться в Git.

### Шаг 11. Запуск

```bash
python app.py
```

## Установка для Fedora/RHEL/CentOS

### Шаг 1. Обновление системы

```bash
sudo dnf update -y
```

### Шаг 2. Установка Python 3.10+

```bash
sudo dnf install python3.10 python3.10-devel
```

### Шаг 3. Установка системных зависимостей

```bash
sudo dnf install -y \
    python3-pip \
    ffmpeg \
    git \
    gcc \
    gcc-c++ \
    make \
    libsndfile-devel \
    portaudio-devel \
    tk-devel
```

Примечание: FFmpeg может потребовать подключения RPM Fusion:
```bash
sudo dnf install -y \
    https://download1.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm \
    https://download1.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm
sudo dnf install ffmpeg
```

### Шаг 4-11. Продолжение установки

Выполните шаги 4-11 из раздела Ubuntu/Debian выше.

## Установка для Arch Linux

### Шаг 1. Обновление системы

```bash
sudo pacman -Syu
```

### Шаг 2. Установка зависимостей

```bash
sudo pacman -S python python-pip ffmpeg git base-devel libsndfile portaudio tk
```

### Шаг 3-11. Продолжение установки

Выполните шаги 4-11 из раздела Ubuntu/Debian выше (начиная с клонирования).

## Настройка NVIDIA GPU (опционально)

### Проверка наличия GPU

```bash
lspci | grep -i nvidia
```

### Установка драйверов NVIDIA

#### Ubuntu
```bash
sudo ubuntu-drivers devices
sudo ubuntu-drivers autoinstall
# или конкретную версию
sudo apt install nvidia-driver-535
```

#### Fedora
```bash
sudo dnf install akmod-nvidia
sudo dnf install xorg-x11-drv-nvidia-cuda
```

### Установка CUDA Toolkit

#### Ubuntu 22.04
```bash
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt update
sudo apt install cuda-toolkit-12-4
```

#### Fedora
```bash
sudo dnf config-manager --add-repo https://developer.download.nvidia.com/compute/cuda/repos/fedora37/x86_64/cuda-fedora37.repo
sudo dnf install cuda-toolkit-12-4
```

### Настройка переменных окружения

Добавьте в `~/.bashrc` или `~/.zshrc`:
```bash
export PATH=/usr/local/cuda/bin:$PATH
export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH
```

Перезагрузите терминал или выполните:
```bash
source ~/.bashrc
```

### Проверка CUDA

```bash
nvcc --version
nvidia-smi
```

## Создание desktop-файла (опционально)

Для удобного запуска из меню приложений создайте файл `~/.local/share/applications/gigaam.desktop`:

```ini
[Desktop Entry]
Version=1.0
Type=Application
Name=GigaAM Transcriber
Comment=Транскрибация аудио и видео
Exec=/home/ваш_username/GigaAMv3/venv/bin/python /home/ваш_username/GigaAMv3/app.py
Icon=/home/ваш_username/GigaAMv3/icon.png
Terminal=false
Categories=AudioVideo;Audio;
```

Замените `ваш_username` на ваше имя пользователя.

## Создание скрипта запуска

Создайте файл `start.sh` в папке проекта:

```bash
#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
python app.py
```

Сделайте его исполняемым:
```bash
chmod +x start.sh
```

Теперь можно запускать:
```bash
./start.sh
```

## Автозапуск при входе в систему (опционально)

### GNOME
1. Откройте "Настройки" > "Приложения" > "Автозапуск"
2. Добавьте новое приложение
3. Команда: `/home/ваш_username/GigaAMv3/start.sh`

### KDE
1. Откройте "Параметры системы" > "Запуск и завершение" > "Автозапуск"
2. Добавьте скрипт

### Универсальный способ
Создайте файл `~/.config/autostart/gigaam.desktop` с содержимым из раздела "Создание desktop-файла" выше.

## Использование через SSH

Если запускаете на удаленном сервере без GUI:

### Установка X11 forwarding

На сервере убедитесь, что установлен X11:
```bash
sudo apt install xauth
```

В `/etc/ssh/sshd_config` должно быть:
```
X11Forwarding yes
```

Подключайтесь с X11 forwarding:
```bash
ssh -X user@server
```

### Альтернатива: VNC

Установите VNC сервер:
```bash
sudo apt install tightvncserver
vncserver :1
```

Подключитесь VNC клиентом к `server:5901`.

## Обновление приложения

```bash
cd ~/GigaAMv3
source venv/bin/activate
git pull
pip install -r requirements.txt --upgrade
cd GigaAM
git pull
pip install -e . --upgrade
cd ..
```

## Решение проблем

### Ошибка "tkinter не найден"

```bash
# Ubuntu/Debian
sudo apt install python3-tk

# Fedora
sudo dnf install python3-tkinter

# Arch
sudo pacman -S tk
```

### Ошибка "portaudio не найден"

```bash
# Ubuntu/Debian
sudo apt install portaudio19-dev

# Fedora
sudo dnf install portaudio-devel

# Arch
sudo pacman -S portaudio
```

### Ошибка "sndfile не найден"

```bash
# Ubuntu/Debian
sudo apt install libsndfile1-dev

# Fedora
sudo dnf install libsndfile-devel

# Arch
sudo pacman -S libsndfile
```

### Ошибка "CUDA не найдена" при наличии NVIDIA GPU

Проверьте установку драйверов и CUDA:
```bash
nvidia-smi
nvcc --version
```

Если драйверы не установлены, см. раздел "Настройка NVIDIA GPU".

### PyTorch не видит GPU

Проверьте:
```bash
python -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.device_count())"
```

Если `False`, переустановите PyTorch с правильной версией CUDA:
```bash
pip uninstall torch torchvision torchaudio
pip install torch==2.6.0 torchvision==0.21.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124
```

### Ошибка "Permission denied" для FFmpeg

```bash
which ffmpeg
ls -l $(which ffmpeg)
```

Если нет прав:
```bash
sudo chmod +x /usr/bin/ffmpeg
```

### Приложение не запускается в GUI

Проверьте DISPLAY:
```bash
echo $DISPLAY
```

Если пусто:
```bash
export DISPLAY=:0
```

### Медленная работа на CPU

Установите оптимизированную версию NumPy и SciPy:
```bash
pip uninstall numpy scipy
pip install numpy scipy --no-binary :all:
```

Или используйте conda для оптимизированных библиотек:
```bash
conda install numpy scipy
```

### Out of memory

Увеличьте swap:
```bash
sudo fallocate -l 8G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

Для постоянного использования добавьте в `/etc/fstab`:
```
/swapfile none swap sw 0 0
```

## Производительность

### Примерное время обработки

#### CPU (AMD Ryzen 7 5800X, 32GB RAM)
- 1 минута аудио: 40-50 секунд
- 5 минут аудио: 3-4 минуты
- 30 минут аудио: 18-25 минут

#### GPU (NVIDIA RTX 3080, 10GB VRAM)
- 1 минута аудио: 12-18 секунд
- 5 минут аудио: 1-1.5 минуты
- 30 минут аудио: 4-7 минут

### Оптимизация производительности

1. Используйте SSD для временных файлов
2. Закройте ненужные приложения
3. Установите `htop` для мониторинга:
```bash
sudo apt install htop
htop
```

4. Для GPU следите за использованием:
```bash
watch -n 1 nvidia-smi
```

## Использование Docker (альтернативный способ)

Создайте `Dockerfile`:

```dockerfile
FROM python:3.10-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    build-essential \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN cd GigaAM && pip install -e .

CMD ["python", "app.py"]
```

Сборка и запуск:
```bash
docker build -t gigaam-transcriber .
docker run -it --rm \
    -v /tmp/.X11-unix:/tmp/.X11-unix \
    -e DISPLAY=$DISPLAY \
    gigaam-transcriber
```

Для GPU добавьте флаги:
```bash
docker run -it --rm \
    --gpus all \
    -v /tmp/.X11-unix:/tmp/.X11-unix \
    -e DISPLAY=$DISPLAY \
    gigaam-transcriber
```

## Удаление

```bash
cd ~/GigaAMv3
deactivate  # если окружение активно
cd ..
rm -rf GigaAMv3
rm -rf ~/.cache/huggingface
rm -rf ~/.cache/torch
rm ~/.local/share/applications/gigaam.desktop  # если создавали
```

## Поддержка

При возникновении проблем:
1. Проверьте раздел "Решение проблем" выше
2. Посмотрите [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
3. Создайте issue на GitHub с описанием проблемы и логами

---

Установка завершена. Приятного использования GigaAM v3 Transcriber.


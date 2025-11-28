# Быстрая настройка GigaAM v3 Transcriber

## Шаг 1. Клонирование репозитория

```bash
git clone https://github.com/your-username/GigaAMv3.git
cd GigaAMv3
git submodule update --init --recursive
```

## Шаг 2. Создание виртуального окружения

### Windows
```bash
python -m venv venv
venv\Scripts\activate
```

### macOS/Linux
```bash
python3 -m venv venv
source venv/bin/activate
```

## Шаг 3. Установка зависимостей

```bash
pip install --upgrade pip
pip install -r requirements.txt
cd GigaAM
pip install -e .
cd ..
```

## Шаг 4. Установка FFmpeg

### Windows
Скачайте с [ffmpeg.org](https://ffmpeg.org/download.html) и добавьте в PATH

### macOS
```bash
brew install ffmpeg
```

### Linux (Ubuntu/Debian)
```bash
sudo apt install ffmpeg
```

## Шаг 5. Настройка HuggingFace токена

1. Получите токен на [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
2. Примите условия на [huggingface.co/pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0)
3. Скопируйте конфигурацию:

```bash
cp .env.example .env
```

4. Отредактируйте `.env` и замените:
```
HF_TOKEN=your_huggingface_token_here
```
на ваш токен.

## Шаг 6. Запуск

```bash
python app.py
```

## Детальные инструкции

Для подробных инструкций по установке смотрите:
- [Установка на Windows](docs/INSTALL_WINDOWS.md)
- [Установка на macOS](docs/INSTALL_MACOS.md)
- [Установка на Linux](docs/INSTALL_LINUX.md)

## Решение проблем

См. [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)


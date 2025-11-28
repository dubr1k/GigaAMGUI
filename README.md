# GigaAM v3 Transcriber

Приложение для транскрибации аудио и видео файлов с использованием модели **GigaAM-v3** от **Sber**.

> **Важно:** Данная реализация работает благодаря компании **Sber (ПАО «Сбербанк»)** и их команде разработки модели **GigaAM-v3** — современной модели распознавания русской речи. Модель доступна на [HuggingFace](https://huggingface.co/ai-sage/GigaAM-v3) и в [официальном репозитории](https://github.com/salute-developers/GigaAM).

## Возможности

- Транскрибация аудио и видео файлов на русском языке
- Поддержка множества форматов: mp3, wav, m4a, mp4, avi, mov, mkv, webm, flac, ogg, wma
- Автоматическая сегментация длинных записей
- Графический интерфейс на CustomTkinter
- Прогресс-бар с прогнозом времени завершения
- Статистика обработки и самообучающийся прогноз на основе длительности файлов
- Вывод в двух форматах: чистый текст и текст с таймкодами
- Поддержка GPU ускорения (CUDA на Windows/Linux, MPS на macOS)
- Автоматическое логирование всех операций с организацией по датам и времени
- Автоматическая очистка старых логов (старше 30 дней)

## Системные требования

### Windows
- Windows 10/11
- Python 3.10 или выше
- 8 GB RAM (рекомендуется 16 GB)
- 10 GB свободного места на диске
- NVIDIA GPU с CUDA (опционально, для ускорения)

### macOS
- macOS 11.0 или выше (Big Sur+)
- Python 3.10 или выше
- 8 GB RAM (рекомендуется 16 GB)
- 10 GB свободного места на диске
- Apple Silicon (M1/M2/M3) для MPS ускорения (опционально)

### Linux
- Ubuntu 20.04+ или другой современный дистрибутив
- Python 3.10 или выше
- 8 GB RAM (рекомендуется 16 GB)
- 10 GB свободного места на диске
- NVIDIA GPU с CUDA (опционально, для ускорения)

## Быстрая установка

### Windows

1. Установите Python 3.10 или выше с [python.org](https://www.python.org/downloads/)
2. Установите FFmpeg:
   - Скачайте с [ffmpeg.org](https://ffmpeg.org/download.html)
   - Добавьте в PATH
3. Установите зависимости:
```bash
pip install -r requirements.txt
cd GigaAM
pip install -e .
```
4. Настройте HuggingFace токен в `src/config.py`

Подробная инструкция: [docs/INSTALL_WINDOWS.md](docs/INSTALL_WINDOWS.md)

### macOS

1. Установите Homebrew (если еще не установлен):
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

2. Установите зависимости:
```bash
brew install python@3.10 ffmpeg
```

3. Создайте окружение и установите пакеты:
```bash
conda create -n gigaam python=3.10 -y
conda activate gigaam
pip install torch==2.6.0 torchvision==0.21.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements_macos.txt
cd GigaAM
pip install -e .
```

4. Настройте HuggingFace токен в `src/config.py`

Подробная инструкция: [docs/INSTALL_MACOS.md](docs/INSTALL_MACOS.md)

### Linux

1. Установите Python и FFmpeg:
```bash
sudo apt update
sudo apt install python3.10 python3.10-venv ffmpeg
```

2. Создайте виртуальное окружение:
```bash
python3.10 -m venv venv
source venv/bin/activate
```

3. Установите зависимости:
```bash
pip install -r requirements.txt
cd GigaAM
pip install -e .
```

4. Настройте HuggingFace токен в `src/config.py`

Подробная инструкция: [docs/INSTALL_LINUX.md](docs/INSTALL_LINUX.md)

## Настройка HuggingFace токена

1. Зарегистрируйтесь на [huggingface.co](https://huggingface.co)
2. Создайте токен доступа: [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
3. Примите условия использования модели: [huggingface.co/pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0)
4. Скопируйте `.env.example` в `.env`:
```bash
cp .env.example .env
```
5. Откройте файл `.env` и замените токен:
```bash
HF_TOKEN=ваш_токен_здесь
```

## Запуск приложения

### Windows
```bash
python app.py
```

### macOS
```bash
conda activate gigaam
python app.py
```

### Linux
```bash
source venv/bin/activate
python app.py
```

## Использование

1. Запустите приложение командой выше
2. В графическом интерфейсе нажмите "Выбрать файлы"
3. Выберите один или несколько аудио/видео файлов
4. (Опционально) Нажмите "Папка сохранения" для выбора каталога вывода
5. Нажмите "ЗАПУСТИТЬ ОБРАБОТКУ"
6. Дождитесь завершения обработки

### Результаты

Для каждого обработанного файла создается два файла:
- `имя_файла.txt` - чистый текст транскрибации
- `имя_файла_timecodes.txt` - текст с временными метками

По умолчанию файлы сохраняются в той же папке, где находится исходный файл.

## Структура проекта

```
GigaAMv3/
├── app.py                      # Точка входа приложения
├── requirements.txt            # Зависимости для Windows/Linux
├── requirements_macos.txt      # Зависимости для macOS
├── src/                        # Исходный код
│   ├── config.py              # Конфигурация (HF токен)
│   ├── core/                  # Основная логика
│   │   ├── model_loader.py    # Загрузка модели GigaAM
│   │   └── processor.py       # Обработка транскрибации
│   ├── gui/                   # Графический интерфейс
│   │   └── app.py            # GUI приложение
│   └── utils/                 # Утилиты
│       ├── audio_converter.py # Конвертация через FFmpeg
│       ├── time_formatter.py  # Форматирование времени
│       ├── processing_stats.py # Статистика
│       └── pyannote_patch.py  # Патчи совместимости
├── GigaAM/                    # Библиотека GigaAM
└── docs/                      # Документация
    ├── INSTALL_WINDOWS.md     # Установка для Windows
    ├── INSTALL_MACOS.md       # Установка для macOS
    ├── INSTALL_LINUX.md       # Установка для Linux
    ├── TROUBLESHOOTING.md     # Решение проблем
    └── API.md                 # API документация
```

## Документация

- [Установка на Windows](docs/INSTALL_WINDOWS.md)
- [Установка на macOS](docs/INSTALL_MACOS.md)
- [Установка на Linux](docs/INSTALL_LINUX.md)
- [Решение проблем](docs/TROUBLESHOOTING.md)
- [Система логирования](docs/LOGGING.md)
- [API документация](docs/API.md)
- [Авторство и благодарности](CREDITS.md)

## Технологии

### Ядро распознавания речи
- **GigaAM-v3** (Sber) — современная модель автоматического распознавания русской речи

### Фреймворки и библиотеки
- **Python 3.10+** — основной язык программирования
- **PyTorch 2.6.0** — фреймворк глубокого обучения
- **Transformers** (HuggingFace) — работа с transformer-моделями
- **Pyannote.audio** — сегментация и обработка аудио

### Интерфейс и медиа
- **CustomTkinter** — современный графический интерфейс
- **FFmpeg** — конвертация аудио и видео файлов

## Производительность

### Время обработки (примерные значения)

| Длительность аудио | CPU | GPU (CUDA/MPS) |
|-------------------|-----|----------------|
| 1 минута | 30-60 сек | 15-25 сек |
| 5 минут | 2.5-5 мин | 1-2 мин |
| 30 минут | 15-30 мин | 5-10 мин |
| 1 час | 30-60 мин | 10-20 мин |

Время зависит от конфигурации компьютера и качества аудио.

## Решение проблем

### Ошибка импорта pyannote.audio
```
AttributeError: `np.NaN` was removed in the NumPy 2.0 release
```
Патч применяется автоматически при запуске. Если проблема сохраняется, см. [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

### FFmpeg не найден
Убедитесь, что FFmpeg установлен и добавлен в PATH:
```bash
ffmpeg -version
```

### Недостаточно памяти
Обрабатывайте файлы по одному, закройте другие приложения, увеличьте swap/pagefile.

### Подробнее
См. полный список решений в [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

## Благодарности

Данная реализация работает благодаря:

- **Sber (ПАО «Сбербанк»)** — разработка и предоставление модели **GigaAM-v3**, современной модели транскрибации русской речи
- **Команда GigaAM** — за создание высококачественной модели распознавания речи
- **HuggingFace** — за инфраструктуру и библиотеки для работы с transformer-моделями
- **Pyannote.audio** — за инструменты сегментации и обработки аудио

## Контрибьютинг

Приветствуются pull request'ы и issue reports. Перед отправкой PR убедитесь, что:
- Код следует стилю проекта
- Добавлены комментарии на русском языке
- Проведено тестирование на вашей платформе

## Поддержка

При возникновении проблем:
1. Проверьте [документацию](docs/)
2. Посмотрите [решение проблем](docs/TROUBLESHOOTING.md)
3. Создайте issue на GitHub с подробным описанием проблемы

---

**GigaAM v3 Transcriber** - простой и эффективный инструмент для транскрибации аудио на русском языке.

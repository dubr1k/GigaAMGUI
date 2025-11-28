# Установка GigaAM v3 Transcriber на Windows

Полное руководство по установке приложения на Windows 10/11.

## Системные требования

### Минимальные
- Windows 10 (64-bit) или Windows 11
- Python 3.10 или выше
- 8 GB RAM
- 10 GB свободного места на диске
- Процессор: Intel Core i5 или аналогичный AMD

### Рекомендуемые
- Windows 11
- Python 3.11
- 16 GB RAM
- 20 GB свободного места на диске
- Процессор: Intel Core i7 или аналогичный AMD
- NVIDIA GPU с 6+ GB VRAM и CUDA 12.x

## Шаг 1. Установка Python

### 1.1. Скачивание Python

1. Перейдите на [python.org/downloads](https://www.python.org/downloads/)
2. Скачайте установщик Python 3.10 или 3.11 для Windows (64-bit)
3. Запустите установщик

### 1.2. Настройка установки

В установщике Python:
1. Обязательно отметьте галочку "Add Python to PATH"
2. Выберите "Customize installation"
3. Убедитесь, что отмечены:
   - pip
   - tcl/tk and IDLE
   - Python test suite
   - py launcher
4. На следующем экране отметьте:
   - Install for all users (опционально)
   - Add Python to environment variables
   - Precompile standard library
5. Нажмите "Install"

### 1.3. Проверка установки

Откройте Command Prompt или PowerShell и выполните:
```bash
python --version
```

Должно вывести: `Python 3.10.x` или `Python 3.11.x`

Также проверьте pip:
```bash
pip --version
```

## Шаг 2. Установка FFmpeg

FFmpeg необходим для конвертации видео и обработки различных аудио форматов.

### 2.1. Скачивание FFmpeg

#### Вариант A: Через winget (Windows 11)
```bash
winget install FFmpeg
```

#### Вариант B: Ручная установка

1. Перейдите на [ffmpeg.org/download.html](https://ffmpeg.org/download.html)
2. Выберите "Windows builds from gyan.dev"
3. Скачайте "ffmpeg-release-full.7z"
4. Распакуйте архив в `C:\ffmpeg`

### 2.2. Добавление FFmpeg в PATH

1. Откройте "Панель управления" > "Система" > "Дополнительные параметры системы"
2. Нажмите "Переменные среды"
3. В разделе "Системные переменные" найдите "Path" и нажмите "Изменить"
4. Нажмите "Создать" и добавьте путь: `C:\ffmpeg\bin`
5. Нажмите "ОК" во всех окнах

### 2.3. Проверка установки

Откройте новое окно Command Prompt и выполните:
```bash
ffmpeg -version
```

Должна вывестись информация о версии FFmpeg.

## Шаг 3. Клонирование репозитория

### 3.1. Установка Git (если не установлен)

1. Скачайте Git с [git-scm.com](https://git-scm.com/download/win)
2. Установите с настройками по умолчанию

### 3.2. Клонирование проекта

Откройте Command Prompt или PowerShell в нужной папке:
```bash
git clone https://github.com/ваш_username/GigaAMv3.git
cd GigaAMv3
```

Или скачайте ZIP архив с GitHub и распакуйте.

## Шаг 4. Создание виртуального окружения

### 4.1. Создание окружения

В папке проекта выполните:
```bash
python -m venv venv
```

### 4.2. Активация окружения

Для Command Prompt:
```bash
venv\Scripts\activate.bat
```

Для PowerShell:
```bash
venv\Scripts\Activate.ps1
```

Если PowerShell выдает ошибку "execution policy", выполните:
```bash
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

После активации в начале строки появится `(venv)`.

## Шаг 5. Установка PyTorch

### 5.1. Определение конфигурации

Выберите вариант установки в зависимости от наличия NVIDIA GPU:

#### С NVIDIA GPU (рекомендуется для скорости)

Проверьте версию CUDA:
1. Откройте NVIDIA Control Panel
2. Или выполните: `nvidia-smi`

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

### 5.2. Проверка установки PyTorch

```bash
python -c "import torch; print('PyTorch:', torch.__version__); print('CUDA:', torch.cuda.is_available())"
```

Ожидаемый вывод:
- С GPU: `PyTorch: 2.6.0` и `CUDA: True`
- Без GPU: `PyTorch: 2.6.0` и `CUDA: False`

## Шаг 6. Установка зависимостей проекта

### 6.1. Обновление pip

```bash
python -m pip install --upgrade pip
```

### 6.2. Установка основных зависимостей

```bash
pip install -r requirements.txt
```

Это установит все необходимые пакеты:
- transformers
- pyannote.audio
- customtkinter
- librosa
- soundfile
- и другие

### 6.3. Установка GigaAM

```bash
cd GigaAM
pip install -e .
cd ..
```

Флаг `-e` устанавливает пакет в режиме разработки.

### 6.4. Проверка установки

```bash
python -c "import gigaam; import transformers; import customtkinter; print('Все библиотеки установлены успешно')"
```

## Шаг 7. Настройка HuggingFace токена

### 7.1. Получение токена

1. Зарегистрируйтесь на [huggingface.co](https://huggingface.co/join)
2. Перейдите в [настройки токенов](https://huggingface.co/settings/tokens)
3. Нажмите "New token"
4. Введите имя (например, "GigaAM")
5. Выберите тип "Read"
6. Нажмите "Generate"
7. Скопируйте сгенерированный токен

### 7.2. Принятие условий модели

1. Перейдите на [huggingface.co/pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0)
2. Нажмите "Agree and access repository"
3. Также посетите [huggingface.co/ai-sage/GigaAM-v3](https://huggingface.co/ai-sage/GigaAM-v3)

### 7.3. Добавление токена в конфигурацию

Скопируйте файл `.env.example` в `.env`:
```bash
copy .env.example .env
```

Откройте файл `.env` в текстовом редакторе и замените строку:
```
HF_TOKEN=your_huggingface_token_here
```

на:
```
HF_TOKEN=ваш_токен_здесь
```

Сохраните файл.

Примечание: Файл `.env` содержит конфиденциальные данные и не должен публиковаться в Git.

## Шаг 8. Первый запуск

### 8.1. Запуск приложения

В папке проекта с активированным окружением:
```bash
python app.py
```

### 8.2. Что происходит при первом запуске

При первом запуске приложение:
1. Применит патчи совместимости
2. Загрузит модели из HuggingFace (около 2-3 GB)
3. Загрузка может занять 5-15 минут в зависимости от скорости интернета
4. Откроет графический интерфейс

### 8.3. Использование приложения

1. Нажмите "Выбрать файлы"
2. Выберите аудио или видео файл
3. (Опционально) Выберите папку для сохранения результатов
4. Нажмите "ЗАПУСТИТЬ ОБРАБОТКУ"
5. Дождитесь завершения

Результаты сохраняются в виде:
- `имя_файла.txt` - текст
- `имя_файла_timecodes.txt` - текст с таймкодами

## Шаг 9. Создание ярлыка (опционально)

### 9.1. Создание bat-файла

Создайте файл `start_gigaam.bat` в папке проекта:
```batch
@echo off
cd /d "%~dp0"
call venv\Scripts\activate.bat
python app.py
pause
```

### 9.2. Создание ярлыка

1. Правый клик на `start_gigaam.bat`
2. "Отправить" > "Рабочий стол (создать ярлык)"
3. Переименуйте ярлык в "GigaAM Transcriber"
4. Измените иконку (опционально)

## Обновление приложения

Для обновления до новой версии:

```bash
# Активируйте окружение
venv\Scripts\activate.bat

# Получите обновления из репозитория
git pull

# Обновите зависимости
pip install -r requirements.txt --upgrade

# Обновите GigaAM
cd GigaAM
git pull
pip install -e . --upgrade
cd ..
```

## Удаление приложения

Для полного удаления:

1. Удалите папку проекта
2. Удалите кэш моделей:
   - `%USERPROFILE%\.cache\huggingface`
   - `%USERPROFILE%\.cache\torch`
3. (Опционально) Удалите Python, если больше не нужен

## Решение проблем

### Ошибка "Python не найден"

Убедитесь, что Python добавлен в PATH. Переустановите Python с галочкой "Add Python to PATH".

### Ошибка "torch не найден" или проблемы с CUDA

Переустановите PyTorch с правильной версией CUDA:
```bash
pip uninstall torch torchvision torchaudio
# Затем установите снова с нужной версией CUDA
```

### Ошибка "FFmpeg не найден"

Проверьте, что FFmpeg в PATH:
```bash
where ffmpeg
```

Должно вывести путь к ffmpeg.exe. Если нет - проверьте переменные среды.

### Ошибка с pyannote.audio и NumPy

Патч применяется автоматически. Если проблема сохраняется:
```bash
pip install numpy==2.2.6 --force-reinstall
```

### Медленная работа

Причины медленной работы:
1. Отсутствие GPU - обработка идет на CPU
2. Недостаточно RAM - закройте другие приложения
3. Медленный диск - перенесите на SSD

Для проверки использования GPU:
```bash
python -c "import torch; print('CUDA доступна:', torch.cuda.is_available())"
```

### Ошибка "Out of memory" на GPU

Уменьшите batch size в коде или обрабатывайте более короткие файлы.

### Приложение не запускается

1. Проверьте логи в консоли
2. Убедитесь, что окружение активировано
3. Проверьте, что токен HuggingFace установлен
4. Переустановите зависимости

## Дополнительная настройка

### Использование переменной окружения для токена

Вместо хранения токена в config.py можно использовать переменную окружения:

1. Откройте "Панель управления" > "Система" > "Дополнительные параметры системы"
2. Нажмите "Переменные среды"
3. В "Переменные пользователя" нажмите "Создать"
4. Имя: `HF_TOKEN`
5. Значение: ваш токен
6. Нажмите "ОК"

Затем в `src/config.py` измените:
```python
import os
HF_TOKEN = os.environ.get("HF_TOKEN", "")
```

### Настройка количества потоков

Для CPU можно настроить количество потоков в `src/config.py`:
```python
import torch
torch.set_num_threads(4)  # Используйте половину от количества ядер
```

## Производительность

### Примерное время обработки на разных конфигурациях

#### CPU (Intel i7 10th gen, 16GB RAM)
- 1 минута аудио: 45-60 секунд
- 5 минут аудио: 4-5 минут
- 30 минут аудио: 20-30 минут

#### GPU (NVIDIA RTX 3060, 12GB VRAM)
- 1 минута аудио: 15-20 секунд
- 5 минут аудио: 1-1.5 минуты
- 30 минут аудио: 5-8 минут

#### GPU (NVIDIA RTX 4090, 24GB VRAM)
- 1 минута аудио: 8-12 секунд
- 5 минут аудио: 40-60 секунд
- 30 минут аудио: 3-5 минут

Время зависит от качества аудио, количества речи, фонового шума.

## Поддержка

При возникновении проблем:
1. Проверьте раздел "Решение проблем" выше
2. Посмотрите [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
3. Создайте issue на GitHub с описанием проблемы и логами

---

Установка завершена. Приятного использования GigaAM v3 Transcriber.


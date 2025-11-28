# Решение проблем GigaAM v3 Transcriber

Полное руководство по решению распространенных проблем при установке и использовании приложения.

## Содержание

- [Проблемы установки](#проблемы-установки)
- [Проблемы с зависимостями](#проблемы-с-зависимостями)
- [Проблемы с GPU](#проблемы-с-gpu)
- [Проблемы с моделями](#проблемы-с-моделями)
- [Проблемы с производительностью](#проблемы-с-производительностью)
- [Проблемы с FFmpeg](#проблемы-с-ffmpeg)
- [Проблемы с GUI](#проблемы-с-gui)
- [Ошибки при обработке](#ошибки-при-обработке)

---

## Проблемы установки

### Python не найден

**Симптомы:**
```
'python' is not recognized as an internal or external command
```

**Решение для Windows:**
1. Проверьте установку: откройте Command Prompt и введите `python --version`
2. Если не работает, переустановите Python с галочкой "Add Python to PATH"
3. Или добавьте вручную в PATH: `C:\Users\USERNAME\AppData\Local\Programs\Python\Python310`

**Решение для Linux/macOS:**
```bash
# Используйте python3 вместо python
python3 --version

# Или создайте алиас
echo 'alias python=python3' >> ~/.bashrc
source ~/.bashrc
```

### pip не найден

**Симптомы:**
```
pip: command not found
```

**Решение:**
```bash
# Windows
python -m pip --version

# Linux/macOS
python3 -m pip --version

# Если не помогло, установите pip
python3 -m ensurepip --upgrade
```

### Ошибка создания виртуального окружения

**Симптомы:**
```
Error: Command 'venv' not found
```

**Решение для Ubuntu/Debian:**
```bash
sudo apt install python3-venv
```

**Решение для Windows:**
```bash
python -m pip install virtualenv
python -m virtualenv venv
```

---

## Проблемы с зависимостями

### Ошибка с NumPy и pyannote.audio

**Симптомы:**
```
AttributeError: `np.NaN` was removed in the NumPy 2.0 release
```

**Решение:**
Патч применяется автоматически при запуске `app.py`. Если проблема сохраняется:

```bash
# Вариант 1: Понизить версию NumPy
pip install numpy==1.26.4

# Вариант 2: Применить патч вручную
python -c "import numpy as np; np.NaN = np.nan"
```

Или добавьте в начало вашего скрипта:
```python
import numpy as np
if not hasattr(np, 'NaN'):
    np.NaN = np.nan
```

### Ошибка установки soundfile

**Симптомы:**
```
ERROR: Failed building wheel for soundfile
```

**Решение для Windows:**
```bash
pip install soundfile --no-cache-dir
```

**Решение для Linux:**
```bash
sudo apt install libsndfile1-dev
pip install soundfile
```

**Решение для macOS:**
```bash
brew install libsndfile
pip install soundfile
```

### Ошибка установки pyaudio

**Симптомы:**
```
ERROR: Could not build wheels for pyaudio
```

**Решение для Windows:**
```bash
pip install pipwin
pipwin install pyaudio
```

**Решение для Linux:**
```bash
sudo apt install portaudio19-dev python3-pyaudio
pip install pyaudio
```

**Решение для macOS:**
```bash
brew install portaudio
pip install pyaudio
```

### Конфликт версий пакетов

**Симптомы:**
```
ERROR: pip's dependency resolver does not currently take into account all the packages
```

**Решение:**
```bash
# Очистите кеш pip
pip cache purge

# Переустановите зависимости
pip uninstall -r requirements.txt -y
pip install -r requirements.txt --no-cache-dir
```

---

## Проблемы с GPU

### CUDA не найдена на Windows/Linux

**Симптомы:**
```python
>>> import torch
>>> torch.cuda.is_available()
False
```

**Проверка CUDA:**
```bash
# Windows
nvidia-smi

# Linux
nvidia-smi
nvcc --version
```

**Решение:**
1. Убедитесь, что установлены драйверы NVIDIA
2. Установите CUDA Toolkit с [developer.nvidia.com/cuda-downloads](https://developer.nvidia.com/cuda-downloads)
3. Переустановите PyTorch с правильной версией CUDA:

```bash
# Для CUDA 12.4
pip uninstall torch torchvision torchaudio
pip install torch==2.6.0 torchvision==0.21.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124

# Для CUDA 11.8
pip install torch==2.6.0 torchvision==0.21.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu118
```

### MPS не работает на macOS

**Симптомы:**
```python
>>> torch.backends.mps.is_available()
False
```

**Требования для MPS:**
- macOS 12.3 или выше
- Mac с Apple Silicon (M1/M2/M3)
- PyTorch 1.12 или выше

**Решение:**
```bash
# Обновите macOS до 12.3+
# Обновите PyTorch
pip install --upgrade torch torchvision torchaudio
```

### Out of memory на GPU

**Симптомы:**
```
RuntimeError: CUDA out of memory
```

**Решение:**
1. Уменьшите batch size в коде
2. Обрабатывайте более короткие файлы
3. Закройте другие приложения, использующие GPU
4. Очистите кеш CUDA:

```python
import torch
torch.cuda.empty_cache()
```

5. Используйте CPU вместо GPU:
```python
# В src/core/model_loader.py измените
device = torch.device("cpu")
```

### GPU не используется

**Проверка использования GPU:**

Windows/Linux:
```bash
# В отдельном терминале запустите
nvidia-smi -l 1
```

macOS:
```bash
# Проверьте в Activity Monitor или
sudo powermetrics --samplers gpu_power -i 1000
```

**Решение:**
Убедитесь, что модель загружена на GPU. Проверьте в коде:
```python
print(f"Device: {model.device}")
```

---

## Проблемы с моделями

### Ошибка загрузки модели из HuggingFace

**Симптомы:**
```
HTTPError: 401 Client Error: Unauthorized for url
```

**Решение:**
1. Проверьте токен HuggingFace в `src/config.py`
2. Убедитесь, что токен начинается с `hf_`
3. Проверьте, что приняли условия модели:
   - [huggingface.co/pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0)
   - [huggingface.co/ai-sage/GigaAM-v3](https://huggingface.co/ai-sage/GigaAM-v3)

### Медленная загрузка моделей

**Симптомы:**
Первый запуск занимает очень много времени.

**Причина:**
Модели загружаются из интернета (около 2-3 GB).

**Решение:**
1. Дождитесь завершения загрузки
2. В последующие запуски модели будут браться из кеша
3. Кеш находится в:
   - Windows: `C:\Users\USERNAME\.cache\huggingface`
   - Linux/macOS: `~/.cache/huggingface`

### Ошибка кеша моделей

**Симптомы:**
```
OSError: Cannot load model from cache
```

**Решение:**
```bash
# Очистите кеш
# Windows
rmdir /s %USERPROFILE%\.cache\huggingface

# Linux/macOS
rm -rf ~/.cache/huggingface

# Модели загрузятся заново при следующем запуске
```

---

## Проблемы с производительностью

### Очень медленная обработка

**Возможные причины и решения:**

1. **Используется CPU вместо GPU:**
   ```python
   import torch
   print("CUDA:", torch.cuda.is_available())  # Windows/Linux
   print("MPS:", torch.backends.mps.is_available())  # macOS
   ```

2. **Недостаточно RAM:**
   - Закройте другие приложения
   - Увеличьте swap/pagefile
   - Обрабатывайте файлы по одному

3. **Медленный диск:**
   - Перенесите проект и временные файлы на SSD
   - Освободите место на диске

4. **Фоновые процессы:**
   - Проверьте Task Manager / Activity Monitor
   - Отключите антивирус на время обработки

### Приложение зависает

**Решение:**
1. Проверьте логи в консоли
2. Уменьшите размер обрабатываемого файла
3. Увеличьте таймауты в коде
4. Проверьте наличие свободной памяти

### Высокая нагрузка на CPU

**Это нормально** при обработке без GPU. Для снижения нагрузки:

```python
# В начале src/core/model_loader.py
import torch
torch.set_num_threads(4)  # Ограничьте количество потоков
```

---

## Проблемы с FFmpeg

### FFmpeg не найден

**Симптомы:**
```
FileNotFoundError: ffmpeg not found
```

**Проверка:**
```bash
ffmpeg -version
```

**Решение для Windows:**
1. Скачайте FFmpeg с [ffmpeg.org](https://ffmpeg.org/download.html)
2. Распакуйте в `C:\ffmpeg`
3. Добавьте `C:\ffmpeg\bin` в PATH
4. Перезапустите Command Prompt

**Решение для Linux:**
```bash
sudo apt install ffmpeg  # Ubuntu/Debian
sudo dnf install ffmpeg  # Fedora
sudo pacman -S ffmpeg    # Arch
```

**Решение для macOS:**
```bash
brew install ffmpeg
```

### Ошибка конвертации видео

**Симптомы:**
```
Error: Conversion failed for file.mp4
```

**Решение:**
1. Проверьте, что видео не повреждено
2. Попробуйте конвертировать вручную:
```bash
ffmpeg -i input.mp4 -ar 16000 -ac 1 output.wav
```
3. Если не работает, файл поврежден или имеет неподдерживаемый кодек

### Медленная конвертация

**Решение:**
FFmpeg использует CPU. Для ускорения:
```bash
# Используйте аппаратное ускорение (если доступно)
ffmpeg -hwaccel auto -i input.mp4 output.wav
```

---

## Проблемы с GUI

### Окно не открывается

**Симптомы:**
Приложение запускается, но окно не появляется.

**Решение для Linux:**
```bash
# Проверьте DISPLAY
echo $DISPLAY

# Установите, если пусто
export DISPLAY=:0

# Установите необходимые пакеты
sudo apt install python3-tk
```

**Решение для Windows:**
Проверьте антивирус и брандмауэр.

**Решение для macOS:**
```bash
# Дайте разрешение в System Preferences > Security & Privacy
# Переустановите Python с поддержкой Tk
brew install python-tk@3.10
```

### Окно открывается, но кнопки не работают

**Решение:**
1. Проверьте версию customtkinter:
```bash
pip show customtkinter
```

2. Переустановите:
```bash
pip uninstall customtkinter
pip install customtkinter==5.2.2
```

### Некорректное отображение на HiDPI

**Решение для Windows:**
Добавьте в начало `app.py`:
```python
import ctypes
ctypes.windll.shcore.SetProcessDpiAwareness(1)
```

**Решение для Linux:**
```bash
export GDK_SCALE=2
export GDK_DPI_SCALE=0.5
python app.py
```

---

## Ошибки при обработке

### Ошибка сегментации (Segmentation fault)

**Возможные причины:**
1. Поврежденный аудио файл
2. Недостаточно памяти
3. Конфликт библиотек

**Решение:**
```bash
# Переустановите критические библиотеки
pip uninstall torch torchaudio soundfile librosa -y
pip install torch torchaudio soundfile librosa --no-cache-dir
```

### Пустой результат транскрибации

**Возможные причины:**
1. В аудио нет речи
2. Плохое качество записи
3. Неподдерживаемый язык (модель работает только с русским)

**Решение:**
1. Проверьте аудио вручную
2. Убедитесь, что речь на русском языке
3. Попробуйте улучшить качество аудио

### Неправильная транскрибация

**Возможные причины:**
1. Плохое качество аудио
2. Сильный фоновый шум
3. Быстрая речь
4. Специфическая терминология

**Решение:**
1. Улучшите качество исходного аудио
2. Удалите фоновый шум (например, через Audacity)
3. Используйте аудио с четкой речью

### Ошибка при обработке длинных файлов

**Симптомы:**
```
MemoryError: Cannot allocate memory
```

**Решение:**
1. Разбейте файл на части:
```bash
ffmpeg -i long_audio.mp3 -f segment -segment_time 600 -c copy part_%03d.mp3
```

2. Обработайте части по отдельности
3. Объедините результаты

---

## Логи и отладка

### Включение подробных логов

Добавьте в начало `app.py`:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Сохранение логов в файл

```python
import logging
logging.basicConfig(
    level=logging.DEBUG,
    filename='gigaam.log',
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
```

### Проверка версий всех пакетов

```bash
pip list > installed_packages.txt
```

### Тест минимального примера

Создайте `test_minimal.py`:
```python
import torch
import transformers
import gigaam
import customtkinter

print("PyTorch:", torch.__version__)
print("CUDA:", torch.cuda.is_available())
print("Transformers:", transformers.__version__)
print("GigaAM: OK")
print("CustomTkinter: OK")
print("All imports successful!")
```

Запустите:
```bash
python test_minimal.py
```

---

## Получение помощи

Если проблема не решена:

1. **Соберите информацию:**
   - Версия ОС
   - Версия Python
   - Версии установленных пакетов (`pip list`)
   - Полный текст ошибки
   - Логи приложения

2. **Проверьте существующие issue:**
   - GitHub Issues проекта
   - Stack Overflow

3. **Создайте новый issue:**
   - Опишите проблему подробно
   - Приложите логи и скриншоты
   - Укажите шаги для воспроизведения

4. **Сообщество:**
   - Форум HuggingFace
   - Reddit r/MachineLearning
   - Telegram группы по ML

---

## Полезные команды для диагностики

### Проверка системы

```bash
# Информация о системе
python -c "import platform; print(platform.platform())"

# Информация о Python
python --version
pip --version

# Проверка GPU
python -c "import torch; print('CUDA:', torch.cuda.is_available()); print('Device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"

# Проверка памяти
python -c "import psutil; print(f'RAM: {psutil.virtual_memory().total / (1024**3):.1f} GB')"

# Проверка места на диске
python -c "import shutil; print(f'Free: {shutil.disk_usage('.').free / (1024**3):.1f} GB')"
```

### Очистка для чистой переустановки

```bash
# Деактивируйте окружение
deactivate  # Linux/macOS
# или просто закройте терминал для Windows

# Удалите окружение
rm -rf venv  # Linux/macOS
rmdir /s venv  # Windows

# Очистите кеши
rm -rf ~/.cache/pip
rm -rf ~/.cache/huggingface
rm -rf ~/.cache/torch

# Пересоздайте окружение
python -m venv venv
source venv/bin/activate  # Linux/macOS
venv\Scripts\activate  # Windows

# Переустановите всё
pip install --upgrade pip
pip install -r requirements.txt
cd GigaAM && pip install -e . && cd ..
```

---

Если ваша проблема не описана в этом руководстве, создайте issue на GitHub с подробным описанием.


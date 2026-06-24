# Сборка EXE — инструкция

## Что получится

Папка `dist/GigaAMTranscriber/` с `GigaAMTranscriber.exe` и всеми DLL-зависимостями.  
Модель GigaAM (~1–2 ГБ) **не включается** в EXE — скачивается при первом запуске автоматически.  
**Токен HuggingFace не нужен** — модель публичная.

---

## Требования перед сборкой

- Python 3.10–3.12 (64-bit)  
- Виртуальное окружение с установленными зависимостями (`pip install -r requirements.txt`)  
- FFmpeg в PATH (для конвертации аудио)  
- ~10 GB свободного места (PyTorch + CUDA + сборка)

---

## Шаг 1 — активируй виртуальное окружение

```bat
# Если окружение в venv/
venv\Scripts\activate

# Или если используешь conda
conda activate gigaam
```

После активации проверь:
```bat
python -c "import gigaam; import torch; import PyQt6; print('OK')"
```

---

## Шаг 2 — запусти сборку

```bat
build_exe.bat
```

Или вручную:
```bat
pip install pyinstaller --upgrade
pyinstaller gigaam_app.spec --noconfirm
```

Сборка занимает **5–20 минут** в зависимости от скорости диска.

---

## Шаг 3 — проверь результат

```bat
dist\GigaAMTranscriber\GigaAMTranscriber.exe
```

При первом запуске появится уведомление о скачивании модели (~1–2 ГБ).  
Модель сохраняется в `C:\HuggingFaceCache\` (создаётся автоматически).

---

## Раздача другим пользователям

Скопируй **всю папку** `dist\GigaAMTranscriber\` — она полностью автономна.  
Дополнительно у получателя должен быть установлен **FFmpeg**:
- Скачать: https://ffmpeg.org/download.html  
- Распакуй и добавь `bin/` в PATH, или положи `ffmpeg.exe` рядом с EXE

Или собери FFmpeg внутрь — добавь в spec в секцию `datas`:
```python
('C:/path/to/ffmpeg.exe', '.'),
```

---

## Возможные проблемы

### `ModuleNotFoundError: No module named 'gigaam'`
gigaam установлен как editable. Убедись что venv активирован и `pip show gigaam` работает.

### Очень большой размер папки (>5 GB)
Это нормально для CUDA-сборки PyTorch. Для CPU-only можно переустановить torch без CUDA:
```bat
pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cpu
```
Размер уменьшится до ~1.5 GB.

### `Failed to execute script` при запуске EXE
Запусти EXE из командной строки чтобы увидеть ошибку:
```bat
dist\GigaAMTranscriber\GigaAMTranscriber.exe
```
Если не хватает модуля — добавь его в `hiddenimports` в `gigaam_app.spec`.

### Ошибка кириллицы в пути (Windows)
Приложение автоматически переключает кэш HF в `C:\HuggingFaceCache`.

---

## Диаризация (разделение по спикерам)

Для диаризации нужен HF-токен с доступом к `pyannote/segmentation-3.0`:
1. Зарегистрируйся на huggingface.co
2. Прими условия модели: huggingface.co/pyannote/segmentation-3.0
3. Создай токен: huggingface.co/settings/tokens  
4. Положи рядом с EXE файл `.env`:
```
HF_TOKEN=hf_ваш_токен_здесь
```

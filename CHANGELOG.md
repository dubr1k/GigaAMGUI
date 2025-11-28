# Changelog

Все значимые изменения в проекте GigaAM v3 Transcriber будут документированы в этом файле.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.0.0/),
и этот проект придерживается [семантического версионирования](https://semver.org/lang/ru/).

## [Unreleased]

### Планируется
- Экспорт в формат SRT субтитров
- Экспорт в формат VTT субтитров
- Экспорт в JSON формат
- CLI интерфейс для batch обработки
- Параллельная обработка файлов
- Поддержка real-time транскрибации
- Темная тема интерфейса
- Настройки в GUI
- История обработанных файлов

## [1.0.0] - 2025-11-28

### Добавлено
- Первый публичный релиз
- Графический интерфейс на CustomTkinter
- Поддержка транскрибации аудио форматов: MP3, WAV, M4A, FLAC, OGG, WMA
- Поддержка транскрибации видео форматов: MP4, AVI, MOV, MKV, WEBM
- Автоматическая конвертация медиа файлов через FFmpeg
- Автоматическая сегментация длинных записей через pyannote.audio
- Вывод результатов в двух форматах: чистый текст и текст с таймкодами
- Прогресс-бар с оценкой времени завершения
- Статистика обработки и самообучающийся прогноз времени
- Поддержка GPU ускорения (CUDA для Windows/Linux, MPS для macOS)
- Модульная архитектура с разделением на слои
- Автоматические патчи совместимости для NumPy 2.0
- Подробная документация для Windows, macOS и Linux
- Руководство по решению проблем

### Компоненты
- `src/core/model_loader.py` - загрузка модели GigaAM
- `src/core/processor.py` - обработка транскрибации
- `src/gui/app.py` - графический интерфейс
- `src/utils/audio_converter.py` - конвертация через FFmpeg
- `src/utils/time_formatter.py` - форматирование времени
- `src/utils/processing_stats.py` - сбор статистики
- `src/utils/pyannote_patch.py` - патчи совместимости

### Документация
- Установка для Windows: `docs/INSTALL_WINDOWS.md`
- Установка для macOS: `docs/INSTALL_MACOS.md`
- Установка для Linux: `docs/INSTALL_LINUX.md`
- Решение проблем: `docs/TROUBLESHOOTING.md`
- API документация: `docs/API.md`
- Руководство для контрибьюторов: `CONTRIBUTING.md`

### Зависимости
- Python 3.10+
- PyTorch 2.6.0
- Transformers 4.57.3
- pyannote.audio 3.1.1
- CustomTkinter 5.2.2
- FFmpeg 8.0+

### Производительность
- Поддержка NVIDIA GPU с CUDA 11.8/12.x
- Поддержка Apple Silicon с MPS
- Оптимизация для CPU обработки
- Эффективное использование памяти

### Безопасность
- Безопасное хранение HuggingFace токена
- Валидация входных файлов
- Защита от переполнения памяти

---

## Типы изменений

- `Added` - новый функционал
- `Changed` - изменения в существующем функционале
- `Deprecated` - функционал, который скоро будет удален
- `Removed` - удаленный функционал
- `Fixed` - исправления ошибок
- `Security` - исправления уязвимостей

---

[Unreleased]: https://github.com/your-username/GigaAMv3/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/your-username/GigaAMv3/releases/tag/v1.0.0


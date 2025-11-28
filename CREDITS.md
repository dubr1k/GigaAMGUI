# Авторство и благодарности

## О модели GigaAM-v3

Данное приложение использует модель **GigaAM-v3**, разработанную компанией **Sber (ПАО «Сбербанк»)**.

### Информация о модели

- **Название:** GigaAM-v3 (GigaAudio Model, version 3)
- **Разработчик:** Команда GigaAM, Sber
- **Назначение:** Автоматическое распознавание русской речи (ASR - Automatic Speech Recognition)
- **Тип:** Transformer-based модель для транскрибации аудио
- **Язык:** Русский

### Ресурсы

- **Модель на HuggingFace:** [ai-sage/GigaAM-v3](https://huggingface.co/ai-sage/GigaAM-v3)
- **Официальный репозиторий:** [github.com/salute-developers/GigaAM](https://github.com/salute-developers/GigaAM)
- **Документация:** [GigaAM README](https://github.com/salute-developers/GigaAM/blob/main/README.md)

## Об этой реализации

Данное приложение представляет собой **обёртку с графическим интерфейсом** для удобной работы с моделью GigaAM-v3.

### Реализовано:

- Графический интерфейс на CustomTkinter
- Поддержка множества аудио и видео форматов через FFmpeg
- Автоматическая конвертация медиафайлов
- Прогресс-бар с прогнозом времени обработки
- Статистика обработки с самообучающимся алгоритмом
- Система логирования
- Кросс-платформенная поддержка (Windows, macOS, Linux)
- Поддержка GPU ускорения (CUDA, MPS)

### Используемые технологии

1. **GigaAM-v3** (Sber) — модель распознавания речи
2. **PyTorch** — фреймворк глубокого обучения
3. **Transformers** (HuggingFace) — библиотека для работы с transformer-моделями
4. **Pyannote.audio** — сегментация и обработка аудио
5. **CustomTkinter** — современный графический интерфейс
6. **FFmpeg** — конвертация аудио и видео файлов

## Благодарности

### Компании и организации

- **Sber (ПАО «Сбербанк»)** — за разработку и открытый доступ к модели GigaAM-v3
- **GigaChat Team** — за создание высококачественной модели распознавания речи
- **HuggingFace** — за платформу и инфраструктуру для обмена моделями
- **CNRS (French National Centre for Scientific Research)** — за разработку Pyannote.audio

### Проекты с открытым исходным кодом

- **PyTorch** — Facebook AI Research (FAIR)
- **Transformers** — HuggingFace Team
- **Pyannote.audio** — Hervé Bredin и команда
- **CustomTkinter** — Tom Schimansky
- **FFmpeg** — FFmpeg Team

## Условия использования модели

Модель GigaAM-v3 распространяется под лицензией MIT (см. [GigaAM/LICENSE](GigaAM/LICENSE)).

При использовании модели в своих проектах, пожалуйста:
- Указывайте авторство Sber и команды GigaAM
- Ссылайтесь на официальные ресурсы модели
- Соблюдайте условия лицензии MIT

## Цитирование

Если вы используете модель GigaAM-v3 в научных работах или публикациях, пожалуйста, ссылайтесь на:

```
GigaAM-v3: Модель автоматического распознавания русской речи
Разработчик: Sber, GigaChat Team
URL: https://huggingface.co/ai-sage/GigaAM-v3
```

## Контакты

### По вопросам работы приложения

Создавайте Issues в этом репозитории на GitHub.

### По вопросам модели GigaAM-v3

Обращайтесь в официальный репозиторий: [github.com/salute-developers/GigaAM](https://github.com/salute-developers/GigaAM)

---

**Версия документа:** 1.0  
**Дата:** 28.11.2025



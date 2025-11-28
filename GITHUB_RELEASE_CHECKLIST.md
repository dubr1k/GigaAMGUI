# Checklist для публикации на GitHub

## Перед публикацией

### Безопасность
- [x] Токен HuggingFace перенесен в .env
- [x] .env добавлен в .gitignore
- [x] .env.example создан с примером конфигурации
- [x] Проверено, что src/config.py загружает переменные из .env
- [ ] Проверьте, что в коде нет других секретных ключей или токенов

### Документация
- [x] README.md обновлен с полной информацией
- [x] INSTALL_WINDOWS.md создан
- [x] INSTALL_MACOS.md создан
- [x] INSTALL_LINUX.md создан
- [x] TROUBLESHOOTING.md создан
- [x] API.md создан
- [x] CONTRIBUTING.md создан
- [x] CHANGELOG.md создан
- [x] SETUP.md создан для быстрого старта

### Конфигурация проекта
- [x] .gitignore настроен корректно
- [x] requirements.txt актуален
- [x] GigaAM добавлен как submodule или описан процесс установки

### GitHub специфичные файлы
- [x] .github/workflows/test.yml создан (CI/CD)
- [x] .github/ISSUE_TEMPLATE/bug_report.md создан
- [x] .github/ISSUE_TEMPLATE/feature_request.md создан
- [x] .github/pull_request_template.md создан

### Код
- [ ] Все import'ы корректны
- [ ] Код следует PEP 8
- [ ] Комментарии на русском языке присутствуют
- [ ] Нет закомментированного кода
- [ ] Нет debug print'ов (кроме необходимых)

### Тестирование
- [ ] Приложение запускается без ошибок
- [ ] GUI открывается корректно
- [ ] Можно выбрать файлы
- [ ] Обработка файлов работает
- [ ] Результаты сохраняются корректно
- [ ] Тесты в папке tests/ проходят (если есть)

## Создание репозитория на GitHub

### 1. Создайте новый репозиторий
- Перейдите на [github.com/new](https://github.com/new)
- Название: `GigaAMv3` или другое на ваш выбор
- Описание: "Приложение для транскрибации аудио и видео с использованием GigaAM v3"
- Выберите Public или Private
- НЕ добавляйте README, .gitignore (они уже есть)

### 2. Инициализируйте Git (если еще не сделано)

```bash
cd /Users/dubr1k/VSCode/GigaAMv3
git init
```

### 3. Добавьте файлы

```bash
# Проверьте статус
git status

# Добавьте все файлы (кроме игнорируемых)
git add .

# Проверьте, что .env НЕ добавлен
git status | grep .env

# Если .env в списке, удалите из staging
git reset .env
```

### 4. Создайте первый коммит

```bash
git commit -m "Initial commit: GigaAM v3 Transcriber

- Добавлен графический интерфейс на CustomTkinter
- Поддержка множества аудио/видео форматов
- Автоматическая сегментация через pyannote.audio
- Поддержка GPU (CUDA/MPS)
- Полная документация для Windows/macOS/Linux
- Система статистики и прогноза времени
"
```

### 5. Добавьте remote и push

```bash
# Замените на URL вашего репозитория
git remote add origin https://github.com/your-username/GigaAMv3.git

# Push в main ветку
git branch -M main
git push -u origin main
```

### 6. Добавьте GigaAM как submodule (если еще не сделано)

```bash
git submodule add https://github.com/salute-developers/GigaAM.git GigaAM
git commit -m "Add GigaAM submodule"
git push
```

## После публикации

### Настройка репозитория

- [ ] Добавьте описание и теги в настройках репозитория
- [ ] Добавьте темы: python, pytorch, transcription, speech-recognition, audio-processing
- [ ] Настройте GitHub Pages (если нужна документация онлайн)
- [ ] Включите Issues
- [ ] Включите Discussions (опционально)

### Создайте первый Release

1. Перейдите в Releases
2. Нажмите "Create a new release"
3. Tag version: `v1.0.0`
4. Release title: "GigaAM v3 Transcriber v1.0.0"
5. Опишите изменения из CHANGELOG.md
6. Publish release

### README badges (опционально)

Добавьте в начало README.md:

```markdown
![Python Version](https://img.shields.io/badge/python-3.10%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![Model](https://img.shields.io/badge/model-GigaAM--v3-orange)
```

### Проверка

- [ ] README корректно отображается на GitHub
- [ ] Документация в docs/ доступна
- [ ] .env не виден в репозитории
- [ ] .env.example виден и корректен
- [ ] Ссылки в документации работают
- [ ] Issue templates работают
- [ ] PR template работает

## Распространение

### Опционально

- [ ] Опубликуйте на [Habr](https://habr.com)
- [ ] Поделитесь в социальных сетях
- [ ] Добавьте в [awesome-python](https://github.com/vinta/awesome-python)
- [ ] Создайте видео демонстрацию
- [ ] Напишите статью на Medium

## Поддержка

- [ ] Настройте GitHub Sponsors (опционально)
- [ ] Добавьте способы связи для поддержки
- [ ] Мониторьте Issues и PR

---

## Команды для проверки перед push

```bash
# Проверка, что .env не будет запушен
git status | grep .env
# Не должно быть в списке для коммита

# Проверка игнорируемых файлов
git check-ignore -v .env
# Должно показать .gitignore:.env

# Просмотр всех файлов для коммита
git diff --cached --name-only

# Просмотр изменений
git diff --cached

# Если нужно удалить файл из Git но оставить локально
git rm --cached src/config.py
# Затем добавьте в .gitignore если еще не добавлен
```

---

Готово к публикации!


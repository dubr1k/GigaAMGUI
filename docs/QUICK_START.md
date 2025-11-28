# 🚀 Быстрый старт GigaAM v3 (macOS)

## Активация и запуск

```bash
# В терминале:
cd /Users/dubr1k/VSCode/GigaAMv3
source activate_gigaam.sh
python app.py
```

## Альтернативный способ

```bash
# Активировать окружение
conda activate gigaam

# Перейти в каталог проекта
cd /Users/dubr1k/VSCode/GigaAMv3

# Запустить приложение
python app.py
```

## Проверка установки

```bash
conda activate gigaam
python -c "import torch; print('PyTorch:', torch.__version__); print('MPS:', torch.backends.mps.is_available())"
```

Ожидаемый результат:
```
PyTorch: 2.6.0
MPS: True
```

## Деактивация

```bash
conda deactivate
```

---

**Всё готово! Приятного использования! 🎉**

Подробная инструкция: [INSTALL_MACOS.md](INSTALL_MACOS.md)


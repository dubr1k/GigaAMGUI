#!/usr/bin/env python3
"""
Скрипт для скачивания обновленных моделей GigaAM для распознавания речи
"""

import os
import sys

# Добавляем путь к пакету gigaam
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src', 'gigaam'))

# Применяем патч для PyTorch 2.6+ (weights_only=False)
# Это должно быть ПЕРЕД импортом gigaam и torch
try:
    from src.utils.torch_patch import apply_torch_load_patch
    apply_torch_load_patch()
except ImportError:
    # Патч не критичен, продолжаем без него
    pass

try:
    import gigaam  # noqa: F401  (проверка доступности пакета)
    from gigaam import load_model
    print("✓ Пакет gigaam успешно импортирован")
except ImportError as e:
    print(f"✗ Ошибка импорта gigaam: {e}")
    print("Убедитесь, что виртуальное окружение активировано и пакет установлен")
    sys.exit(1)

def download_recognition_models():
    """Скачивает модели для распознавания речи"""

    # Модели для распознавания речи (ASR)
    recognition_models = [
        "v3_e2e_rnnt",  # End-to-end RNN-T (основная модель для проекта)
        "v3_e2e_ctc",   # End-to-end CTC
        "v3_ctc",       # CTC модель
        "v3_rnnt",      # RNN-T модель
    ]

    print("=" * 60)
    print("Скачивание моделей GigaAM для распознавания речи")
    print("=" * 60)
    print(f"Всего моделей для скачивания: {len(recognition_models)}")
    print()

    downloaded = []
    failed = []

    for model_name in recognition_models:
        print(f"\n📥 Скачивание модели: {model_name}")
        print("-" * 60)

        try:
            # Загружаем модель (это автоматически скачает её, если её нет в кеше)
            model = load_model(model_name, fp16_encoder=True)
            print(f"✓ Модель {model_name} успешно загружена")
            downloaded.append(model_name)

            # Освобождаем память
            del model

        except Exception as e:
            print(f"✗ Ошибка при загрузке модели {model_name}: {e}")
            failed.append(model_name)

    print("\n" + "=" * 60)
    print("РЕЗУЛЬТАТЫ СКАЧИВАНИЯ")
    print("=" * 60)
    print(f"✓ Успешно скачано: {len(downloaded)}")
    for model in downloaded:
        print(f"  - {model}")

    if failed:
        print(f"\n✗ Ошибки при скачивании: {len(failed)}")
        for model in failed:
            print(f"  - {model}")

    print("\n" + "=" * 60)
    print("Модели сохранены в: ~/.cache/gigaam/")
    print("=" * 60)

if __name__ == "__main__":
    download_recognition_models()

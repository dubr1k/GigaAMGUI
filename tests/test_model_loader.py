"""Тесты управления памятью модели (Phase 2.5) — без реальной загрузки весов."""

from src.core.model_loader import ModelLoader


def test_empty_cache_safe_when_cpu():
    loader = ModelLoader()
    loader.device = "cpu"
    # Не должно бросать исключений на CPU
    loader._empty_cache()


def test_unload_clears_model():
    loader = ModelLoader()
    loader.model = object()  # заглушка вместо модели
    loader.device = "cpu"
    assert loader.is_loaded() is True
    loader.unload()
    assert loader.model is None
    assert loader.is_loaded() is False

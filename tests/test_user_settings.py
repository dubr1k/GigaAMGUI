"""Тесты пользовательских настроек (Phase 4.1)."""

from src.utils.user_settings import UserSettings


def test_output_dir_roundtrip(tmp_path):
    f = str(tmp_path / "settings.json")
    s = UserSettings(settings_file=f)
    s.set_last_output_dir(str(tmp_path))
    # Новый экземпляр читает с диска
    s2 = UserSettings(settings_file=f)
    assert s2.get_last_output_dir() == str(tmp_path)


def test_get_returns_none_for_missing_dir(tmp_path):
    f = str(tmp_path / "settings.json")
    s = UserSettings(settings_file=f)
    s.set_last_output_dir(str(tmp_path / "does_not_exist"))
    # Несуществующая директория не должна возвращаться
    assert s.get_last_output_dir() is None


def test_corrupt_settings_recovers(tmp_path):
    f = tmp_path / "settings.json"
    f.write_text("{ broken", encoding="utf-8")
    s = UserSettings(settings_file=str(f))
    assert s.settings == {}


def test_set_last_files_dir_from_file_path(tmp_path):
    f = str(tmp_path / "settings.json")
    media = tmp_path / "audio.mp3"
    media.write_bytes(b"x")
    s = UserSettings(settings_file=f)
    s.set_last_files_dir(str(media))
    # Должна сохраниться директория файла, а не сам файл
    assert s.get_last_files_dir() == str(tmp_path)

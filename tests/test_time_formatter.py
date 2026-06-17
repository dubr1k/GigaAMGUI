"""Тесты форматирования времени (Phase 4.1)."""

import pytest

from src.utils.time_formatter import TimeFormatter


@pytest.mark.parametrize("seconds,expected", [
    (0, "00:00"),
    (59, "00:59"),
    (60, "01:00"),
    (3599, "59:59"),
    (3600, "01:00:00"),
    (3661, "01:01:01"),
])
def test_format_timestamp(seconds, expected):
    assert TimeFormatter.format_timestamp(seconds) == expected


@pytest.mark.parametrize("seconds,expected", [
    (0, "0 сек"),
    (59, "59 сек"),
    (60, "1 мин 0 сек"),
    (90, "1 мин 30 сек"),
    (3600, "1 ч 0 мин"),
    (3700, "1 ч 1 мин"),
])
def test_format_duration(seconds, expected):
    assert TimeFormatter.format_duration(seconds) == expected

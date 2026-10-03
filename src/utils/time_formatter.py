"""
Модуль форматирования времени
"""


class TimeFormatter:
    """Класс для форматирования времени в различные форматы"""

    @staticmethod
    def format_timestamp(seconds: float) -> str:
        """
        Преобразует секунды в формат HH:MM:SS или MM:SS

        Args:
            seconds: время в секундах

        Returns:
            str: форматированный таймкод
        """
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)

        if h > 0:
            return f"{int(h):02d}:{int(m):02d}:{int(s):02d}"
        return f"{int(m):02d}:{int(s):02d}"

    @staticmethod
    def format_clock(seconds: float) -> str:
        """Длительность записи для журнала: «M:SS» или «неизвестна» (0 — ffprobe не смог)."""
        if not seconds or seconds <= 0:
            return "неизвестна"
        return f"{int(seconds // 60)}:{int(seconds % 60):02d}"

    @staticmethod
    def format_duration(seconds: float) -> str:
        """
        Форматирует длительность в читаемый вид

        Args:
            seconds: длительность в секундах

        Returns:
            str: читаемая длительность (например "2 мин 30 сек")
        """
        if seconds < 60:
            return f"{int(seconds)} сек"
        elif seconds < 3600:
            m = int(seconds / 60)
            s = int(seconds % 60)
            return f"{m} мин {s} сек"
        else:
            h = int(seconds / 3600)
            m = int((seconds % 3600) / 60)
            return f"{h} ч {m} мин"

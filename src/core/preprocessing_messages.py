"""Русские строки журнала об очистке звука.

Журнал читают люди без технического бэкграунда: отчёт предобработки хранит
английские машинные формулировки (их проверяют тесты и API), а в журнал они
попадают уже по-русски. Неизвестная причина выводится как есть.
"""

from __future__ import annotations

from collections.abc import Callable

PREPROCESSING_ACTIONS_RU = {
    "none": "не требуется",
    "light_cleanup": "лёгкая очистка от шума",
    "neural_denoise": "нейросетевое подавление шума",
    "normalize": "выравнивание громкости",
}
PREPROCESSING_REASONS_RU = {
    "Reduced estimated signal-to-noise ratio": "речь слабо выделяется на фоне шума",
    "Broadband stationary noise signature": "в записи постоянный фоновый шум",
    "Low-frequency rumble signature": "в записи низкочастотный гул",
    "Severe broadband noise detected": "в записи сильный фоновый шум",
    "Neural denoiser unavailable; using conservative FFmpeg cleanup": "нейросетевая очистка недоступна, применена мягкая очистка FFmpeg",
    "Severe clipping detected; denoising cannot restore clipped speech safely": "запись сильно перегружена (клиппинг), очистка не поможет",
    "Almost no analyzable speech signal; processing refused": "в записи почти нет речи, очистка не выполнялась",
    "Signal is quiet but estimated SNR is uncertain; amplification refused": "запись тихая, но усиливать её небезопасно",
    "Speech level is low while signal-to-noise ratio is healthy": "речь тихая, но чистая — громкость выровнена",
    "Input appears clean; enhancement is unnecessary": "запись чистая, очистка не нужна",
    "Audio preprocessing is disabled": "очистка звука отключена в настройках",
    "Light cleanup explicitly requested": "лёгкая очистка выбрана в настройках",
    "Neural denoising explicitly requested": "нейросетевая очистка выбрана в настройках",
    "Selected preprocessing backend failed safely": "средство очистки завершилось с ошибкой, использована исходная запись",
}
_QUALITY_GATE_PREFIX = "Quality gate rejected candidate: "
_QUALITY_GATE_RU = {
    "clipping increased": "после очистки появились перегрузки",
    "too much speech became silence": "очистка заглушила часть речи",
    "useful signal level collapsed": "после очистки речь стала слишком тихой",
    "loudness moved away from target": "громкость ушла от нужного уровня",
    "no measurable cleanup benefit": "очистка не дала заметного улучшения",
}


def preprocessing_reason_ru(reason: str) -> str:
    if reason in PREPROCESSING_REASONS_RU:
        return PREPROCESSING_REASONS_RU[reason]
    if reason.startswith(_QUALITY_GATE_PREFIX):
        detail = reason[len(_QUALITY_GATE_PREFIX):]
        return "результат очистки отклонён: " + _QUALITY_GATE_RU.get(detail, detail)
    if reason.startswith("Quality gate could not validate candidate: "):
        return "не удалось проверить результат очистки: " + reason.split(": ", 1)[1]
    return reason


def log_preprocessing_report(logger: Callable[[str], None], report) -> None:
    """Записать в журнал решение об очистке звука и его причины."""
    decision = report.decision
    action_ru = PREPROCESSING_ACTIONS_RU.get(decision.action, decision.action)
    if report.applied:
        logger(f"Очистка звука: {action_ru} (режим {report.mode})")
    else:
        logger(f"Очистка звука не выполнялась: {action_ru} (режим {report.mode})")
    for reason in decision.reasons:
        logger(f"  Почему: {preprocessing_reason_ru(reason)}")
    if report.runtime_fallback:
        detail = report.fallback_reason
        logger(
            "  Очистка не применена: "
            + (preprocessing_reason_ru(detail) if detail else "использована исходная запись")
        )

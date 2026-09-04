"""Перечисление PipeWire/PulseAudio мониторов мимо PortAudio (issue #49).

PortAudio ходит в ALSA напрямую и видит только физические устройства и агрегаты
``pulse``/``default``. Мониторы выходных каналов (``*.sink.monitor``) —
виртуальные источники звукового сервера, и в перечислении PortAudio их нет в
принципе, из-за чего список SYSTEM-устройств оставался пустым даже там, где
``pactl list sources short`` показывает шесть мониторов.

Модуль спрашивает сам звуковой сервер через ``pactl`` и умеет перецепить уже
открытый поток на выбранный монитор — это штатный ``move-source-output``, а не
подмена умолчаний в системе, поэтому чужие записи он не трогает.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from time import monotonic, sleep
from typing import Any

#: Префикс идентификатора устройства, означающий «источник звукового сервера, а
#: не индекс в перечислении PortAudio».
PULSE_DEVICE_PREFIX = "pulse:"

#: pactl отвечает мгновенно; если сервер не отвечает — молча идём дальше по
#: PortAudio-пути, а не вешаем старт сессии.
_PACTL_TIMEOUT = 4.0

_SAMPLE_SPEC = re.compile(r"(?P<channels>\d+)ch\s+(?P<rate>\d+)Hz")
_SOURCE_OUTPUT_HEADER = re.compile(r"^Source Output #(?P<index>\d+)")
_PROCESS_ID = re.compile(r"application\.process\.id\s*=\s*\"(?P<pid>\d+)\"")


@dataclass(frozen=True)
class PulseMonitor:
    """Монитор выходного канала, каким его видит звуковой сервер."""

    name: str
    sample_rate: int
    channels: int


def _pactl(*args: str) -> str | None:
    """Прогнать pactl и вернуть stdout; None — если сервера или утилиты нет.

    ``errors="replace"`` здесь не косметика: имена устройств бывают
    неаскишными, а строгое декодирование пайпа уже стоило проекту зависания
    ffmpeg в issue #20.
    """
    try:
        completed = subprocess.run(
            ["pactl", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PACTL_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def monitors() -> list[PulseMonitor]:
    """Мониторы выходных каналов по данным звукового сервера."""
    listing = _pactl("list", "sources", "short")
    if not listing:
        return []
    found: list[PulseMonitor] = []
    for line in listing.splitlines():
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        name = fields[1].strip()
        if not name.casefold().endswith(".monitor"):
            continue
        spec = _SAMPLE_SPEC.search(fields[3]) if len(fields) > 3 else None
        found.append(
            PulseMonitor(
                name=name,
                sample_rate=int(spec.group("rate")) if spec else 48_000,
                channels=int(spec.group("channels")) if spec else 2,
            )
        )
    return found


def default_monitor_name() -> str | None:
    """Монитор текущего выходного канала по умолчанию."""
    sink = _pactl("get-default-sink")
    sink = sink.strip() if sink else ""
    return f"{sink}.monitor" if sink else None


def monitor_devices() -> list[dict[str, Any]]:
    """Мониторы в том же виде, в каком устройства отдаёт SoundDeviceCapture."""
    default_name = default_monitor_name()
    return [
        {
            "id": f"{PULSE_DEVICE_PREFIX}{monitor.name}",
            "name": monitor.name,
            "sample_rate": monitor.sample_rate,
            "channels": monitor.channels,
            "is_default": monitor.name == default_name,
        }
        for monitor in monitors()
    ]


def source_name(device_id: str) -> str:
    """Имя источника сервера из идентификатора устройства."""
    return device_id[len(PULSE_DEVICE_PREFIX) :]


def source_outputs(pid: int) -> frozenset[str]:
    """Индексы записей, открытых процессом ``pid``."""
    listing = _pactl("list", "source-outputs")
    if not listing:
        return frozenset()
    index: str | None = None
    found: set[str] = set()
    for line in listing.splitlines():
        header = _SOURCE_OUTPUT_HEADER.match(line.strip())
        if header:
            index = header.group("index")
            continue
        owner = _PROCESS_ID.search(line)
        if owner and index is not None and int(owner.group("pid")) == pid:
            found.add(index)
    return frozenset(found)


def attach_to_monitor(name: str, pid: int, timeout: float = 2.0, known: frozenset[str] = frozenset()) -> bool:
    """Перецепить наш только что открытый поток на монитор ``name``.

    Поток появляется у сервера асинхронно, поэтому запись ищется с повтором;
    ``known`` — записи того же процесса, существовавшие до открытия (микрофон
    идёт через тот же сервер), их трогать нельзя.

    Возвращает False, если перецепить не удалось: продолжать нельзя — иначе
    захват молча пишет источник по умолчанию, то есть обычно микрофон.
    """
    deadline = monotonic() + timeout
    while True:
        new = sorted(source_outputs(pid) - known)
        if new and _pactl("move-source-output", new[-1], name) is not None:
            return True
        if monotonic() >= deadline:
            return False
        sleep(0.05)

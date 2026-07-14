"""
Прямая загрузка колёс (wheels) PyTorch без участия pip.

Зачем: pip внутри PyInstaller-сборки ненадёжен (падает на vendored distlib:
``Unable to locate finder for 'pip._vendor.distlib'``). Колесо — это обычный
zip-архив, поэтому torch/torchaudio/torchvision можно скачать и распаковать
напрямую. Их python-зависимости (numpy, sympy, filelock, fsspec, typing_extensions
и т.д.) уже вшиты в .exe, отдельно ставить не нужно.

На Windows и macOS колёса torch самодостаточны (CUDA-библиотеки лежат внутри
самого колеса). На Linux с CUDA дополнительно докачиваются nvidia-* колёса,
перечисленные в метаданных torch.
"""

from __future__ import annotations

import hashlib
import re
import ssl
import sys
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import unquote, urljoin


class DownloadCancelled(RuntimeError):
    """Загрузка отменена пользователем."""


# Пакеты сборки torch, которые качаем всегда. Версии передаются единым стеком:
# бинарные расширения torchaudio/torchvision несовместимы с другим torch-релизом.
CORE_PACKAGES = ("torch", "torchaudio", "torchvision")

_HREF_RE = re.compile(r'href="([^"]+?\.whl)(#sha256=([0-9a-fA-F]+))?"', re.IGNORECASE)


def _py_tag() -> str:
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def _platform_ok(filename: str) -> bool:
    """Подходит ли колесо под текущую ОС/архитектуру."""
    fn = filename.lower()
    if sys.platform == "win32":
        return "win_amd64" in fn
    if sys.platform == "darwin":
        return "macosx" in fn and ("arm64" in fn or "universal2" in fn)
    # linux
    return "linux" in fn and "x86_64" in fn and "aarch64" not in fn


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_version(filename: str) -> str:
    """Извлекает версию из имени колеса: 'torch-2.9.1+cu128-cp311-...' -> '2.9.1+cu128'.

    Формат wheel: {name}-{version}-{py}-{abi}-{plat}.whl; имя нормализовано
    (дефисы заменены на подчёркивания), поэтому второй элемент split("-") — всегда версия.
    """
    return filename[:-4].split("-")[1]


def _version_key(ver: str):
    """Грубое сравнение версий без сторонних зависимостей: (2,9,1) из '2.9.1+cu128'."""
    base = ver.split("+")[0]
    parts = []
    for chunk in base.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _raise_if_cancelled(cancel_event=None) -> None:
    if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
        raise DownloadCancelled("Загрузка отменена пользователем.")


def _ssl_context() -> ssl.SSLContext:
    """Use certifi's CA bundle in frozen apps where OS certificates are absent."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except (ImportError, OSError):
        return ssl.create_default_context()


def _fetch(url: str, timeout: int = 60, cancel_event=None) -> bytes:
    _raise_if_cancelled(cancel_event)
    req = urllib.request.Request(url, headers={"User-Agent": "GigaAMGUI-downloader"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
        data = r.read()
    _raise_if_cancelled(cancel_event)
    return data


def _index_url(base: str, package: str) -> str:
    return base.rstrip("/") + "/" + _normalize(package) + "/"


def find_wheel(
    base: str, package: str, version: str | None = None, py_specific: bool = True, cancel_event=None
) -> tuple[str, str | None, str]:
    """
    Находит URL лучшего колеса пакета на индексе.

    py_specific=True  — колесо под текущую версию Python (torch/audio/vision);
    py_specific=False — универсальное py3-none (nvidia-* пакеты).

    Возвращает (url, sha256|None, version). Бросает RuntimeError, если не найдено.
    """
    page_url = _index_url(base, package)
    html = _fetch(page_url, cancel_event=cancel_event).decode("utf-8", "replace")
    py_tag = _py_tag()

    best = None  # (version_key, url, sha256, version)
    for m in _HREF_RE.finditer(html):
        href, _, sha = m.group(1), m.group(2), m.group(3)
        filename = unquote(href.split("/")[-1].split("#")[0])

        if py_specific:
            if f"-{py_tag}-" not in filename:
                continue
        else:
            if "py3-none" not in filename and "-none-any" not in filename:
                continue

        if not (_platform_ok(filename) or "none-any" in filename):
            continue

        ver = _parse_version(filename)
        if version is not None and ver.split("+")[0] != version.split("+")[0]:
            continue

        url = urljoin(page_url, href.split("#")[0])
        key = _version_key(ver)
        if best is None or key > best[0]:
            best = (key, url, sha, ver)

    if best is None:
        raise RuntimeError(f"Не найдено подходящее колесо {package} ({py_tag}, {sys.platform}) на {page_url}")
    return best[1], best[2], best[3]


def _download_and_extract(
    url: str, sha256: str | None, target: Path, log_cb=None, name: str = "", cancel_event=None
) -> None:
    """Скачивает колесо (с проверкой sha256) и распаковывает в target."""

    def _log(msg):
        if log_cb:
            log_cb(msg)

    req = urllib.request.Request(url, headers={"User-Agent": "GigaAMGUI-downloader"})
    tmp = target / ("_dl_" + url.split("/")[-1].split("#")[0])
    target.mkdir(parents=True, exist_ok=True)

    _raise_if_cancelled(cancel_event)
    with urllib.request.urlopen(req, timeout=120, context=_ssl_context()) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        total_mb = total / 1024 / 1024
        _log(f"Скачивание {name or url.split('/')[-1]} ({total_mb:.1f} МБ)…" if total else f"Скачивание {name}…")
        hasher = hashlib.sha256()
        done = 0
        last_pct = -5
        with open(tmp, "wb") as f:
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                _raise_if_cancelled(cancel_event)
                f.write(chunk)
                hasher.update(chunk)
                done += len(chunk)
                if total:
                    pct = int(done * 100 / total)
                    if pct >= last_pct + 5:
                        last_pct = pct
                        _log(f"  {name}: {pct}%  ({done / 1024 / 1024:.0f}/{total_mb:.0f} МБ)")

    if sha256 and hasher.hexdigest().lower() != sha256.lower():
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Контрольная сумма {name} не совпала — файл повреждён.")

    _raise_if_cancelled(cancel_event)
    _log(f"  Распаковка {name}…")
    with zipfile.ZipFile(tmp) as z:
        z.extractall(target)
    tmp.unlink(missing_ok=True)


def _nvidia_requirements(target: Path) -> list[tuple[str, str]]:
    """Читает из метаданных torch список nvidia-* зависимостей (для Linux+CUDA)."""
    result = []
    for meta in target.glob("torch-*.dist-info/METADATA"):
        text = meta.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            if not line.startswith("Requires-Dist:"):
                continue
            body = line[len("Requires-Dist:") :].strip()
            if "nvidia" not in body.lower():
                continue
            # Пример: nvidia-cudnn-cu12==9.1.0.70; platform_system == "Linux" ...
            m = re.match(r"([A-Za-z0-9_.\-]+)\s*==\s*([0-9][0-9A-Za-z.\-]*)", body)
            if m:
                result.append((m.group(1), m.group(2)))
        break
    return result


def install(
    base_index: str, target: Path, versions: dict[str, str], need_nvidia: bool = False, log_cb=None, cancel_event=None
) -> None:
    """
    Скачивает согласованный стек torch/torchaudio/torchvision (+ nvidia-* на
    Linux+CUDA) в target. Бросает исключение при ошибке.
    """
    target = Path(target)

    missing = [package for package in CORE_PACKAGES if package not in versions]
    if missing:
        raise ValueError(f"Не заданы версии runtime-пакетов: {', '.join(missing)}")

    for pkg in CORE_PACKAGES:
        _raise_if_cancelled(cancel_event)
        url, sha, ver = find_wheel(
            base_index,
            pkg,
            version=versions[pkg],
            cancel_event=cancel_event,
        )
        if log_cb:
            log_cb(f"{pkg} {ver}")
        _download_and_extract(url, sha, target, log_cb=log_cb, name=f"{pkg} {ver}", cancel_event=cancel_event)

    if need_nvidia:
        deps = _nvidia_requirements(target)
        if log_cb:
            log_cb(f"Дополнительно для Linux+CUDA: {len(deps)} nvidia-пакетов…")
        for name, ver in deps:
            _raise_if_cancelled(cancel_event)
            try:
                url, sha, got = find_wheel(base_index, name, version=ver, py_specific=False, cancel_event=cancel_event)
            except RuntimeError:
                # Некоторые nvidia-пакеты лежат на обычном PyPI-индексе pytorch — пробуем без версии.
                url, sha, got = find_wheel(base_index, name, py_specific=False, cancel_event=cancel_event)
            _download_and_extract(url, sha, target, log_cb=log_cb, name=f"{name} {got}", cancel_event=cancel_event)

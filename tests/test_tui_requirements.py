"""TUI не должен предлагать бэкенд, которого нет в зависимостях его воркера."""

import ast
import re
import sys
from pathlib import Path

TUI_SOURCE_DIR = Path("tui/src")
TUI_REQUIREMENTS = Path("requirements-tui.txt")
MACOS_MLX_REQUIREMENTS = Path("requirements-macos-mlx.txt")
INSTALL_TUI_SCRIPT = Path("scripts/install_tui.sh")


def _tui_source() -> str:
    """Весь Rust-исходник TUI: после разбиения на модули команды живут не в main.rs."""
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(TUI_SOURCE_DIR.rglob("*.rs"))
    )


def _requirement_names() -> set[str]:
    names = set()
    for line in TUI_REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("--"):
            continue
        names.add(re.split(r"[<>=!\[]", line, maxsplit=1)[0].strip().lower())
    return names


def test_tui_offers_onnx_and_the_worker_can_run_it():
    """Команды /backend onnx и /onnx-provider есть — значит нужен onnx-asr."""
    source = _tui_source()
    assert '"onnx"' in source
    assert "/onnx-provider" in source

    names = _requirement_names()
    assert "onnx-asr" in names
    assert "onnxruntime" in names


def test_worker_requirements_cover_onnx_diarization():
    """ONNX-диаризация ресемплит через soxr — без него падает на не-16 кГц."""
    assert "soxr" in _requirement_names()


def test_tui_offers_mlx_and_the_worker_can_run_it():
    """Команда /backend mlx есть в tui/src — значит installer должен ставить mlx/gigaam-mlx."""
    source = _tui_source()
    assert '"mlx"' in source

    mlx_text = MACOS_MLX_REQUIREMENTS.read_text(encoding="utf-8")
    mlx_names = set()
    for line in mlx_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("--"):
            continue
        mlx_names.add(re.split(r"[<>=!\[@ ]", line, maxsplit=1)[0].strip().lower())
    assert "mlx" in mlx_names
    assert "gigaam-mlx" in mlx_names

    installer_text = INSTALL_TUI_SCRIPT.read_text(encoding="utf-8")
    assert "requirements-macos-mlx.txt" in installer_text


def test_pytorch_line_stays_bounded():
    """torchaudio 2.9+ снёс legacy backend API, который импортирует pyannote 3.1.1."""
    text = TUI_REQUIREMENTS.read_text(encoding="utf-8")

    for package in ("torch", "torchaudio"):
        assert f"{package}>=2.6.0,<2.9.0" in text


# Имя модуля при импорте → имя дистрибутива в requirements, где они различаются.
_DISTRIBUTION_FOR_MODULE = {
    "dotenv": "python-dotenv",
    "yt_dlp": "yt-dlp",
    "typing_extensions": "typing-extensions",
}


def _top_level_imports(path: Path) -> list[str]:
    """Импорты уровня модуля, включая `try:`/`if` на верхнем уровне — они
    выполняются при импорте и роняют процесс, если пакета нет."""
    names = []
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        block = node.body if isinstance(node, (ast.Try, ast.If)) else [node]
        for statement in block:
            if isinstance(statement, ast.Import):
                names.extend(alias.name for alias in statement.names)
            elif isinstance(statement, ast.ImportFrom) and statement.level == 0 and statement.module:
                names.append(statement.module)
                names.extend(f"{statement.module}.{alias.name}" for alias in statement.names)
    return names


def _third_party_imports(*entry_points: str) -> dict[str, str]:
    """Сторонние пакеты, которые импортирует граф `src.*` от точек входа."""
    seen: set[Path] = set()
    found: dict[str, str] = {}

    def module_file(name: str) -> Path | None:
        base = Path(*name.split("."))
        for candidate in (base.with_suffix(".py"), base / "__init__.py"):
            if candidate.exists():
                return candidate
        return None

    def visit(path: Path) -> None:
        if path in seen:
            return
        seen.add(path)
        for name in _top_level_imports(path):
            top = name.split(".")[0]
            if top == "src":
                target = module_file(name)
                if target is not None:
                    visit(target)
            elif top != "__future__" and top not in sys.stdlib_module_names:
                found.setdefault(top, str(path))

    for entry in entry_points:
        visit(Path(entry))
    return found


def test_worker_and_mcp_imports_are_direct_requirements():
    """Каждый пакет, который воркер TUI и `gigaam mcp` импортируют при старте,
    объявлен в requirements-tui.txt напрямую. Транзитивная зависимость — не
    гарантия: psutil приходил в dev-окружение через accelerate, а в venv TUI
    его не было, и после 3b727e9 `gigaam --update` падал на импорте llm_service.
    """
    def normalized(name: str) -> str:  # PEP 503: typing_extensions == typing-extensions
        return re.sub(r"[-_.]+", "-", name).lower()

    declared = {normalized(name) for name in _requirement_names()}
    missing = {
        _DISTRIBUTION_FOR_MODULE.get(module, module): importer
        for module, importer in _third_party_imports("src/tui_worker.py", "src/mcp_server.py").items()
        if normalized(_DISTRIBUTION_FOR_MODULE.get(module, module)) not in declared
    }
    assert not missing, f"не объявлены в {TUI_REQUIREMENTS}: {missing}"

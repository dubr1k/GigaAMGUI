"""Диагностика доступа HF-токена к gated-репозиториям pyannote."""

from __future__ import annotations

# Репозитории, нужные пайплайну pyannote/speaker-diarization-3.1.
_DIARIZATION_REQUIRED_REPOS = [
    "pyannote/speaker-diarization-3.1",
    "pyannote/segmentation-3.0",
    "pyannote/wespeaker-voxceleb-resnet34-LM",
]


def diagnose_hf_access(token: str | None) -> str:
    """Дополняет ошибку pyannote проверкой доступа к нужным репозиториям.

    Пробует каждый нужный репозиторий через HfApi и возвращает человекочитаемый
    отчёт: валиден ли токен и к какому именно репозиторию нет доступа и почему
    (не приняты условия / fine-grained токен без доступа к gated-репам / 401 / сеть).
    Никогда не бросает исключение — только возвращает строку.
    """
    if not token:
        return "HF-токен не задан. Укажите read-токен: https://huggingface.co/settings/tokens"

    try:
        from huggingface_hub import HfApi
        from huggingface_hub.utils import (
            GatedRepoError,
            HfHubHTTPError,
            RepositoryNotFoundError,
        )
    except Exception as e:  # noqa: BLE001
        return f"Не удалось выполнить диагностику доступа (huggingface_hub): {e}"

    api = HfApi()
    lines: list[str] = []

    # 1) Валиден ли сам токен.
    try:
        who = api.whoami(token=token)
        name = who.get("name") if isinstance(who, dict) else None
        lines.append(f"Токен валиден (пользователь: {name or '?'}).")
    except Exception as e:  # noqa: BLE001
        code = getattr(getattr(e, "response", None), "status_code", None)
        details = f"{type(e).__name__}: {e}"
        if code is not None:
            details = f"HTTP {code}; {details}"
        return (
            f"Токен НЕвалиден или не даёт доступа (whoami: {details}). "
            "Создайте новый read-токен: https://huggingface.co/settings/tokens"
        )

    # 2) Доступ к каждому нужному репозиторию.
    for repo in _DIARIZATION_REQUIRED_REPOS:
        try:
            api.model_info(repo, token=token)
            lines.append(f"  OK  {repo}")
        except GatedRepoError:
            lines.append(
                f"  НЕТ {repo}: не приняты условия — откройте "
                f"https://huggingface.co/{repo} и нажмите 'Agree and access repository'."
            )
        except RepositoryNotFoundError:
            lines.append(
                f"  НЕТ {repo}: репозиторий не виден токену. Если токен fine-grained, "
                "включите 'Read access to contents of all public gated repos you can access'."
            )
        except HfHubHTTPError as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code == 403:
                lines.append(
                    f"  НЕТ {repo}: 403 — примите условия и/или дайте токену доступ к "
                    "gated-репозиториям (fine-grained токен: 'Read access to public gated repos')."
                )
            elif code == 401:
                lines.append(f"  НЕТ {repo}: 401 — токен недействителен.")
            else:
                lines.append(f"  НЕТ {repo}: HTTP {code}: {e}")
        except Exception as e:  # noqa: BLE001
            lines.append(f"  НЕТ {repo}: {type(e).__name__}: {e}")

    return "\n".join(lines)

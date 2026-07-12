import sys
import types

from src.utils import diarization


# Определены на уровне модуля, а не внутри _install_fake_hf: повторные вызовы
# _install_fake_hf в одном тесте (переустановка sys.modules) должны давать ТЕ ЖЕ
# классы исключений, иначе `except GatedRepoError:` в diagnose_hf_access не
# поймает исключение, поднятое до переустановки (разные объекты класса с
# одинаковым именем не проходят isinstance).
class _FakeGatedRepoError(Exception):
    pass


class _FakeRepositoryNotFoundError(Exception):
    pass


class _FakeHfHubHTTPError(Exception):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.response = types.SimpleNamespace(status_code=status)


def _install_fake_hf(monkeypatch, *, whoami=None, whoami_raises=None, info_side=None):
    """Ставит фейковый huggingface_hub с HfApi и utils-исключениями."""
    hub = types.ModuleType("huggingface_hub")
    utils = types.ModuleType("huggingface_hub.utils")

    utils.GatedRepoError = _FakeGatedRepoError
    utils.RepositoryNotFoundError = _FakeRepositoryNotFoundError
    utils.HfHubHTTPError = _FakeHfHubHTTPError

    class HfApi:
        def whoami(self, token=None):
            if whoami_raises:
                raise whoami_raises
            return whoami or {"name": "tester"}

        def model_info(self, repo, token=None):
            if info_side:
                info_side(repo)

    hub.HfApi = HfApi
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.setitem(sys.modules, "huggingface_hub.utils", utils)
    return utils


def test_diagnose_reports_invalid_token(monkeypatch):
    _install_fake_hf(monkeypatch, whoami_raises=RuntimeError("401"))
    report = diarization.diagnose_hf_access("tok")
    assert "НЕвалиден" in report or "невалиден" in report.lower()


def test_diagnose_reports_gated_repo(monkeypatch):
    utils = _install_fake_hf(monkeypatch)

    def side(repo):
        if repo == "pyannote/segmentation-3.0":
            raise utils.GatedRepoError("gated")

    # переустановим model_info через side
    _install_fake_hf(monkeypatch, info_side=side)
    report = diarization.diagnose_hf_access("tok")
    assert "pyannote/segmentation-3.0" in report
    assert "Agree" in report or "условия" in report


def test_diagnose_no_token(monkeypatch):
    assert "не задан" in diarization.diagnose_hf_access(None).lower() or \
           "токен" in diarization.diagnose_hf_access(None).lower()


def test_diagnose_all_ok(monkeypatch):
    _install_fake_hf(monkeypatch)  # model_info never raises → all OK
    report = diarization.diagnose_hf_access("tok")
    assert report.count("OK") >= 3

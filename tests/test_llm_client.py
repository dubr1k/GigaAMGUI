from src.utils.llm_client import LLMClient, LLMSettings


class _Response:
    status_code = 200
    headers = {}

    def raise_for_status(self):
        pass

    def iter_lines(self, decode_unicode=True):
        return iter([
            'data: {"choices":[{"delta":{"content":"при"}}]}',
            'data: {"choices":[{"delta":{"content":"вет"}}]}',
            "data: [DONE]",
        ])


def test_openai_streams_text_chunks(monkeypatch):
    captured = {}

    def fake_post(*args, **kwargs):
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr("src.utils.llm_client.requests.post", fake_post)
    client = LLMClient(LLMSettings("https://example.test/v1", "key", "model"))
    chunks = []

    result = client.process_transcript("текст", "промпт", stream_callback=chunks.append)

    assert result == "привет"
    assert chunks == ["при", "вет"]
    assert captured["json"]["stream"] is True
    assert captured["stream"] is True


def test_google_openai_compat_endpoint_does_not_duplicate_v1():
    endpoint = LLMClient._build_openai_endpoint(
        "https://generativelanguage.googleapis.com/v1beta/openai/"
    )

    assert endpoint == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"


def test_retries_transient_service_unavailable(monkeypatch):
    class RetryResponse:
        status_code = 503
        headers = {}

        def close(self):
            pass

        def raise_for_status(self):
            raise RuntimeError("503")

    class SuccessResponse:
        status_code = 200

    responses = iter([RetryResponse(), SuccessResponse()])
    monkeypatch.setattr("src.utils.llm_client.requests.post", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr("src.utils.llm_client.time.sleep", lambda _: None)
    client = LLMClient(LLMSettings("https://example.test/v1", "key", "model"))

    response = client._post_with_retry("https://example.test/v1/chat/completions", headers={}, payload={})

    assert response.status_code == 200


def _retry_response(status=429, headers=None):
    class R:
        status_code = status

        def close(self):
            pass

    R.headers = headers or {}
    return R()


def _ok_response():
    class R:
        status_code = 200

    return R()


def _run_retry(monkeypatch, responses, cancel_check=None):
    sleeps = []
    responses = iter(responses)
    monkeypatch.setattr("src.utils.llm_client.requests.post", lambda *a, **k: next(responses))
    monkeypatch.setattr("src.utils.llm_client.time.sleep", sleeps.append)
    client = LLMClient(LLMSettings("https://example.test/v1", "key", "model"))
    result = client._post_with_retry(
        "https://example.test/v1/chat/completions", headers={}, payload={}, cancel_check=cancel_check,
    )
    return result, sleeps


def test_retry_honours_numeric_retry_after_capped_at_15s(monkeypatch):
    _, sleeps = _run_retry(monkeypatch, [_retry_response(headers={"Retry-After": "40"}), _ok_response()])
    assert sum(sleeps) == 15.0


def test_retry_falls_back_to_backoff_on_garbage_retry_after(monkeypatch):
    _, sleeps = _run_retry(monkeypatch, [
        _retry_response(headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}),
        _retry_response(),
        _ok_response(),
    ])
    assert sum(sleeps[:len(sleeps)]) == 1 + 2  # 2**0, 2**1


def test_retry_gives_up_after_three_attempts(monkeypatch):
    result, _ = _run_retry(monkeypatch, [_retry_response(503)] * 3)
    assert result.status_code == 503


def test_retry_wait_is_interrupted_by_cancel(monkeypatch):
    import pytest

    calls = {"n": 0}

    def cancel_check():
        calls["n"] += 1
        return calls["n"] > 2  # отмена приходит во время ожидания

    with pytest.raises(RuntimeError, match="cancelled"):
        _run_retry(monkeypatch, [_retry_response(headers={"Retry-After": "10"}), _ok_response()], cancel_check)

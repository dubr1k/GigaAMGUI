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

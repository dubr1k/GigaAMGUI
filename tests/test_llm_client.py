from src.utils.llm_client import LLMClient, LLMSettings


class _Response:
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

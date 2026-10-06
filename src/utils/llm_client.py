"""
Клиент для работы с LLM через OpenAI-совместимый или Anthropic Messages API.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass

import requests


@dataclass
class LLMSettings:
    """Настройки подключения к LLM API."""

    api_url: str
    api_key: str
    model: str
    temperature: float = 0.2
    timeout: int = 180


class LLMClient:
    """Минимальный клиент для OpenAI-совместимого или Anthropic API."""

    def __init__(self, settings: LLMSettings):
        self.settings = settings

    @staticmethod
    def _normalize_url(api_url: str) -> str:
        url = (api_url or "").strip().rstrip("/")
        if not url:
            raise ValueError("Не указан URL API")
        return url

    @classmethod
    def _detect_api_kind(cls, api_url: str) -> str:
        url = cls._normalize_url(api_url).lower()
        if "anthropic.com" in url or url.endswith("/v1/messages") or "/v1/messages" in url:
            return "anthropic"
        return "openai"

    @classmethod
    def _build_openai_endpoint(cls, api_url: str) -> str:
        url = cls._normalize_url(api_url)
        if url.endswith("/chat/completions"):
            return url
        # Google Gemini's OpenAI-compatible base URL is /v1beta/openai/;
        # unlike ordinary providers it already contains the API version.
        if url.endswith("/openai"):
            return f"{url}/chat/completions"
        if url.endswith("/v1"):
            return f"{url}/chat/completions"
        if "/v1/" in url:
            return url
        return f"{url}/v1/chat/completions"

    @classmethod
    def _build_anthropic_endpoint(cls, api_url: str) -> str:
        url = cls._normalize_url(api_url)
        if url.endswith("/messages"):
            return url
        if url.endswith("/v1"):
            return f"{url}/messages"
        if "/v1/" in url:
            return url
        return f"{url}/v1/messages"

    @staticmethod
    def _sleep_with_cancel(delay: float, cancel_check: Callable[[], bool] | None) -> None:
        """Ждать `delay` секунд, но прерваться, если пользователь отменил запрос."""
        if cancel_check is None:
            time.sleep(delay)
            return
        deadline = time.monotonic() + delay
        while True:
            if cancel_check():
                raise RuntimeError("LLM request cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.1, remaining))

    def _post_with_retry(
        self,
        endpoint: str,
        *,
        headers: dict,
        payload: dict,
        stream: bool = False,
        cancel_check: Callable[[], bool] | None = None,
    ):
        last_response = None
        for attempt in range(3):
            response = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=self.settings.timeout,
                stream=stream,
            )
            if response.status_code not in (429, 500, 502, 503, 504) or attempt == 2:
                return response
            last_response = response
            retry_after = response.headers.get("Retry-After")
            try:
                delay = min(float(retry_after), 15.0) if retry_after else 2**attempt
            except ValueError:
                delay = 2**attempt
            response.close()
            self._sleep_with_cancel(delay, cancel_check)
        return last_response

    @staticmethod
    def _error_detail(payload) -> str | None:
        """Текст ошибки провайдера из JSON-тела или SSE-события."""
        if not isinstance(payload, dict):
            return None
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("type") or error.get("code")
            return str(message) if message else json.dumps(error, ensure_ascii=False)
        if isinstance(error, str) and error:
            return error
        if payload.get("type") == "error":
            return str(payload.get("message") or "ошибка без описания")
        return None

    @classmethod
    def _raise_for_status(cls, response) -> None:
        """raise_for_status с телом ответа: «404 Not Found» без него бесполезно."""
        status = getattr(response, "status_code", 200)
        if not isinstance(status, int) or status < 400:
            response.raise_for_status()
            return
        try:
            body = response.content.decode("utf-8", errors="replace")
        except Exception:
            body = ""
        detail = None
        try:
            detail = cls._error_detail(json.loads(body))
        except ValueError:
            pass
        detail = detail or body.strip()[:500]
        reason = getattr(response, "reason", "") or ""
        message = f"LLM API: HTTP {status} {reason}".rstrip()
        if detail:
            message += f": {detail}"
        raise requests.HTTPError(message, response=response)

    def _iter_sse_events(self, response, cancel_check):
        """JSON-события SSE-потока; ошибка в потоке становится исключением.

        SSE по спецификации — UTF-8, но без charset в Content-Type requests
        декодирует text/* как ISO-8859-1, и русский ответ превращался в мусор.
        Ошибки посреди потока (OpenAI-совместимые {"error": ...}, Anthropic
        event: error) раньше молча пропускались: оставался обрывок текста или
        «ответ без текста».
        """
        response.encoding = "utf-8"
        for line in response.iter_lines(decode_unicode=True):
            if cancel_check and cancel_check():
                response.close()
                raise RuntimeError("LLM request cancelled")
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                return
            try:
                event = json.loads(data)
            except ValueError:
                continue
            detail = self._error_detail(event)
            if detail:
                response.close()
                raise RuntimeError(f"LLM API вернул ошибку: {detail}")
            yield event

    def process_transcript(
        self,
        transcript_text: str,
        prompt: str,
        system_prompt: str | None = None,
        stream_callback: Callable[[str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> str:
        transcript = (transcript_text or "").strip()
        if not transcript:
            raise ValueError("Текст транскрипта пуст")

        user_prompt = (prompt or "").strip()
        if not user_prompt:
            raise ValueError("Промпт пуст")

        api_kind = self._detect_api_kind(self.settings.api_url)
        if api_kind == "anthropic":
            return self._process_anthropic(transcript, user_prompt, system_prompt, stream_callback, cancel_check)
        return self._process_openai(transcript, user_prompt, system_prompt, stream_callback, cancel_check)

    def _process_openai(
        self,
        transcript: str,
        user_prompt: str,
        system_prompt: str | None,
        stream_callback: Callable[[str], None] | None,
        cancel_check: Callable[[], bool] | None,
    ) -> str:
        endpoint = self._build_openai_endpoint(self.settings.api_url)
        headers = {
            "Authorization": f"Bearer {self.settings.api_key.strip()}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.settings.model.strip(),
            "temperature": self.settings.temperature,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt.strip() if system_prompt else "Ты помогаешь анализировать транскрипты голосовых сообщений на русском языке.",
                },
                {
                    "role": "user",
                    "content": (
                        f"Инструкция:\n{user_prompt}\n\n"
                        f"Транскрипт для обработки:\n{transcript}"
                    ),
                },
            ],
        }
        if stream_callback:
            payload["stream"] = True
            response = self._post_with_retry(endpoint, headers=headers, payload=payload, stream=True, cancel_check=cancel_check)
            self._raise_for_status(response)
            parts = []
            for chunk in self._iter_sse_events(response, cancel_check):
                delta = ((chunk.get("choices") or [{}])[0].get("delta") or {}).get("content", "")
                if delta:
                    parts.append(delta)
                    stream_callback(delta)
            return self._extract_text_content("".join(parts))

        response = self._post_with_retry(endpoint, headers=headers, payload=payload, cancel_check=cancel_check)
        self._raise_for_status(response)
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise ValueError("LLM API вернул пустой ответ")
        message = choices[0].get("message") or {}
        content = message.get("content", "")
        return self._extract_text_content(content)

    def _process_anthropic(
        self,
        transcript: str,
        user_prompt: str,
        system_prompt: str | None,
        stream_callback: Callable[[str], None] | None,
        cancel_check: Callable[[], bool] | None,
    ) -> str:
        endpoint = self._build_anthropic_endpoint(self.settings.api_url)
        headers = {
            "x-api-key": self.settings.api_key.strip(),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self.settings.model.strip(),
            "temperature": self.settings.temperature,
            "max_tokens": 4096,
            "system": system_prompt.strip() if system_prompt else "Ты помогаешь анализировать транскрипты голосовых сообщений на русском языке.",
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"Инструкция:\n{user_prompt}\n\n"
                        f"Транскрипт для обработки:\n{transcript}"
                    ),
                }
            ],
        }
        if stream_callback:
            payload["stream"] = True
            response = self._post_with_retry(endpoint, headers=headers, payload=payload, stream=True, cancel_check=cancel_check)
            self._raise_for_status(response)
            parts = []
            for event in self._iter_sse_events(response, cancel_check):
                delta = event.get("delta") or {}
                text = delta.get("text", "") if delta.get("type") == "text_delta" else ""
                if text:
                    parts.append(text)
                    stream_callback(text)
            return self._extract_text_content("".join(parts))

        response = self._post_with_retry(endpoint, headers=headers, payload=payload, cancel_check=cancel_check)
        self._raise_for_status(response)
        data = response.json()
        content = data.get("content", "")
        return self._extract_text_content(content)

    @staticmethod
    def _extract_text_content(content) -> str:
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif isinstance(item, str):
                    parts.append(item)
            content = "\n".join(part for part in parts if part)
        content = (content or "").strip()
        if not content:
            raise ValueError("LLM API вернул ответ без текста")
        return content

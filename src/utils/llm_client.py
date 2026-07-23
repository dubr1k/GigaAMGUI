"""
Клиент для работы с LLM через OpenAI-совместимый или Anthropic Messages API.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from collections.abc import Callable

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

    def process_transcript(
        self,
        transcript_text: str,
        prompt: str,
        system_prompt: str | None = None,
        stream_callback: Callable[[str], None] | None = None,
    ) -> str:
        transcript = (transcript_text or "").strip()
        if not transcript:
            raise ValueError("Текст транскрипта пуст")

        user_prompt = (prompt or "").strip()
        if not user_prompt:
            raise ValueError("Промпт пуст")

        api_kind = self._detect_api_kind(self.settings.api_url)
        if api_kind == "anthropic":
            return self._process_anthropic(transcript, user_prompt, system_prompt, stream_callback)
        return self._process_openai(transcript, user_prompt, system_prompt, stream_callback)

    def _process_openai(
        self,
        transcript: str,
        user_prompt: str,
        system_prompt: str | None,
        stream_callback: Callable[[str], None] | None,
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
            response = requests.post(
                endpoint, headers=headers, json=payload, timeout=self.settings.timeout, stream=True
            )
            response.raise_for_status()
            parts = []
            for line in response.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                delta = ((chunk.get("choices") or [{}])[0].get("delta") or {}).get("content", "")
                if delta:
                    parts.append(delta)
                    stream_callback(delta)
            return self._extract_text_content("".join(parts))

        response = requests.post(endpoint, headers=headers, json=payload, timeout=self.settings.timeout)
        response.raise_for_status()
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
            response = requests.post(
                endpoint, headers=headers, json=payload, timeout=self.settings.timeout, stream=True
            )
            response.raise_for_status()
            parts = []
            for line in response.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                delta = event.get("delta") or {}
                text = delta.get("text", "") if delta.get("type") == "text_delta" else ""
                if text:
                    parts.append(text)
                    stream_callback(text)
            return self._extract_text_content("".join(parts))

        response = requests.post(endpoint, headers=headers, json=payload, timeout=self.settings.timeout)
        response.raise_for_status()
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

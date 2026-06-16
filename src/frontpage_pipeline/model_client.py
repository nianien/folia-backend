from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


class ModelError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str
    api_key_env: str | None
    endpoint: str | None
    timeout_seconds: int
    temperature: float
    max_output_tokens: int


class ModelClient:
    def __init__(self, config: ModelConfig) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        return self.config.provider in {"openai", "claude", "gemini", "xinapi"}

    @property
    def model_name(self) -> str:
        if not self.enabled:
            return "heuristic-v1"
        return f"{self.config.provider}:{self.config.model}"

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if not self.enabled:
            raise ModelError("model provider is disabled")
        api_key = self._api_key()
        if self.config.provider == "openai":
            return self._openai(api_key, system_prompt, user_prompt)
        if self.config.provider == "claude":
            return self._claude(api_key, system_prompt, user_prompt)
        if self.config.provider == "gemini":
            return self._gemini(api_key, system_prompt, user_prompt)
        if self.config.provider == "xinapi":
            return self._xinapi(api_key, system_prompt, user_prompt)
        raise ModelError(f"unsupported model provider: {self.config.provider}")

    def _api_key(self) -> str:
        if not self.config.api_key_env:
            raise ModelError("api_key_env is not configured")
        value = os.environ.get(self.config.api_key_env)
        if not value:
            raise ModelError(f"missing API key environment variable: {self.config.api_key_env}")
        return value

    def _openai(self, api_key: str, system_prompt: str, user_prompt: str) -> str:
        endpoint = self.config.endpoint or "https://api.openai.com/v1/responses"
        payload = {
            "model": self.config.model,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.config.temperature,
            "max_output_tokens": self.config.max_output_tokens,
        }
        data = self._post_json(
            endpoint,
            payload,
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        if isinstance(data.get("output_text"), str):
            return data["output_text"]
        chunks: list[str] = []
        for item in data.get("output", []):
            for content in item.get("content", []):
                text = content.get("text")
                if text:
                    chunks.append(text)
        if chunks:
            return "\n".join(chunks)
        raise ModelError("OpenAI response did not contain text output")

    def _claude(self, api_key: str, system_prompt: str, user_prompt: str) -> str:
        endpoint = self.config.endpoint or "https://api.anthropic.com/v1/messages"
        payload = {
            "model": self.config.model,
            "max_tokens": self.config.max_output_tokens,
            "temperature": self.config.temperature,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        data = self._post_json(
            endpoint,
            payload,
            {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
        )
        chunks = [block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"]
        if chunks:
            return "\n".join(chunks)
        raise ModelError("Claude response did not contain text output")

    def _gemini(self, api_key: str, system_prompt: str, user_prompt: str) -> str:
        base = self.config.endpoint or "https://generativelanguage.googleapis.com/v1beta"
        model = urllib.parse.quote(self.config.model, safe="")
        endpoint = f"{base.rstrip('/')}/models/{model}:generateContent?key={urllib.parse.quote(api_key)}"
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": self.config.temperature,
                "maxOutputTokens": self.config.max_output_tokens,
            },
        }
        data = self._post_json(endpoint, payload, {"Content-Type": "application/json"})
        chunks: list[str] = []
        for candidate in data.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                text = part.get("text")
                if text:
                    chunks.append(text)
        if chunks:
            return "\n".join(chunks)
        raise ModelError("Gemini response did not contain text output")

    def _xinapi(self, api_key: str, system_prompt: str, user_prompt: str) -> str:
        endpoint = self.config.endpoint or "https://airouter.xincache.cn/v1/chat/completions"
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_output_tokens,
        }
        data = self._post_json(
            endpoint,
            payload,
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        choices = data.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            content = message.get("content")
            if isinstance(content, str) and content:
                return content
        raise ModelError("XinAPI response did not contain choices[0].message.content")

    def _post_json(self, endpoint: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ModelError(f"model API HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ModelError(f"model API request failed: {exc}") from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ModelError("model API returned invalid JSON") from exc


def create_model_client(settings: dict[str, Any]) -> ModelClient:
    raw = settings.get("model", {})
    provider = str(raw.get("provider", "heuristic")).lower()
    provider_config = raw.get(provider, {}) if isinstance(raw.get(provider, {}), dict) else {}
    return ModelClient(
        ModelConfig(
            provider=provider,
            model=str(provider_config.get("model", raw.get("model", "heuristic-v1"))),
            api_key_env=provider_config.get("api_key_env", raw.get("api_key_env")),
            endpoint=provider_config.get("endpoint", raw.get("endpoint")),
            timeout_seconds=int(raw.get("timeout_seconds", 60)),
            temperature=float(raw.get("temperature", 0.2)),
            max_output_tokens=int(raw.get("max_output_tokens", 3000)),
        )
    )

from __future__ import annotations

import unittest
from unittest.mock import patch

from folia.pipeline.model_client import ModelClient, ModelConfig, ModelError, create_model_client


def _client(provider: str = "", model: str = "", api_key: str = "", endpoint: str = "http://o") -> ModelClient:
    return ModelClient(
        ModelConfig(
            provider=provider, model=model, endpoint=endpoint, api_key=api_key,
            timeout_seconds=30, temperature=0.2, max_output_tokens=1000, num_ctx=8192,
        )
    )


class ModelClientTest(unittest.TestCase):
    def test_disabled_without_provider_or_model(self) -> None:
        self.assertFalse(_client("", "").enabled)
        self.assertFalse(_client("ollama", "").enabled)
        self.assertFalse(_client("", "gpt").enabled)
        self.assertEqual(_client("", "").model_name, "heuristic-v1")

    def test_ollama_chat(self) -> None:
        c = _client("ollama", "gemma3:4b")
        self.assertTrue(c.enabled)
        self.assertEqual(c.model_name, "ollama:gemma3:4b")
        captured = {}

        def fake_post(endpoint, payload, headers=None):
            captured["endpoint"] = endpoint
            captured["payload"] = payload
            return {"message": {"role": "assistant", "content": "ok"}}

        with patch.object(c, "_post", side_effect=fake_post):
            self.assertEqual(c.complete("sys", "usr"), "ok")
        self.assertEqual(captured["endpoint"], "http://o/api/chat")
        self.assertEqual(captured["payload"]["messages"][0], {"role": "system", "content": "sys"})

    def test_openai_compatible(self) -> None:
        c = _client("openai", "gpt-4.1-mini", api_key="sk", endpoint="https://api.openai.com/v1/chat/completions")
        captured = {}

        def fake_post(endpoint, payload, headers=None):
            captured["endpoint"] = endpoint
            captured["headers"] = headers
            return {"choices": [{"message": {"content": "hi"}}]}

        with patch.object(c, "_post", side_effect=fake_post):
            self.assertEqual(c.complete("sys", "usr"), "hi")
        self.assertEqual(captured["endpoint"], "https://api.openai.com/v1/chat/completions")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer sk")

    def test_remote_without_key_raises(self) -> None:
        c = _client("openai", "gpt-4.1-mini", api_key="")
        with self.assertRaises(ModelError):
            c.complete("sys", "usr")

    def test_claude(self) -> None:
        c = _client("claude", "claude-3-5-haiku-latest", api_key="ak", endpoint="https://api.anthropic.com/v1/messages")
        captured = {}

        def fake_post(endpoint, payload, headers=None):
            captured["headers"] = headers
            captured["payload"] = payload
            return {"content": [{"type": "text", "text": "yo"}]}

        with patch.object(c, "_post", side_effect=fake_post):
            self.assertEqual(c.complete("sys", "usr"), "yo")
        self.assertEqual(captured["headers"]["x-api-key"], "ak")
        self.assertEqual(captured["payload"]["system"], "sys")

    def test_gemini(self) -> None:
        c = _client("gemini", "gemini-1.5-flash", api_key="gk", endpoint="https://gen/v1beta")
        captured = {}

        def fake_post(endpoint, payload, headers=None):
            captured["endpoint"] = endpoint
            return {"candidates": [{"content": {"parts": [{"text": "ga"}]}}]}

        with patch.object(c, "_post", side_effect=fake_post):
            self.assertEqual(c.complete("sys", "usr"), "ga")
        self.assertEqual(
            captured["endpoint"], "https://gen/v1beta/models/gemini-1.5-flash:generateContent?key=gk"
        )


class CreateByFunctionTest(unittest.TestCase):
    SETTINGS = {
        "models": {
            "categorize": {"provider": "ollama", "model": "gemma3:4b"},
            "synthesis": {"provider": "openai", "model": "gpt-4.1-mini"},
            "facts": {"provider": "", "model": ""},
        },
        "providers": {
            "ollama": {"endpoint": "http://h:11434", "api_key": ""},
            "openai": {"endpoint": "https://api.openai.com/v1/chat/completions", "api_key": "sk"},
        },
        "model": {},
    }

    def test_ollama_function(self) -> None:
        c = create_model_client(self.SETTINGS, "categorize")
        self.assertTrue(c.enabled)
        self.assertEqual(c.config.provider, "ollama")
        self.assertEqual(c.config.endpoint, "http://h:11434")

    def test_remote_function_picks_key(self) -> None:
        c = create_model_client(self.SETTINGS, "synthesis")
        self.assertTrue(c.enabled)
        self.assertEqual(c.config.api_key, "sk")

    def test_empty_provider_is_disabled(self) -> None:
        self.assertFalse(create_model_client(self.SETTINGS, "facts").enabled)

    def test_legacy_string_model_defaults_to_ollama(self) -> None:
        legacy = {"models": {"synthesis": "gemma3:4b"}, "providers": {"ollama": {"endpoint": "http://x"}}, "model": {}}
        c = create_model_client(legacy, "synthesis")
        self.assertTrue(c.enabled)
        self.assertEqual(c.config.provider, "ollama")


if __name__ == "__main__":
    unittest.main()

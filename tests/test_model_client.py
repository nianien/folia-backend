from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from folia.pipeline.model_client import ModelClient, ModelConfig


class ModelClientTest(unittest.TestCase):
    def test_xinapi_uses_openai_compatible_chat_completions(self) -> None:
        client = ModelClient(
            ModelConfig(
                provider="xinapi",
                model="deepseek-ai/DeepSeek-R1",
                api_key_env="XIN_API_KEY",
                endpoint="https://airouter.xincache.cn/v1/chat/completions",
                timeout_seconds=30,
                temperature=0.2,
                max_output_tokens=1000,
            )
        )
        captured = {}

        def fake_post(endpoint, payload, headers):
            captured["endpoint"] = endpoint
            captured["payload"] = payload
            captured["headers"] = headers
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "ok",
                        }
                    }
                ]
            }

        with patch.dict(os.environ, {"XIN_API_KEY": "secret"}):
            with patch.object(client, "_post_json", side_effect=fake_post):
                self.assertEqual(client.complete("system", "user"), "ok")

        self.assertEqual(captured["endpoint"], "https://airouter.xincache.cn/v1/chat/completions")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(captured["payload"]["model"], "deepseek-ai/DeepSeek-R1")
        self.assertEqual(captured["payload"]["messages"][0], {"role": "system", "content": "system"})
        self.assertEqual(captured["payload"]["messages"][1], {"role": "user", "content": "user"})
        self.assertIs(captured["payload"]["stream"], False)


if __name__ == "__main__":
    unittest.main()

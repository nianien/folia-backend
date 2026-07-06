from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from folia.pipeline import categorize
from folia.pipeline.model_client import ModelError

DIRS = ["国际", "科技", "中国", "综合"]


def _client(return_value=None, side_effect=None, enabled=True):
    client = MagicMock()
    client.enabled = enabled
    client.complete = MagicMock(return_value=return_value, side_effect=side_effect)
    return client


class ClassifyTest(unittest.TestCase):
    def test_matches_llm_output(self) -> None:
        self.assertEqual(categorize.classify("t", "x", DIRS, _client("科技")), "科技")

    def test_substring_match(self) -> None:
        self.assertEqual(categorize.classify("t", "x", DIRS, _client("这条应归入 中国 类")), "中国")

    def test_fallback_when_llm_returns_unknown(self) -> None:
        self.assertEqual(categorize.classify("t", "x", DIRS, _client("体育")), "综合")

    def test_fallback_on_error(self) -> None:
        self.assertEqual(
            categorize.classify("t", "x", DIRS, _client(side_effect=ModelError("down"))), "综合"
        )

    def test_disabled_client_returns_fallback(self) -> None:
        self.assertEqual(categorize.classify("t", "x", DIRS, _client(enabled=False)), "综合")

    def test_none_client_returns_fallback(self) -> None:
        self.assertEqual(categorize.classify("t", "x", DIRS, None), "综合")


if __name__ == "__main__":
    unittest.main()

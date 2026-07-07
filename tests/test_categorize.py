from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from folia.pipeline import categorize
from folia.pipeline.model_client import ModelError

# tree: [(一级, [二级...]), ...]; 归不到二级就停在一级
TREE = [
    ("国际", ["中东"]),
    ("科技", ["AI"]),
    ("中国", []),
    ("综合", []),
]


def _client(return_value=None, side_effect=None, enabled=True):
    client = MagicMock()
    client.enabled = enabled
    client.complete = MagicMock(return_value=return_value, side_effect=side_effect)
    return client


class ClassifyTest(unittest.TestCase):
    def test_exact_two_level(self) -> None:
        self.assertEqual(categorize.classify("t", "x", TREE, _client("国际/中东")), "国际/中东")

    def test_top_only(self) -> None:
        self.assertEqual(categorize.classify("t", "x", TREE, _client("国际")), "国际")

    def test_unknown_sub_falls_to_top(self) -> None:
        self.assertEqual(categorize.classify("t", "x", TREE, _client("国际/体育")), "国际")

    def test_bare_sub_name(self) -> None:
        self.assertEqual(categorize.classify("t", "x", TREE, _client("这条应归入 中东")), "国际/中东")

    def test_top_without_subs(self) -> None:
        self.assertEqual(categorize.classify("t", "x", TREE, _client("中国")), "中国")

    def test_fallback_when_unknown(self) -> None:
        self.assertEqual(categorize.classify("t", "x", TREE, _client("体育")), "综合")

    def test_fallback_on_error(self) -> None:
        self.assertEqual(
            categorize.classify("t", "x", TREE, _client(side_effect=ModelError("down"))), "综合"
        )

    def test_disabled_client(self) -> None:
        self.assertEqual(categorize.classify("t", "x", TREE, _client(enabled=False)), "综合")

    def test_none_client(self) -> None:
        self.assertEqual(categorize.classify("t", "x", TREE, None), "综合")


if __name__ == "__main__":
    unittest.main()

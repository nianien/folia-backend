from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from folia.pipeline import categorize
from folia.pipeline.model_client import ModelError

# 两级树: [(一级, [二级...]), ...]，每个一级都含 "综合" 兜底
TREE = [
    ("国际", ["中东", "综合"]),
    ("科技", ["AI", "综合"]),
    ("中国", ["综合"]),
    ("综合", ["综合"]),
]


def _client(return_value=None, side_effect=None, enabled=True):
    client = MagicMock()
    client.enabled = enabled
    client.complete = MagicMock(return_value=return_value, side_effect=side_effect)
    return client


class ClassifyTest(unittest.TestCase):
    def test_exact_path(self) -> None:
        self.assertEqual(categorize.classify("t", "x", TREE, _client("国际/中东")), "国际/中东")

    def test_top_only_falls_to_default_sub(self) -> None:
        self.assertEqual(categorize.classify("t", "x", TREE, _client("国际")), "国际/综合")

    def test_unknown_sub_falls_to_default_sub(self) -> None:
        self.assertEqual(categorize.classify("t", "x", TREE, _client("国际/体育")), "国际/综合")

    def test_bare_sub_name(self) -> None:
        self.assertEqual(categorize.classify("t", "x", TREE, _client("这条应归入 中东")), "国际/中东")

    def test_fallback_when_unknown(self) -> None:
        self.assertEqual(categorize.classify("t", "x", TREE, _client("体育")), "综合/综合")

    def test_fallback_on_error(self) -> None:
        self.assertEqual(
            categorize.classify("t", "x", TREE, _client(side_effect=ModelError("down"))), "综合/综合"
        )

    def test_disabled_client(self) -> None:
        self.assertEqual(categorize.classify("t", "x", TREE, _client(enabled=False)), "综合/综合")

    def test_none_client(self) -> None:
        self.assertEqual(categorize.classify("t", "x", TREE, None), "综合/综合")


if __name__ == "__main__":
    unittest.main()

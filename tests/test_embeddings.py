from __future__ import annotations

import unittest

from folia.pipeline import embeddings
from folia.pipeline.embeddings import (
    EmbeddingConfig,
    EmbeddingsUnavailable,
    cosine,
    is_available,
    pack_centroid,
    unpack_centroid,
    update_centroid,
)


class EmbeddingMathTest(unittest.TestCase):
    def test_cosine_identical_is_one(self) -> None:
        self.assertAlmostEqual(cosine([1.0, 0.0], [1.0, 0.0]), 1.0)

    def test_cosine_orthogonal_is_zero(self) -> None:
        self.assertAlmostEqual(cosine([1.0, 0.0], [0.0, 1.0]), 0.0)

    def test_cosine_zero_vector_is_zero(self) -> None:
        self.assertEqual(cosine([0.0, 0.0], [1.0, 1.0]), 0.0)

    def test_cosine_length_mismatch_is_zero(self) -> None:
        self.assertEqual(cosine([1.0], [1.0, 2.0]), 0.0)

    def test_centroid_round_trip(self) -> None:
        vec = [0.1, -0.2, 0.3, 0.4]
        restored = unpack_centroid(pack_centroid(vec))
        assert restored is not None
        for a, b in zip(vec, restored):
            self.assertAlmostEqual(a, b, places=5)

    def test_unpack_none_is_none(self) -> None:
        self.assertIsNone(unpack_centroid(None))
        self.assertIsNone(unpack_centroid(b""))

    def test_update_centroid_running_mean(self) -> None:
        self.assertEqual(update_centroid(None, 0, [2.0, 4.0]), [2.0, 4.0])
        self.assertEqual(update_centroid([2.0, 4.0], 1, [4.0, 8.0]), [3.0, 6.0])


class AvailabilityTest(unittest.TestCase):
    def test_is_available_false_when_embed_raises(self) -> None:
        def boom(text, config):
            raise EmbeddingsUnavailable("connection refused")

        original = embeddings.embed
        embeddings.embed = boom  # type: ignore[assignment]
        try:
            cfg = EmbeddingConfig(url="http://localhost:1", model="bge-m3", timeout_seconds=1)
            self.assertFalse(is_available(cfg))
        finally:
            embeddings.embed = original  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()

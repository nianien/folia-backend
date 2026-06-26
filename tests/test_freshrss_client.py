from __future__ import annotations

import unittest

from folia.pipeline.config import SourceMap, SourceMeta
from folia.pipeline.freshrss_client import (
    FreshRSSClient,
    FreshRSSConfig,
    freshrss_item_to_article,
)


def make_source_map() -> SourceMap:
    return SourceMap(
        by_stream_id={"feed/3": SourceMeta(name="AP", tier="wire", category="international")},
        by_title={"Reuters World": SourceMeta(name="Reuters", tier="wire", category="international")},
    )


def sample_item(**overrides) -> dict:
    item = {
        "id": "tag:google.com,2005:reader/item/0001",
        "title": "City council approves transit plan",
        "canonical": [{"href": "https://example.com/a"}],
        "published": 1750000000,
        "summary": {"content": "<p>The city council approved the transit plan.</p>"},
        "origin": {"streamId": "feed/3", "title": "AP News"},
    }
    item.update(overrides)
    return item


class ItemMappingTest(unittest.TestCase):
    def test_maps_full_item_with_tier_from_stream_id(self) -> None:
        article = freshrss_item_to_article(sample_item(), make_source_map())
        assert article is not None
        self.assertEqual(article.source_id, "feed/3")
        self.assertEqual(article.source_name, "AP")
        self.assertEqual(article.source_tier, "wire")
        self.assertEqual(article.category, "international")
        self.assertEqual(article.url, "https://example.com/a")
        self.assertEqual(article.external_id, "tag:google.com,2005:reader/item/0001")
        self.assertIn("transit plan", article.content_html)
        self.assertEqual(article.published_at, "2025-06-15T15:06:40+00:00")

    def test_falls_back_to_alternate_href(self) -> None:
        item = sample_item(canonical=[], alternate=[{"href": "https://example.com/b"}])
        article = freshrss_item_to_article(item, make_source_map())
        assert article is not None
        self.assertEqual(article.url, "https://example.com/b")

    def test_resolves_tier_by_title_when_no_stream_match(self) -> None:
        item = sample_item(origin={"streamId": "feed/99", "title": "Reuters World"})
        article = freshrss_item_to_article(item, make_source_map())
        assert article is not None
        self.assertEqual(article.source_tier, "wire")
        self.assertEqual(article.source_name, "Reuters")

    def test_unknown_source_gets_defaults(self) -> None:
        item = sample_item(origin={"streamId": "feed/99", "title": "Mystery Blog"})
        article = freshrss_item_to_article(item, make_source_map())
        assert article is not None
        self.assertEqual(article.source_tier, "unknown")
        self.assertEqual(article.category, "uncategorized")

    def test_skips_item_without_title_or_url(self) -> None:
        self.assertIsNone(freshrss_item_to_article(sample_item(title=""), make_source_map()))
        self.assertIsNone(
            freshrss_item_to_article(sample_item(canonical=[], alternate=[]), make_source_map())
        )


class ClientLoginParseTest(unittest.TestCase):
    def _client(self) -> FreshRSSClient:
        return FreshRSSClient(
            FreshRSSConfig(
                api_url="http://localhost:8080/api/greader.php",
                user="alice",
                api_password="secret",
                timeout_seconds=5,
                batch_size=10,
                mark_read=False,
            )
        )

    def test_login_extracts_auth_token(self) -> None:
        client = self._client()
        client._request = lambda *a, **k: "SID=x\nLSID=y\nAuth=alice/abc123\n"  # type: ignore[method-assign]
        self.assertEqual(client.login(), "alice/abc123")
        self.assertEqual(client._auth_header()["Authorization"], "GoogleLogin auth=alice/abc123")

    def test_iter_unread_paginates_until_no_continuation(self) -> None:
        client = self._client()
        client._auth = "alice/abc123"
        pages = [
            '{"items": [{"id": "1"}], "continuation": "C1"}',
            '{"items": [{"id": "2"}]}',
        ]
        calls = {"n": 0}

        def fake_request(url, data=None, headers=None):
            page = pages[calls["n"]]
            calls["n"] += 1
            return page

        client._request = fake_request  # type: ignore[method-assign]
        ids = [item["id"] for item in client.iter_unread()]
        self.assertEqual(ids, ["1", "2"])


if __name__ == "__main__":
    unittest.main()

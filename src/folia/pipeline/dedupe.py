from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from .config import FALLBACK_CATEGORY
from .text import clean_text
from .embeddings import (
    EmbeddingConfig,
    EmbeddingsUnavailable,
    cosine,
    embed,
    is_available,
    pack_centroid,
    unpack_centroid,
    update_centroid,
)
from .text import jaccard


def assign_pending_articles(conn: sqlite3.Connection, settings: dict[str, Any]) -> int:
    """Accretion clustering, two explicit phases.

    Phase 1 (directed assignment): each new article joins the single nearest
    EXISTING cluster within threshold, else it is left unattached. Existing
    clusters are only updated, never merged/split — there is no cluster->cluster
    operation, so they can never merge.

    Phase 2 (seed new clusters): the unattached new articles are clustered
    among themselves into brand-new clusters.
    """
    dedupe_cfg = settings.get("dedupe", {})
    emb_cfg = EmbeddingConfig.from_settings(settings)
    use_embeddings = is_available(emb_cfg)
    if use_embeddings:
        threshold = float(dedupe_cfg.get("same_event_threshold", 0.85))
    else:
        threshold = float(dedupe_cfg.get("jaccard_threshold", 0.42))

    lookback_hours = float(dedupe_cfg.get("lookback_hours", 24))
    # 只聚"已分析"的文章(有 article_facts → 有 summary/category); 未分析的等分析完再聚。
    pending = list(
        conn.execute(
            """
            SELECT id, title, category, published_at, fetched_at, article_facts
            FROM articles
            WHERE cluster_id IS NULL AND article_facts IS NOT NULL
            ORDER BY published_at DESC, fetched_at DESC
            """
        )
    )
    if not pending:
        return 0

    existing_ids = {
        int(row["id"]) for row in conn.execute("SELECT id FROM clusters WHERE status='active'")
    }

    # Phase 1: assign to existing clusters (only) or defer.
    unattached: list[tuple[sqlite3.Row, list[float] | None]] = []
    for article in pending:
        vector = _safe_embed(comparison_text(article), emb_cfg) if use_embeddings else None
        cluster_id, _ = best_cluster(
            conn, article, vector, existing_ids, use_embeddings, threshold, lookback_hours
        )
        if cluster_id is not None:
            join_cluster(conn, cluster_id, article, vector, use_embeddings)
        else:
            unattached.append((article, vector))

    # Phase 2: cluster the leftovers among themselves into new clusters.
    new_ids: set[int] = set()
    for article, vector in unattached:
        cluster_id, _ = best_cluster(
            conn, article, vector, new_ids, use_embeddings, threshold, lookback_hours
        )
        if cluster_id is None:
            new_ids.add(create_cluster(conn, article, vector))
        else:
            join_cluster(conn, cluster_id, article, vector, use_embeddings)

    conn.commit()
    return len(pending)


def best_cluster(
    conn: sqlite3.Connection,
    article: sqlite3.Row,
    vector: list[float] | None,
    candidate_ids: set[int],
    use_embeddings: bool,
    threshold: float,
    lookback_hours: float,
) -> tuple[int | None, float]:
    """候选簇只在:同一最细分类(整串相等) + 代表文章 published_at 在 ±lookback_hours 内。

    分类整串相等 = 有二级只跟同二级聚, 只到一级只跟同一级聚(不与已到二级的混)。
    """
    if not candidate_ids:
        return None, 0.0
    art_category = article["category"] or FALLBACK_CATEGORY
    art_dt = _event_dt(article)
    article_text = None if (use_embeddings and vector is not None) else comparison_text(article)
    best_id: int | None = None
    best_score = 0.0
    for row in conn.execute(
        """
        SELECT c.id AS id, c.centroid AS centroid,
               a.category AS category, a.published_at AS published_at, a.fetched_at AS fetched_at,
               a.title AS title, a.article_facts AS article_facts
        FROM clusters c
        JOIN articles a ON a.id = c.representative_article_id
        WHERE c.status='active'
        """
    ):
        cid = int(row["id"])
        if cid not in candidate_ids:
            continue
        if (row["category"] or FALLBACK_CATEGORY) != art_category:  # 同最细分类才聚
            continue
        cl_dt = _event_dt(row)
        if art_dt and cl_dt and abs((art_dt - cl_dt).total_seconds()) > lookback_hours * 3600:
            continue  # 超出时间窗
        if use_embeddings and vector is not None:
            centroid = unpack_centroid(row["centroid"])
            if centroid is None:
                continue
            score = cosine(vector, centroid)
        else:
            score = jaccard(article_text, comparison_text(row))
        if score > best_score:
            best_id, best_score = cid, score
    if best_id is not None and best_score >= threshold:
        return best_id, best_score
    return None, best_score


def _event_dt(row: sqlite3.Row) -> datetime | None:
    """取文章时间用于时间窗比较:优先 published_at, 退 fetched_at;解析失败返回 None。"""
    for key in ("published_at", "fetched_at"):
        value = row[key] if key in row.keys() else None
        if value:
            try:
                # 去掉时区: published_at 带 +00:00、fetched_at 无时区, 统一按 naive 比差值(都近似 UTC)
                return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                continue
    return None


def create_cluster(conn: sqlite3.Connection, article: sqlite3.Row, vector: list[float] | None) -> int:
    cursor = conn.execute(
        "INSERT INTO clusters (representative_article_id, title, centroid) VALUES (?, ?, ?)",
        (article["id"], article["title"], pack_centroid(vector) if vector is not None else None),
    )
    cluster_id = int(cursor.lastrowid)
    conn.execute("UPDATE articles SET cluster_id=? WHERE id=?", (cluster_id, article["id"]))
    refresh_cluster(conn, cluster_id)
    return cluster_id


def join_cluster(
    conn: sqlite3.Connection,
    cluster_id: int,
    article: sqlite3.Row,
    vector: list[float] | None,
    use_embeddings: bool,
) -> None:
    if use_embeddings and vector is not None:
        _update_cluster_centroid(conn, cluster_id, vector)
    conn.execute("UPDATE articles SET cluster_id=? WHERE id=?", (cluster_id, article["id"]))
    refresh_cluster(conn, cluster_id)
    # 内容变了 → 标记待综述, 让综述步骤只重算它(而非全部簇)
    conn.execute("UPDATE clusters SET synthesis_status='stale' WHERE id=?", (cluster_id,))


def _safe_embed(text: str, config: EmbeddingConfig) -> list[float] | None:
    try:
        return embed(text, config)
    except EmbeddingsUnavailable:
        return None


def _update_cluster_centroid(conn: sqlite3.Connection, cluster_id: int, vector: list[float]) -> None:
    row = conn.execute("SELECT centroid FROM clusters WHERE id=?", (cluster_id,)).fetchone()
    old = unpack_centroid(row["centroid"]) if row else None
    # Running mean over the vectors actually merged so far = current article count.
    count = int(
        conn.execute(
            "SELECT COUNT(*) FROM articles WHERE cluster_id=?", (cluster_id,)
        ).fetchone()[0]
    )
    merged = update_centroid(old, count, vector)
    conn.execute("UPDATE clusters SET centroid=? WHERE id=?", (pack_centroid(merged), cluster_id))


def comparison_text(row: sqlite3.Row) -> str:
    """聚类用的比较文本 = 标题 + 分析出的 summary(核心内容), 语义更聚焦。"""
    summary = ""
    facts = row["article_facts"] if "article_facts" in row.keys() else None
    if facts:
        try:
            summary = clean_text(str(json.loads(facts).get("summary", "")))
        except (ValueError, TypeError):
            summary = ""
    return " ".join([row["title"] or "", summary]).strip()


def refresh_cluster(conn: sqlite3.Connection, cluster_id: int) -> None:
    conn.execute(
        """
        UPDATE clusters
        SET source_count = (
          SELECT COUNT(DISTINCT source_id)
          FROM articles
          WHERE cluster_id=?
        ),
        updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (cluster_id, cluster_id),
    )
    existing = {
        row["article_id"]: int(row["source_no"])
        for row in conn.execute(
            "SELECT article_id, source_no FROM cluster_sources WHERE cluster_id=?",
            (cluster_id,),
        )
    }
    rows = list(
        conn.execute(
            """
            SELECT id, source_name, title, url, published_at
            FROM articles
            WHERE cluster_id=?
            ORDER BY source_name, published_at
            """,
            (cluster_id,),
        )
    )
    conn.execute("DELETE FROM cluster_sources WHERE cluster_id=?", (cluster_id,))
    next_source_no = max(existing.values(), default=0) + 1
    source_rows = []
    for row in rows:
        source_no = existing.get(row["id"])
        if source_no is None:
            source_no = next_source_no
            next_source_no += 1
        source_rows.append((cluster_id, source_no, row["id"], row["source_name"], row["title"], row["url"], row["published_at"]))
    conn.executemany(
        """
        INSERT INTO cluster_sources (cluster_id, source_no, article_id, source_name, title, url, published_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        source_rows,
    )

from __future__ import annotations

import sqlite3
from typing import Any

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
    dedupe_cfg = settings.get("dedupe", {})
    emb_cfg = EmbeddingConfig.from_settings(settings)
    use_embeddings = is_available(emb_cfg)
    if use_embeddings:
        threshold = float(dedupe_cfg.get("same_event_threshold", 0.82))
    else:
        threshold = float(dedupe_cfg.get("jaccard_threshold", 0.42))

    pending = list(
        conn.execute(
            """
            SELECT id, title, summary, extracted_text
            FROM articles
            WHERE cluster_id IS NULL
            ORDER BY published_at DESC, fetched_at DESC
            """
        )
    )
    changed = 0
    for article in pending:
        vector = _safe_embed(comparison_text(article), emb_cfg) if use_embeddings else None
        if vector is not None:
            cluster_id, similarity = find_cluster_embedding(conn, vector, threshold)
        else:
            cluster_id, similarity = find_cluster(conn, article, threshold)

        if cluster_id is None:
            cursor = conn.execute(
                """
                INSERT INTO clusters (representative_article_id, title, centroid)
                VALUES (?, ?, ?)
                """,
                (article["id"], article["title"], pack_centroid(vector) if vector is not None else None),
            )
            cluster_id = int(cursor.lastrowid)
            similarity = 1.0
        elif vector is not None:
            _update_cluster_centroid(conn, cluster_id, vector)

        conn.execute("UPDATE articles SET cluster_id=? WHERE id=?", (cluster_id, article["id"]))
        conn.execute(
            """
            INSERT OR IGNORE INTO cluster_articles (cluster_id, article_id, similarity)
            VALUES (?, ?, ?)
            """,
            (cluster_id, article["id"], similarity),
        )
        refresh_cluster(conn, cluster_id)
        changed += 1
    conn.commit()
    return changed


def _safe_embed(text: str, config: EmbeddingConfig) -> list[float] | None:
    try:
        return embed(text, config)
    except EmbeddingsUnavailable:
        return None


def find_cluster_embedding(
    conn: sqlite3.Connection, vector: list[float], threshold: float
) -> tuple[int | None, float]:
    best_id: int | None = None
    best_score = 0.0
    for row in conn.execute("SELECT id, centroid FROM clusters WHERE status='active'"):
        centroid = unpack_centroid(row["centroid"])
        if centroid is None:
            continue
        score = cosine(vector, centroid)
        if score > best_score:
            best_id = int(row["id"])
            best_score = score
    if best_id is not None and best_score >= threshold:
        return best_id, best_score
    return None, best_score


def _update_cluster_centroid(conn: sqlite3.Connection, cluster_id: int, vector: list[float]) -> None:
    row = conn.execute(
        "SELECT centroid, source_count FROM clusters WHERE id=?", (cluster_id,)
    ).fetchone()
    old = unpack_centroid(row["centroid"]) if row else None
    count = int(row["source_count"]) if row else 0
    merged = update_centroid(old, count, vector)
    conn.execute("UPDATE clusters SET centroid=? WHERE id=?", (pack_centroid(merged), cluster_id))


def find_cluster(conn: sqlite3.Connection, article: sqlite3.Row, threshold: float) -> tuple[int | None, float]:
    article_text = comparison_text(article)
    best_id: int | None = None
    best_score = 0.0
    rows = conn.execute(
        """
        SELECT c.id, c.title, a.summary, a.extracted_text
        FROM clusters c
        JOIN articles a ON a.id = c.representative_article_id
        WHERE c.status='active'
        """
    )
    for row in rows:
        score = jaccard(article_text, comparison_text(row))
        if score > best_score:
            best_id = int(row["id"])
            best_score = score
    if best_id is not None and best_score >= threshold:
        return best_id, best_score
    return None, best_score


def comparison_text(row: sqlite3.Row) -> str:
    prefix = (row["extracted_text"] or "")[:500]
    return " ".join([row["title"] or "", row["summary"] or "", prefix])


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

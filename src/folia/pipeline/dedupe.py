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
    if not pending:
        return 0

    existing_ids = {
        int(row["id"]) for row in conn.execute("SELECT id FROM clusters WHERE status='active'")
    }

    # Phase 1: assign to existing clusters (only) or defer.
    unattached: list[tuple[sqlite3.Row, list[float] | None]] = []
    for article in pending:
        vector = _safe_embed(comparison_text(article), emb_cfg) if use_embeddings else None
        cluster_id, _ = best_cluster(conn, article, vector, existing_ids, use_embeddings, threshold)
        if cluster_id is not None:
            join_cluster(conn, cluster_id, article, vector, use_embeddings)
        else:
            unattached.append((article, vector))

    # Phase 2: cluster the leftovers among themselves into new clusters.
    new_ids: set[int] = set()
    for article, vector in unattached:
        cluster_id, _ = best_cluster(conn, article, vector, new_ids, use_embeddings, threshold)
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
) -> tuple[int | None, float]:
    if not candidate_ids:
        return None, 0.0
    best_id: int | None = None
    best_score = 0.0
    if use_embeddings and vector is not None:
        for row in conn.execute("SELECT id, centroid FROM clusters WHERE status='active'"):
            if int(row["id"]) not in candidate_ids:
                continue
            centroid = unpack_centroid(row["centroid"])
            if centroid is None:
                continue
            score = cosine(vector, centroid)
            if score > best_score:
                best_id, best_score = int(row["id"]), score
    else:
        article_text = comparison_text(article)
        for row in conn.execute(
            """
            SELECT c.id AS id, a.title AS title, a.summary AS summary,
                   a.extracted_text AS extracted_text
            FROM clusters c
            JOIN articles a ON a.id = c.representative_article_id
            WHERE c.status='active'
            """
        ):
            if int(row["id"]) not in candidate_ids:
                continue
            score = jaccard(article_text, comparison_text(row))
            if score > best_score:
                best_id, best_score = int(row["id"]), score
    if best_id is not None and best_score >= threshold:
        return best_id, best_score
    return None, best_score


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

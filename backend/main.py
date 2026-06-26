"""Frontpage read API (Cloud Run + Neon).

Public, read-mostly: list / search / detail, plus a global like counter.
Favorites and per-user state are deferred until auth is added.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

LIST_COLUMNS = (
    "story_id, title, category, category_label, tier, dek, image_url, "
    "published_at, source_count, like_count"
)

pool: ConnectionPool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set")
    pool = ConnectionPool(
        dsn,
        min_size=1,
        max_size=int(os.environ.get("DB_POOL_MAX", "8")),
        check=ConnectionPool.check_connection,
        open=True,
    )
    try:
        yield
    finally:
        pool.close()


app = FastAPI(title="Frontpage API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


def query(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    assert pool is not None
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall()


@app.get("/healthz")
def healthz() -> dict[str, str]:
    query("SELECT 1")
    return {"status": "ok"}


@app.get("/stories")
def list_stories(
    category: str | None = None,
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    where = "WHERE active"
    params: list[Any] = []
    if category:
        where += " AND category = %s"
        params.append(category)
    rows = query(
        f"SELECT {LIST_COLUMNS} FROM stories {where} "
        "ORDER BY published_at DESC NULLS LAST, story_id LIMIT %s OFFSET %s",
        (*params, limit, offset),
    )
    return {"count": len(rows), "stories": rows}


@app.get("/search")
def search(q: str = Query(..., min_length=1), limit: int = Query(30, ge=1, le=100)) -> dict[str, Any]:
    rows = query(
        f"SELECT {LIST_COLUMNS}, similarity(search_text, %s) AS score "
        "FROM stories WHERE active AND search_text ILIKE %s "
        "ORDER BY score DESC, published_at DESC NULLS LAST LIMIT %s",
        (q, f"%{q}%", limit),
    )
    return {"count": len(rows), "query": q, "stories": rows}


@app.get("/story/{story_id}")
def get_story(story_id: int) -> dict[str, Any]:
    rows = query(
        "SELECT story_id, title, category, category_label, tier, dek, image_url, "
        "published_at, source_count, synthesis_md, synthesis_model, sources, like_count "
        "FROM stories WHERE story_id = %s AND active",
        (story_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="story not found")
    return rows[0]


@app.post("/story/{story_id}/like")
def like_story(story_id: int) -> dict[str, Any]:
    rows = query(
        "UPDATE stories SET like_count = like_count + 1 WHERE story_id = %s AND active "
        "RETURNING story_id, like_count",
        (story_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="story not found")
    return rows[0]

from __future__ import annotations

import json
import sqlite3

from .model_client import ModelClient, ModelError
from .prompts import SYNTHESIS_SYSTEM_PROMPT, synthesis_user_prompt
from .text import clean_text


def synthesize_pending(conn: sqlite3.Connection, model_client: ModelClient | None = None) -> int:
    cluster_ids = [
        int(row["cluster_id"])
        for row in conn.execute(
            """
            SELECT DISTINCT cluster_id
            FROM articles
            WHERE cluster_id IS NOT NULL AND article_facts IS NOT NULL
            """
        )
    ]
    changed = 0
    for cluster_id in cluster_ids:
        markdown, model_name = synthesize_cluster(conn, cluster_id, model_client)
        if not markdown:
            continue
        conn.execute(
            """
            UPDATE clusters
            SET synthesized_text=?,
                synthesis_status='ok',
                synthesis_model=?,
                synthesis_updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (markdown, model_name, cluster_id),
        )
        changed += 1
    conn.commit()
    return changed


def synthesize_cluster(
    conn: sqlite3.Connection,
    cluster_id: int,
    model_client: ModelClient | None = None,
) -> tuple[str | None, str]:
    rows = list(
        conn.execute(
            """
            SELECT a.article_facts, cs.source_no, cs.source_name, cs.title, cs.url
            FROM articles a
            JOIN cluster_sources cs ON cs.article_id=a.id
            WHERE a.cluster_id=? AND a.article_facts IS NOT NULL
            ORDER BY cs.source_no
            """,
            (cluster_id,),
        )
    )
    if not rows:
        return None, "none"

    fact_packages = []
    for row in rows:
        package = json.loads(row["article_facts"])
        package["source_no"] = row["source_no"]
        fact_packages.append(package)
    title = clean_text(rows[0]["title"])
    if model_client is not None and model_client.enabled:
        sources = [
            {
                "source_no": row["source_no"],
                "source_name": row["source_name"],
                "title": row["title"],
                "url": row["url"],
            }
            for row in rows
        ]
        try:
            markdown = model_client.complete(
                SYNTHESIS_SYSTEM_PROMPT,
                synthesis_user_prompt(title, fact_packages, sources),
            )
            return ensure_sources(markdown, rows), model_client.model_name
        except ModelError:
            pass

    return synthesize_cluster_heuristic(rows, fact_packages, title), "heuristic-v1"


def synthesize_cluster_heuristic(rows: list[sqlite3.Row], fact_packages: list[dict], title: str) -> str:
    core = collect_unique_facts(fact_packages, "facts", limit=8)
    numbers = collect_unique_strings(fact_packages, "numbers", limit=5)

    parts = [f"# {title}", "", "## 核心事实", ""]
    parts.extend(core or ["原文未提供足够可抽取的核心事实。"])
    if numbers:
        parts.extend(["", "## 关键数字", ""])
        parts.extend(numbers)
    parts.extend(["", "## 分歧与不确定", "", "当前启发式合成器不会自动判断来源分歧；需要后续接入模型增强。"])
    parts.extend(["", "---", "", "## Sources", ""])
    for row in rows:
        parts.append(f"[{row['source_no']}] {row['source_name']} · {row['title']} · {row['url']}")
    return "\n".join(parts).strip() + "\n"


def ensure_sources(markdown: str, rows: list[sqlite3.Row]) -> str:
    text = markdown.strip()
    if "## Sources" not in text:
        text += "\n\n---\n\n## Sources"
    for row in rows:
        marker = f"[{row['source_no']}]"
        source_line = f"{marker} {row['source_name']} · {row['title']} · {row['url']}"
        if source_line not in text:
            text += f"\n{source_line}"
    return text.strip() + "\n"


def collect_unique_facts(packages: list[dict], key: str, limit: int) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for package in packages:
        source_no = package["source_no"]
        for item in package.get(key, []):
            text = clean_text(item.get("text") if isinstance(item, dict) else str(item))
            normalized = text.lower()
            if not text or normalized in seen:
                continue
            seen.add(normalized)
            output.append(f"{text} [{source_no}]")
            if len(output) >= limit:
                return output
    return output


def collect_unique_strings(packages: list[dict], key: str, limit: int) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for package in packages:
        source_no = package["source_no"]
        for item in package.get(key, []):
            text = clean_text(str(item))
            normalized = text.lower()
            if not text or normalized in seen:
                continue
            seen.add(normalized)
            output.append(f"{text} [{source_no}]")
            if len(output) >= limit:
                return output
    return output

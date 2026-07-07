from __future__ import annotations

import json
import re
import sqlite3

from .model_client import ModelClient, ModelError
from .prompts import SYNTHESIS_SYSTEM_PROMPT, synthesis_user_prompt
from .text import clean_text


def synthesize_pending(
    conn: sqlite3.Connection, model_client: ModelClient | None = None, limit: int = 5
) -> int:
    # 只综述"待综述"的簇: 新建的(synthesis_status 为 NULL)或被加了新成员的(标 'stale')。
    # 已 'ok' 且本轮没动过的跳过。综述贵(双语×大模型), 每轮限量, 多源优先, 靠循环多轮啃完。
    cluster_ids = [
        int(row["id"])
        for row in conn.execute(
            """
            SELECT c.id
            FROM clusters c
            WHERE COALESCE(c.synthesis_status, '') != 'ok'
              AND EXISTS (
                  SELECT 1 FROM articles a
                  WHERE a.cluster_id = c.id AND a.article_facts IS NOT NULL
              )
            ORDER BY c.source_count DESC, c.id
            LIMIT ?
            """,
            (limit,),
        )
    ]
    changed = 0
    for cluster_id in cluster_ids:
        text, zh, en, model_name = synthesize_cluster(conn, cluster_id, model_client)
        if not text:
            continue
        conn.execute(
            """
            UPDATE clusters
            SET synthesized_text=?,
                synthesis_zh=?,
                synthesis_en=?,
                synthesis_status='ok',
                synthesis_model=?,
                synthesis_updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (text, zh, en, model_name, cluster_id),
        )
        changed += 1
    conn.commit()
    return changed


def synthesize_cluster(
    conn: sqlite3.Connection,
    cluster_id: int,
    model_client: ModelClient | None = None,
) -> tuple[str | None, str | None, str | None, str]:
    """返回 (synthesized_text, synthesis_zh, synthesis_en, model_name)。

    走模型时生成中/英两版(synthesized_text 取中文版, 兼容既有读取);
    走规则时只有单版(zh/en 为 None, 无双语切换)。
    """
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
        return None, None, None, "none"

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
        valid_nos = {int(row["source_no"]) for row in rows}
        try:
            zh_body = model_client.complete(
                SYNTHESIS_SYSTEM_PROMPT, synthesis_user_prompt(title, fact_packages, sources, "zh")
            )
            en_body = model_client.complete(
                SYNTHESIS_SYSTEM_PROMPT, synthesis_user_prompt(title, fact_packages, sources, "en")
            )
            # 去掉指向不存在来源的引用编号(如 [7]), 再由程序追加权威 Sources。
            zh = ensure_sources(strip_invalid_citations(zh_body, valid_nos), rows)
            en = ensure_sources(strip_invalid_citations(en_body, valid_nos), rows)
            return zh, zh, en, model_client.model_name
        except ModelError:
            pass

    return synthesize_cluster_heuristic(rows, fact_packages, title), None, None, "heuristic-v1"


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


_CITE = re.compile(r"\[(\d+)\]")


def strip_invalid_citations(text: str, valid_nos: set[int]) -> str:
    """删掉正文里指向不存在来源的引用编号(模型偶尔编 [7])。"""
    return _CITE.sub(lambda m: m.group(0) if int(m.group(1)) in valid_nos else "", text)


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

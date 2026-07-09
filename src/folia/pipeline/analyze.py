"""分析阶段:一次 LLM 调用完成 分类 + 标签 + 提炼(核心内容/关键信息)。

合并了原来的 categorize + facts 两次调用为一次。纯 LLM、每轮限量、逐条落库;
无模型或本轮失败的条目留到下轮。**分类兜底在代码里**(prompt 不提"综合",不污染判断):
模型给的分类不在目录里/为空 → 落 FALLBACK_CATEGORY。
"""
from __future__ import annotations

import json
import sqlite3

from .config import FALLBACK_CATEGORY
from .model_client import ModelClient, ModelError
from .prompts import ANALYZE_SYSTEM_PROMPT, analyze_user_prompt
from .text import clean_text


def analyze_pending(conn: sqlite3.Connection, model_client: ModelClient | None = None, limit: int = 5) -> int:
    if model_client is None or not model_client.enabled:
        return 0
    tree, valid = _catalog(conn)
    if not tree:
        return 0
    rows = list(
        conn.execute(
            """
            SELECT a.id, a.title, a.source_name, a.extracted_text, cs.source_no
            FROM articles a
            LEFT JOIN cluster_sources cs ON cs.article_id=a.id
            WHERE a.extracted_text IS NOT NULL
              AND (a.article_facts IS NULL OR a.fact_status != 'ok')
            LIMIT ?
            """,
            (limit,),
        )
    )
    done = 0
    for row in rows:
        result = _analyze_one(row, tree, model_client)
        if result is None:
            continue  # 本轮没出来, 留到下轮
        category, tags, package = result
        category = category if category in valid else FALLBACK_CATEGORY  # 代码兜底, 非目录内 → 综合
        conn.execute(
            "UPDATE articles SET category=?, tags=?, article_facts=?, fact_status='ok' WHERE id=?",
            (category, ",".join(tags), json.dumps(package, ensure_ascii=False), row["id"]),
        )
        conn.commit()  # 逐条落库: 进度可见, 中断不丢
        done += 1
    return done


def _catalog(conn: sqlite3.Connection) -> tuple[list[tuple[str, list[str]]], set[str]]:
    """返回 (分类树, 合法分类字符串集合)。合法集含"一级"与"一级/二级"整串,用于代码校验。"""
    rows = conn.execute("SELECT name, parent FROM directory ORDER BY sort_order, name").fetchall()
    tops = [r["name"] for r in rows if not r["parent"]]
    tree = [(top, [r["name"] for r in rows if r["parent"] == top]) for top in tops]
    valid = set(tops)
    for top, subs in tree:
        for sub in subs:
            valid.add(f"{top}/{sub}")
    return tree, valid


def _analyze_one(
    row: sqlite3.Row, tree: list[tuple[str, list[str]]], model_client: ModelClient
) -> tuple[str, list[str], dict] | None:
    source_no = row["source_no"] or 0
    article = {"source_name": row["source_name"], "title": row["title"], "text": row["extracted_text"]}
    base = analyze_user_prompt(article, tree)
    for attempt in range(2):
        user = base if attempt == 0 else (
            base + "\n\n上次输出不是合法 JSON。只输出符合 schema 的合法 JSON，不要任何多余文字。"
        )
        try:
            data = parse_json_object(model_client.complete(ANALYZE_SYSTEM_PROMPT, user))
        except ModelError:
            return None
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
        category = clean_text(str(data.get("category", "")))
        tags = _norm_list(data.get("tags", []))
        package = {
            "article_id": row["id"],
            "source_no": source_no,
            "source_name": row["source_name"],
            "title": row["title"],
            "summary": clean_text(str(data.get("summary", ""))),
            "key_points": _norm_list(data.get("key_points", [])),
        }
        return category, tags, package
    return None


def parse_json_object(value: str) -> dict:
    stripped = value.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("model output does not contain a JSON object")
    parsed = json.loads(stripped[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("model output JSON is not an object")
    return parsed


def _norm_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := clean_text(str(item)))]

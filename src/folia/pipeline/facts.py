from __future__ import annotations

import json
import sqlite3

from .model_client import ModelClient, ModelError
from .prompts import FACT_SYSTEM_PROMPT, fact_user_prompt
from .text import clean_text


def facts_pending(conn: sqlite3.Connection, model_client: ModelClient | None = None) -> int:
    """抽取待处理文章的事实包(纯 LLM)。

    无模型(未配置/本地也没起)或本轮抽取失败的条目,留到下轮再试,不产出规则兜底。
    """
    if model_client is None or not model_client.enabled:
        return 0
    rows = list(
        conn.execute(
            """
            SELECT a.id, a.title, a.source_name, a.extracted_text, cs.source_no
            FROM articles a
            LEFT JOIN cluster_sources cs ON cs.article_id=a.id
            WHERE a.extracted_text IS NOT NULL
              AND (a.article_facts IS NULL OR a.fact_status != 'ok')
            """
        )
    )
    done = 0
    for row in rows:
        facts = extract_facts_with_model(row, model_client)
        if facts is None:
            continue  # 本轮没抽出来, 留到下轮
        conn.execute(
            "UPDATE articles SET article_facts=?, fact_status='ok' WHERE id=?",
            (json.dumps(facts, ensure_ascii=False), row["id"]),
        )
        done += 1
    conn.commit()
    return done


def extract_facts_with_model(row: sqlite3.Row, model_client: ModelClient) -> dict | None:
    """调模型抽事实包;非法 JSON 重试一次,仍失败或调用出错 → 返回 None(本轮不出)。"""
    source_no = row["source_no"] or 0
    article = {
        "article_id": row["id"],
        "source_no": source_no,
        "source_name": row["source_name"],
        "title": row["title"],
        "text": row["extracted_text"],
    }
    base_prompt = fact_user_prompt(article)
    for attempt in range(2):
        user = base_prompt if attempt == 0 else (
            base_prompt + "\n\n上次输出不是合法 JSON。只输出符合 schema 的合法 JSON，不要任何多余文字。"
        )
        try:
            facts = parse_json_object(model_client.complete(FACT_SYSTEM_PROMPT, user))
        except ModelError:
            return None
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
        facts["article_id"] = row["id"]
        facts["source_no"] = source_no
        facts["source_name"] = row["source_name"]
        facts["title"] = row["title"]
        return normalize_fact_package(facts)
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


def normalize_fact_package(value: dict) -> dict:
    """归一为精简事实包:元数据 + 核心内容(summary) + 关键信息(key_points)。"""
    return {
        "article_id": str(value.get("article_id", "")),
        "source_no": int(value.get("source_no", 0)),
        "source_name": str(value.get("source_name", "")),
        "title": str(value.get("title", "")),
        "summary": clean_text(str(value.get("summary", ""))),
        "key_points": normalize_strings(value.get("key_points", [])),
    }


def normalize_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := clean_text(str(item)))]

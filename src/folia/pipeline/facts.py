from __future__ import annotations

import json
import re
import sqlite3

from .model_client import ModelClient, ModelError
from .prompts import FACT_SYSTEM_PROMPT, fact_user_prompt
from .text import clean_text

SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+")
NUMBER_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?%?\b")


def extract_facts(text: str, article_id: str, source_no: int, source_name: str, title: str) -> dict:
    sentences = [clean_text(sentence) for sentence in SENTENCE_RE.split(text) if clean_text(sentence)]
    facts = [{"text": sentence, "type": "core_fact"} for sentence in score_sentences(sentences)[:8]]
    numbers = []
    for sentence in sentences:
        if NUMBER_RE.search(sentence):
            numbers.append(sentence)
        if len(numbers) >= 5:
            break
    return {
        "article_id": article_id,
        "source_no": source_no,
        "source_name": source_name,
        "title": title,
        "facts": facts,
        "numbers": numbers,
        "quotes": [],
        "background": [],
        "uncertainties": [],
    }


def score_sentences(sentences: list[str]) -> list[str]:
    scored: list[tuple[int, int, str]] = []
    for index, sentence in enumerate(sentences):
        score = 0
        if NUMBER_RE.search(sentence):
            score += 3
        if any(word in sentence.lower() for word in ["said", "announced", "reported", "according", "will"]):
            score += 2
        if 60 <= len(sentence) <= 260:
            score += 1
        scored.append((score, -index, sentence))
    scored.sort(reverse=True)
    return [sentence for _, _, sentence in scored]


def facts_pending(conn: sqlite3.Connection, model_client: ModelClient | None = None) -> int:
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
    for row in rows:
        facts = extract_facts_with_model(row, model_client)
        conn.execute(
            "UPDATE articles SET article_facts=?, fact_status='ok' WHERE id=?",
            (json.dumps(facts, ensure_ascii=False), row["id"]),
        )
    conn.commit()
    return len(rows)


def extract_facts_with_model(row: sqlite3.Row, model_client: ModelClient | None) -> dict:
    source_no = row["source_no"] or 0
    if model_client is None or not model_client.enabled:
        return extract_facts(row["extracted_text"], row["id"], source_no, row["source_name"], row["title"])

    article = {
        "article_id": row["id"],
        "source_no": source_no,
        "source_name": row["source_name"],
        "title": row["title"],
        "text": row["extracted_text"],
    }
    base_prompt = fact_user_prompt(article)
    # 小模型偶尔吐非法 JSON: 解析失败重试一次(带纠正提示), 仍失败退回规则抽取。
    for attempt in range(2):
        user = base_prompt if attempt == 0 else (
            base_prompt + "\n\n上次输出不是合法 JSON。只输出符合 schema 的合法 JSON，不要任何多余文字。"
        )
        try:
            facts = parse_json_object(model_client.complete(FACT_SYSTEM_PROMPT, user))
        except ModelError:
            break
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
        facts["article_id"] = row["id"]
        facts["source_no"] = source_no
        facts["source_name"] = row["source_name"]
        facts["title"] = row["title"]
        return normalize_fact_package(facts)
    return extract_facts(row["extracted_text"], row["id"], source_no, row["source_name"], row["title"])


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
    return {
        "article_id": str(value.get("article_id", "")),
        "source_no": int(value.get("source_no", 0)),
        "source_name": str(value.get("source_name", "")),
        "title": str(value.get("title", "")),
        "facts": normalize_facts(value.get("facts", [])),
        "numbers": normalize_strings(value.get("numbers", [])),
        "quotes": normalize_quotes(value.get("quotes", [])),
        "background": normalize_strings(value.get("background", [])),
        "uncertainties": normalize_strings(value.get("uncertainties", [])),
    }


FACT_TYPES = {"event", "decision", "statement", "cause", "impact", "timeline"}


def normalize_facts(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    facts = []
    for item in value:
        if isinstance(item, dict):
            text = clean_text(str(item.get("text", "")))
            fact_type = clean_text(str(item.get("type", ""))).lower()
        else:
            text = clean_text(str(item))
            fact_type = ""
        if fact_type not in FACT_TYPES:  # 越界/缺失 → 归一为 event
            fact_type = "event"
        if text:
            facts.append({"text": text, "type": fact_type})
    return facts


def normalize_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := clean_text(str(item)))]


def normalize_quotes(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    quotes = []
    for item in value:
        if not isinstance(item, dict):
            continue
        speaker = clean_text(str(item.get("speaker", "")))
        text = clean_text(str(item.get("text", "")))
        if text:
            quotes.append({"speaker": speaker, "text": text})
    return quotes

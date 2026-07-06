"""按内容给文章定分类 —— 调所选 provider 的 LLM, 归到用户维护的目录之一。

目录列表来自 db 的 directory 表(用户在「目录」页维护)。分类由**新闻内容**决定,
与来自哪个 RSS 源无关。LLM 只回一个目录名; 回的不在目录里 / 未配模型 / 调用失败 → 落 FALLBACK。
provider 与模型在「模型」页选(本地 Ollama 或任一远程 provider), 这里不关心是哪家。
"""
from __future__ import annotations

from .model_client import ModelClient, ModelError

FALLBACK = "综合"

SYSTEM_PROMPT = (
    "你是新闻分类器。把用户给的新闻归入所给目录之一，只回一个目录名，"
    "不要解释、不要标点、不要引号。"
)


def classify(
    title: str | None,
    text: str | None,
    directory_names: list[str],
    client: ModelClient | None,
) -> str:
    names = [n for n in directory_names if n]
    if not names or client is None or not client.enabled:
        return FALLBACK if FALLBACK in names or not names else names[-1]
    catalog = "、".join(names)
    snippet = (text or "")[:300]
    user_prompt = (
        f"目录：{catalog}\n"
        f"标题：{title or ''}\n"
        f"摘要：{snippet}\n"
        "目录："
    )
    try:
        out = client.complete(SYSTEM_PROMPT, user_prompt)
    except ModelError:
        return FALLBACK if FALLBACK in names else names[-1]
    for name in names:  # 容错: 命中作为子串出现的目录名
        if name and name in out:
            return name
    return FALLBACK if FALLBACK in names else names[-1]

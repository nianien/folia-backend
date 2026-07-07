"""按内容给文章定两级分类 —— 调所选 provider 的 LLM, 归到用户维护的「一级/二级」之一。

分类树来自 db 的 directory 表(用户在「新闻分类」页维护): 一级(parent='')下挂若干二级,
每个一级都有一个默认二级 "综合"。分类由**新闻内容**决定, 与来自哪个 RSS 源无关。
LLM 回 "一级/二级"; 归不到具体二级 → 落该一级的 "综合"; 彻底归不了 → 兜底一级的 "综合"。
articles.category 存拼接后的路径字符串, 单列唯一。
"""
from __future__ import annotations

from .config import FALLBACK_CATEGORY
from .model_client import ModelClient, ModelError
from .prompts import CATEGORIZE_SYSTEM_PROMPT, categorize_user_prompt

# tree: [(一级, [二级...]), ...]
Tree = list[tuple[str, list[str]]]


def _fallback(tree: Tree) -> str:
    tops = [t for t, _ in tree if t]
    return FALLBACK_CATEGORY if FALLBACK_CATEGORY in tops else (tops[-1] if tops else FALLBACK_CATEGORY)


def _match(out: str, tree: Tree) -> str:
    text = (out or "").strip()
    subs_by_top = {top: subs for top, subs in tree}
    # 解析 "一级/二级": 二级有效 → 路径; 二级无效 → 只留一级
    if "/" in text:
        top, _, sub = text.partition("/")
        top, sub = top.strip(), sub.strip()
        if top in subs_by_top:
            return f"{top}/{sub}" if sub in subs_by_top[top] else top
    # 命中某个一级(+可能的二级)
    for top, subs in tree:
        if top and top in text:
            for sub in subs:
                if sub and sub in text:
                    return f"{top}/{sub}"
            return top
    # 只命中某个二级(未提一级)
    for top, subs in tree:
        for sub in subs:
            if sub and sub in text:
                return f"{top}/{sub}"
    return _fallback(tree)


def classify(
    title: str | None,
    text: str | None,
    tree: Tree,
    client: ModelClient | None,
) -> str:
    tops = [t for t, _ in tree if t]
    if not tops or client is None or not client.enabled:
        return _fallback(tree)
    user_prompt = categorize_user_prompt(title or "", (text or "")[:300], tree)
    try:
        out = client.complete(CATEGORIZE_SYSTEM_PROMPT, user_prompt)
    except ModelError:
        return _fallback(tree)
    return _match(out, tree)

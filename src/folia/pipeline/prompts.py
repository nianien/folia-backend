from __future__ import annotations

import json


FACT_SYSTEM_PROMPT = """你是新闻事实抽取器。你只从用户提供的单篇文章中抽取信息。
不要补写、推断或编造原文没有的信息。输出必须是合法 JSON，不要使用 Markdown 代码块。"""


SYNTHESIS_SYSTEM_PROMPT = """你是新闻编辑。你将收到同一事件的多篇报道事实包。
任务是生成一篇压缩后的完整新闻稿，不是短摘要。
每个关键事实必须用来源编号标注，如 [1] 或 [1][3]。
如果来源之间有冲突，写入“分歧与不确定”，不要强行合并。
不要使用无来源支持的信息。输出 Markdown。"""


def fact_user_prompt(article: dict) -> str:
    return f"""请从以下新闻中抽取结构化事实。

输出 JSON schema:
{{
  "article_id": "{article["article_id"]}",
  "source_no": {article["source_no"]},
  "source_name": "{article["source_name"]}",
  "title": "{article["title"]}",
  "facts": [{{"text": "...", "type": "core_fact"}}],
  "numbers": ["..."],
  "quotes": [{{"speaker": "...", "text": "..."}}],
  "background": ["..."],
  "uncertainties": ["..."]
}}

要求:
- facts 保留核心事实、时间、地点、人物、机构、政策、因果关系和后续影响。
- numbers 保留带数字、金额、比例、时间线的关键句。
- quotes 只保留有新闻价值的直接引述。
- background 只保留理解事件必需的背景。
- uncertainties 记录原文明确未说明或仍不确定的事项。
- 不要添加原文没有的信息。

标题: {article["title"]}
来源: [{article["source_no"]}] {article["source_name"]}

正文:
{article["text"]}
"""


def synthesis_user_prompt(title: str, fact_packages: list[dict], sources: list[dict]) -> str:
    return f"""请基于以下同一事件的多源事实包，生成压缩完整稿。

建议结构:
# 事件标题
## 核心事实
## 关键细节
## 背景
## 分歧与不确定
---
## Sources

引用规则:
- 每个关键事实后必须标注来源编号，例如 [1]。
- 多个来源支持同一事实时，合并写并标多个编号，例如 [1][2]。
- 不要编造来源未提供的背景、数字或结论。
- Sources 部分必须列出所有来源编号。

候选标题: {title}

事实包 JSON:
{json.dumps(fact_packages, ensure_ascii=False, indent=2)}

来源:
{json.dumps(sources, ensure_ascii=False, indent=2)}
"""

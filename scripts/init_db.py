#!/usr/bin/env python3
"""一次性初始化 SQLite 库:建表 + 写入初始数据(订阅源 / 分类 / 运行期配置)。

- 复用 db.py 的 SCHEMA/init_db 建表(不重复维护 DDL),再调 db 的通用插入方法写默认数据。
- 幂等且非破坏:全部 INSERT OR IGNORE,只补缺失的行/键,已有配置一律保留。
- 运行期代码(config / db / panel / poller ...)**不引用本文件**;默认数据只活在这里。
  之后一切以数据库为唯一真相;面板负责读 DB 显示、页面增删改。

库路径取 config.database_path()(env FOLIA_DB_PATH 或默认 data/frontpage.sqlite)。

用法:
    python scripts/init_db.py                        # 初始化 ./data/frontpage.sqlite
    FOLIA_DB_PATH=/tmp/x.sqlite python scripts/init_db.py
    容器启动会自动跑一次(见 Dockerfile CMD);面板「一键初始化」按钮同样触发它。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 允许未安装包时直接跑: 把 src/ 加进导入路径
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from folia.pipeline.config import PROVIDERS, database_path
from folia.pipeline.db import connect, init_db, insert_directory, insert_feed, insert_setting


# 默认订阅源: (feed_url, 名称, 一句话描述)。原始 RSS/Atom;分类由内容决定,不挂在源上。
DEFAULT_FEEDS: list[tuple[str, str, str]] = [
    ("http://rsshub:1200/apnews/topics/apf-topnews", "AP News", "美联社,国际通讯社头条快讯"),
    ("https://www.aljazeera.com/xml/rss/all.xml", "Al Jazeera", "半岛电视台,国际新闻"),
    ("https://www.theguardian.com/world/rss", "Guardian World", "《卫报》国际版"),
    ("https://feeds.bbci.co.uk/news/world/rss.xml", "BBC World", "BBC 世界新闻"),
    ("https://hnrss.org/frontpage", "Hacker News", "科技创业社区热门讨论"),
    ("http://rsshub:1200/latepost", "LatePost", "晚点 LatePost,中文科技与商业报道"),
]

# 默认新闻分类: (名称, 父级, 描述, 颜色, 排序)。父级 "" = 一级。只播一级,二级用户按需加。
    # 一级分类 → (颜色, 排序, 描述, [(二级名, 二级描述), ...])。二级沿用一级颜色。
# 描述会连同分类名一起喂给 analyze 的 LLM, 帮它判断归类; 所以描述要写清"这类装什么"。
# "有二级归二级、无二级归一级";综合是代码兜底类, 不设二级。
# 描述写成"判定规则"(写给 LLM 看), 单句式:说清"何时选本类"+ 冲突时如何优先。
_CATEGORIES: dict[str, tuple[str, int, str, list[tuple[str, str]]]] = {
    "国际": ("#0f9d76", 1, "非中国国家/地区之间的关系或海外一般时事;仅当不属于任何明确主题类(科技/财经/军事/政治/体育/娱乐等)时才选本类。", [
        ("美国", "以美国为主体的一般海外时事;具体主题(政治/军事/科技等)归对应主题类。"),
        ("欧洲", "以欧洲国家或欧盟为主体的一般海外时事。"),
        ("中东", "以中东地区为主体的一般时事;具体军事行动归军事。"),
        ("亚太", "以亚太地区(不含中国大陆)为主体的一般时事。"),
        ("俄乌", "俄乌之间的一般时事;军事行动归军事、外交谈判归政治/外交。"),
        ("其他地区", "非洲、拉美等其他地区的一般时事。"),
    ]),
    "中国": ("#2a9d8f", 2, "以中国大陆或港澳台为主体的本地时事;仅当不属于明确主题类时才选本类。", [
        ("时政", "中国的政治、政府运作与政策;宏观/产业经济归中国/经济。"),
        ("经济", "中国的宏观经济与产业政策;市场/个股/公司财报归财经。"),
        ("社会", "中国大陆及港澳台本地的社会民生新闻。"),
        ("港澳台", "以香港、澳门、台湾为主体的新闻。"),
    ]),
    "科技": ("#1f8fb3", 3, "新闻重点是技术本身、技术产品或科技产业(互联网/软件/AI/芯片/消费电子)时选本类;若重点是公司财报或所在国而非技术,归财经或国际。", [
        ("人工智能", "核心涉及 AI 技术/产品/行业:大模型、AI Agent、机器学习、ChatGPT/Claude/Gemini/DeepSeek 等;若重点是 AI 公司财报/融资则归财经/公司。"),
        ("互联网", "互联网平台、软件与在线产品业务。"),
        ("数码硬件", "消费电子、手机、可穿戴等硬件产品。"),
        ("半导体", "芯片设计、制造工艺、晶圆与制程。"),
        ("创业公司", "初创企业的产品与发展;纯融资/并购归财经/公司。"),
    ]),
    "财经": ("#c2872f", 4, "新闻重点是市场、投资、企业经营或个人理财时选本类,即使主体是科技公司。", [
        ("宏观经济", "非中国的宏观经济、货币、就业、通胀。"),
        ("股市", "股票市场、指数与交易。"),
        ("公司", "企业经营行为:财报、收购、并购、IPO、CEO变动、裁员、战略调整;即使是科技公司,只要重点是经营活动就归本类。"),
        ("楼市", "房地产市场与交易。"),
        ("加密货币", "数字货币、区块链与加密资产。"),
    ]),
    "政治": ("#b0413e", 5, "新闻重点是政策、选举、政党或外交(非军事行动)时选本类。", [
        ("外交", "国家间外交、峰会、双边关系表态;军事行动归军事。"),
        ("选举", "选举、公投与政党竞争。"),
        ("政策", "立法与公共政策。"),
    ]),
    "军事": ("#5b6b52", 6, "新闻重点是战争、军事行动、武装冲突或武器装备时选本类,优先于国际/政治。", [
        ("冲突", "战争与武装冲突的军事行动。"),
        ("装备", "武器与军事装备。"),
        ("国防", "国防建设、军队与军费。"),
    ]),
    "社会": ("#8a6d9c", 7, "新闻重点是民生、突发、法治或灾害等社会议题时选本类;若是中国本地社会新闻,归中国/社会。", [
        ("民生", "就业、住房、物价等民生议题;中国本地归中国/社会。"),
        ("突发事件", "突发新闻、事故与公共安全事件。"),
        ("法治", "司法、犯罪、执法与审判。"),
        ("灾害", "自然灾害与重大事故。"),
    ]),
    "科学": ("#3f6fb0", 8, "新闻重点是科研、航天或基础科学发现时选本类(非商业化技术产品)。", [
        ("航天", "太空探索、卫星与火箭发射。"),
        ("生命科学", "生物、基因与医学科研;临床医疗归健康。"),
        ("物理", "物理与基础科学研究。"),
        ("考古", "考古发现与历史研究。"),
    ]),
    "健康": ("#3f9c8f", 9, "新闻重点是医疗、疾病、疫情或公共卫生时选本类。", [
        ("医疗", "医疗、药物与临床诊疗。"),
        ("疫情", "传染病与疫情。"),
        ("公共卫生", "公共卫生政策与体系。"),
        ("心理", "心理健康与精神卫生。"),
    ]),
    "环境": ("#4a8c3f", 10, "新闻重点是气候、能源本身、电力供应或生态环境政策时选本类;相关企业与市场归财经。", [
        ("气候变化", "气候变化与全球变暖。"),
        ("能源", "能源本身、电力供应与能源政策;能源企业/市场归财经。"),
        ("生态", "生态保护与环境治理。"),
    ]),
    "体育": ("#2f7d5b", 11, "新闻重点是体育赛事或运动员时选本类。", [
        ("足球", "足球赛事与球员。"),
        ("篮球", "篮球赛事与球员。"),
        ("网球", "网球赛事与球员。"),
        ("综合赛事", "奥运会及其他综合体育赛事。"),
    ]),
    "娱乐": ("#c85c8e", 12, "新闻重点是影视、音乐、游戏或名人时选本类。", [
        ("影视", "电影与电视剧。"),
        ("音乐", "音乐与演出。"),
        ("游戏", "电子游戏。"),
        ("明星", "名人动态与八卦。"),
    ]),
    "文化": ("#9c6b3f", 13, "新闻重点是艺术、历史、教育或出版阅读时选本类。", [
        ("艺术", "艺术、展览与设计。"),
        ("历史", "历史与人文。"),
        ("教育", "教育、学校与考试。"),
        ("阅读", "图书与出版。"),
    ]),
    "综合": ("#6d7c75", 99, "仅当以上所有一级分类都明显不匹配时才选本类。", []),
}


def _build_directories() -> list[tuple[str, str, str, str, int]]:
    """展开成 (名称, 父级, 描述, 颜色, 排序);一级 parent='',二级 parent=所属一级。"""
    rows: list[tuple[str, str, str, str, int]] = []
    for top, (color, order, desc, subs) in _CATEGORIES.items():
        rows.append((top, "", desc, color, order))
        for i, (sub, sub_desc) in enumerate(subs, start=1):
            rows.append((sub, top, sub_desc, color, i))
    return rows


DEFAULT_DIRECTORIES: list[tuple[str, str, str, str, int]] = _build_directories()

# 本地 Ollama 地址: 写死成容器该用的地址(compose 里 panel 经 host.docker.internal 访问宿主)。
OLLAMA_URL = "http://host.docker.internal:11434"


def default_settings() -> dict:
    """运行期通用配置的初值(嵌套 dict,写入时拍平成点分键存进 settings 表)。"""
    providers = {
        name: {
            "endpoint": OLLAMA_URL if name == "ollama" else endpoint,
            "api_key": os.environ.get(key_env, "") if key_env else "",
        }
        for name, _label, endpoint, key_env in PROVIDERS
    }
    return {
        "database": {"url": os.environ.get("DATABASE_URL", "")},
        "poller": {"timeout_seconds": 20},
        "embeddings": {"url": OLLAMA_URL, "timeout_seconds": 30},
        "dedupe": {"same_event_threshold": 0.85, "jaccard_threshold": 0.42, "lookback_hours": 48},
        "model": {"timeout_seconds": 120, "temperature": 0.2, "max_output_tokens": 3000, "num_ctx": 8192},
        "providers": providers,
        "models": {
            "embedding": "bge-m3",
            "analyze": {"provider": "ollama", "model": "qwen3.6:35b-a3b"},
            "synthesis": {"provider": "ollama", "model": "qwen3.6:35b-a3b"},
        },
        "loop": {"enabled": False, "interval": 1800},
    }


def _flatten(tree: dict, prefix: str = "") -> list[tuple[str, str]]:
    """嵌套 dict → [(点分键, 字符串值)]。bool 存 '1'/'0',其余 str()。"""
    out: list[tuple[str, str]] = []
    for key, value in tree.items():
        dotted = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.extend(_flatten(value, dotted))
        else:
            out.append((dotted, "1" if value is True else "0" if value is False else str(value)))
    return out


def main() -> int:
    path = database_path()
    conn = connect(path)
    try:
        init_db(conn)  # 只建表 + 迁移,不含任何数据
        directories = sum(insert_directory(conn, *row) for row in DEFAULT_DIRECTORIES)
        feeds = sum(insert_feed(conn, url, name, desc) for url, name, desc in DEFAULT_FEEDS)
        settings = sum(insert_setting(conn, k, v) for k, v in _flatten(default_settings()))
        conn.commit()
    finally:
        conn.close()
    print(f"✓ initialized {path}")
    print(f"  directories +{directories}, feeds +{feeds}, settings +{settings}  (已存在的跳过)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import os
import re
from typing import Protocol

from .models import Analysis, NewsItem, WatchItem, iso_utc, news_event_key
from .news_impact import explain_news_impact
from .stock_mentions import news_clauses


class Analyzer(Protocol):
    name: str
    analysis_id: str

    def analyze(self, item: NewsItem) -> Analysis: ...


POSITIVE_PHRASES = {
    "大幅超预期": 2.4,
    "超预期": 2.0,
    "上调业绩指引": 2.2,
    "上调盈利预测": 1.8,
    "上调目标价": 1.4,
    "上调评级": 1.5,
    "扭亏为盈": 1.9,
    "扭亏": 1.7,
    "利润增长": 1.2,
    "营收增长": 1.0,
    "净利润增长": 1.3,
    "回购": 1.2,
    "增持": 1.2,
    "获批": 1.4,
    "中标": 1.1,
    "大额订单": 1.3,
    "创纪录": 1.4,
    "历史新高": 1.2,
    "需求强劲": 1.1,
    "供不应求": 1.0,
    "净值上涨": 0.8,
    "股价上涨": 0.7,
    "大涨": 1.0,
    "飙升": 1.0,
    "反弹": 0.65,
    "看涨": 0.8,
    "买入评级": 1.0,
    "跑赢大盘": 0.9,
    "beat expectations": 2.0,
    "beats expectations": 2.0,
    "beat estimates": 2.0,
    "beats estimates": 2.0,
    "tops estimates": 1.8,
    "above estimates": 1.5,
    "raises guidance": 2.1,
    "raised guidance": 2.1,
    "raises outlook": 1.8,
    "raised outlook": 1.8,
    "buyback": 1.2,
    "approved": 1.2,
    "wins approval": 1.4,
    "record revenue": 1.5,
    "record profit": 1.6,
    "profit growth": 1.2,
    "revenue growth": 1.0,
    "strong demand": 1.1,
    "wins contract": 1.2,
    "awarded contract": 1.1,
    "raises price target": 1.4,
    "price target raised": 1.4,
    "upgraded to buy": 1.5,
    "upgrade": 1.3,
    "outperform": 1.0,
    "overweight": 0.9,
    "buy rating": 1.0,
    "buy call": 1.0,
    "buy signal": 0.8,
    "top pick": 0.8,
    "bullish": 0.8,
    "upside": 0.65,
    "surges": 1.0,
    "surged": 1.0,
    "soars": 1.0,
    "soared": 1.0,
    "jumps": 0.85,
    "jumped": 0.85,
    "rallies": 0.75,
    "rallied": 0.75,
    "rebounds": 0.65,
    "rebounded": 0.65,
    "rises": 0.60,
    "rose": 0.60,
    "gains": 0.60,
    "gained": 0.60,
    "closes above": 0.50,
    "大幅上修": 1.8,
    "上修预期": 1.4,
    "上修預期": 1.4,
    "订单增长": 1.1,
    "訂單增長": 1.1,
    "积压订单增长": 1.2,
    "積壓訂單增長": 1.2,
    "毛利率提升": 1.2,
    "利润率提升": 1.2,
    "市占率提升": 1.0,
    "提高分红": 1.0,
    "提高分紅": 1.0,
    "内部人增持": 1.1,
    "战略合作": 0.55,
    "戰略合作": 0.55,
    "positive trial results": 1.4,
    "margin expansion": 1.2,
    "backlog growth": 1.1,
    "backlog grows": 1.1,
    "gains market share": 1.0,
    "raises dividend": 1.0,
    "dividend increase": 1.0,
    "insider buying": 1.1,
    "secures contract": 1.2,
    "secured contract": 1.2,
    "strategic partnership": 0.55,
    "better than expected": 1.7,
    "stronger than expected": 1.7,
}

NEGATIVE_PHRASES = {
    "大幅低于预期": 2.4,
    "低于预期": 2.0,
    "下调业绩指引": 2.2,
    "下调盈利预测": 1.8,
    "下调目标价": 1.4,
    "下调评级": 1.5,
    "亏损": 1.3,
    "暴雷": 1.8,
    "调查": 1.2,
    "处罚": 1.5,
    "诉讼": 1.1,
    "召回": 1.3,
    "减持": 1.0,
    "违约": 2.0,
    "停产": 1.3,
    "需求疲软": 1.1,
    "订单下滑": 1.1,
    "营收下滑": 1.1,
    "利润下滑": 1.3,
    "净值下跌": 0.8,
    "股价下跌": 0.7,
    "大跌": 1.0,
    "暴跌": 1.2,
    "跳水": 0.9,
    "走低": 0.55,
    "看跌": 0.8,
    "卖出评级": 1.0,
    "跑输大盘": 0.9,
    "misses expectations": 2.0,
    "missed expectations": 2.0,
    "misses estimates": 2.0,
    "missed estimates": 2.0,
    "below estimates": 1.5,
    "cuts guidance": 1.8,
    "cut guidance": 1.8,
    "cuts outlook": 1.7,
    "lowered outlook": 1.7,
    "profit warning": 1.7,
    "weak demand": 1.1,
    "revenue decline": 1.1,
    "profit decline": 1.3,
    "probe": 1.2,
    "investigation": 1.2,
    "lawsuit": 1.1,
    "default": 2.0,
    "recall": 1.3,
    "offering": 0.9,
    "share dilution": 1.1,
    "price target cut": 1.4,
    "cuts price target": 1.4,
    "downgraded to sell": 1.5,
    "downgrade": 1.3,
    "underperform": 1.0,
    "underweight": 0.9,
    "sell rating": 1.0,
    "sell signal": 0.8,
    "bearish": 0.8,
    "downside": 0.65,
    "plunges": 1.0,
    "plunged": 1.0,
    "tumbles": 0.9,
    "tumbled": 0.9,
    "slumps": 0.85,
    "slumped": 0.85,
    "sinks": 0.85,
    "sank": 0.85,
    "slides": 0.70,
    "slid": 0.70,
    "falls": 0.65,
    "fell": 0.65,
    "drops": 0.65,
    "dropped": 0.65,
    "declines": 0.60,
    "declined": 0.60,
    "dips": 0.55,
    "dipped": 0.55,
    "closes below": 0.50,
    "warns investors": 0.65,
    "大幅下修": 1.8,
    "下修预期": 1.4,
    "下修預期": 1.4,
    "订单取消": 1.2,
    "訂單取消": 1.2,
    "订单减少": 1.1,
    "訂單減少": 1.1,
    "毛利率下降": 1.2,
    "利润率下降": 1.2,
    "市占率下降": 1.0,
    "裁员": 0.8,
    "裁員": 0.8,
    "数据泄露": 1.2,
    "數據洩露": 1.2,
    "网络攻击": 1.2,
    "網絡攻擊": 1.2,
    "出口限制": 1.1,
    "内部人减持": 1.0,
    "margin compression": 1.2,
    "order cancellation": 1.2,
    "orders decline": 1.1,
    "loses market share": 1.0,
    "job cuts": 0.8,
    "layoffs": 0.8,
    "data breach": 1.2,
    "cyberattack": 1.2,
    "export curbs": 1.1,
    "insider selling": 1.0,
    "bankruptcy filing": 2.0,
    "antitrust probe": 1.3,
    "worse than expected": 1.7,
    "weaker than expected": 1.7,
}

EVENT_PATTERNS = (
    ("earnings", ("财报", "业绩", "营收", "利润", "earnings", "revenue", "profit")),
    ("guidance", ("指引", "guidance", "预告", "outlook")),
    ("regulation", ("监管", "调查", "处罚", "probe", "investigation")),
    ("order", ("订单", "中标", "contract", "tender")),
    ("capital_action", ("回购", "增持", "减持", "增发", "buyback", "offering", "dividend")),
    ("capacity", ("扩产", "资本开支", "产能", "工厂", "capex", "capacity", "factory")),
    ("legal", ("诉讼", "法院", "lawsuit", "违约", "default", "court")),
    ("m_and_a", ("收购", "并购", "合并", "acquisition", "merger", "takeover")),
    ("management", ("首席执行官", "董事长", "管理层", "ceo", "cfo", "chairman")),
    ("analyst", ("评级", "目标价", "分析师", "rating", "price target", "analyst")),
    ("product", ("新品", "发布", "临床", "专利", "launch", "unveil", "product", "trial", "patent")),
)

SURPRISE_PHRASES = (
    "超预期",
    "低于预期",
    "上调业绩指引",
    "下调业绩指引",
    "beat expectations",
    "beats expectations",
    "misses expectations",
    "missed expectations",
    "beat estimates",
    "beats estimates",
    "misses estimates",
    "missed estimates",
    "raises guidance",
    "cuts guidance",
)

PRICE_ACTION_PHRASES = {
    "净值上涨", "股价上涨", "大涨", "飙升", "反弹", "净值下跌", "股价下跌", "大跌",
    "暴跌", "跳水", "走低", "surges", "surged", "soars", "soared", "jumps", "jumped",
    "rallies", "rallied", "rebounds", "rebounded", "rises", "rose", "gains", "gained",
    "closes above", "plunges", "plunged", "tumbles", "tumbled", "slumps", "slumped",
    "sinks", "sank", "slides", "slid", "falls", "fell", "drops", "dropped", "declines",
    "declined", "dips", "dipped", "closes below",
}


def assertion_status(text: str) -> str:
    cleaned = re.sub(r"\bnot only\b|不仅|不僅|不但", "", text, flags=re.I)
    if re.search(r"否认|否認|辟谣|闢謠|不实|不實|未获确认|未獲確認|未经确认|未經確認|未确认|未確認|\bden(?:y|ies|ied|ying)\b|\bunconfirmed\b", cleaned, re.I):
        return "denied_or_unconfirmed"
    if re.search(r"(?:并非|並非|不是|没有|沒有|未曾|不会|不會|不再|不|没|沒|未)(?:\s|将|將|计划|計劃|宣布|出现|出現|上调|上調|下调|下調|超|低于|低於|获|獲|亏|虧|调查|調查|增|减|減)|\b(?:not|never|no longer|did not|does not|has not|is not|isn't|wasn't|didn't|doesn't|hasn't|won't)\b", cleaned, re.I):
        return "negated"
    if re.search(r"去年|前年|此前曾|曾经|曾經|\d{4}年曾|\b(?:last year|years? ago|previously|used to)\b", cleaned, re.I):
        return "historical"
    if re.search(r"如果|假如|若|可能|或将|或將|预计|預計|\b(?:if|unless|may|might|could|expects? to|expected to|predicts?|forecasts?)\b", cleaned, re.I):
        return "conditional"
    if re.search(r"分析师|分析師|\b(?:analysts?|bullish|bearish|buy rating|sell rating|price target)\b", cleaned, re.I):
        return "opinion"
    if re.search(r'["“”]', cleaned) and not re.search(r"公告|宣布|表示|称|稱|说|說|\b(?:said|says|reports?|announc(?:es|ed))\b", cleaned, re.I):
        return "unattributed_quote"
    return "asserted"


def accepted_news_clauses(item: NewsItem, watchlist: tuple[WatchItem, ...] = ()) -> list[dict]:
    clauses = news_clauses(item, watchlist)
    for clause in clauses:
        clause["status"] = assertion_status(clause["text"]) if clause["belongs_to_target"] else "wrong_or_missing_company"
    corrections = [clause for clause in clauses if clause["field"] == "summary"
                   and clause["status"] in {"denied_or_unconfirmed", "negated", "historical"}]
    for clause in clauses:
        if clause["field"] != "title" or clause["status"] not in {"asserted", "opinion"}:
            continue
        events = {event for event, phrases in EVENT_PATTERNS if any(_contains_phrase(clause["text"].casefold(), phrase) for phrase in phrases)}
        if any(not events or any(_contains_phrase(correction["text"].casefold(), phrase)
               for event, phrases in EVENT_PATTERNS if event in events for phrase in phrases) for correction in corrections):
            clause["status"] = "contradicted_by_summary"
    return clauses


class RuleAnalyzer:
    name = "rules-v4-company-evidence"
    analysis_id = name

    def __init__(self, watchlist: tuple[WatchItem, ...] = ()):
        self.watchlist = watchlist

    def analyze(self, item: NewsItem) -> Analysis:
        clauses = accepted_news_clauses(item, self.watchlist)
        accepted = [clause for clause in clauses if clause["status"] in {"asserted", "opinion"}]
        for clause in accepted:
            clause["positive_matches"] = [phrase for phrase, _ in _find_weighted_phrases(clause["text"].casefold(), POSITIVE_PHRASES)]
            clause["negative_matches"] = [phrase for phrase, _ in _find_weighted_phrases(clause["text"].casefold(), NEGATIVE_PHRASES)]
        text = "。".join(clause["text"] for clause in accepted).casefold()
        positive = _find_weighted_phrases(text, POSITIVE_PHRASES)
        negative = _find_weighted_phrases(text, NEGATIVE_PHRASES)
        fundamental_present = any(phrase not in PRICE_ACTION_PHRASES for phrase, _ in positive + negative)
        positive_total = sum(weight * (0.25 if fundamental_present and phrase in PRICE_ACTION_PHRASES else 1) for phrase, weight in positive)
        negative_total = sum(weight * (0.25 if fundamental_present and phrase in PRICE_ACTION_PHRASES else 1) for phrase, weight in negative)
        total = positive_total + negative_total
        net = positive_total - negative_total
        direction = 0.0 if total == 0 else net / max(1.4, total * 0.78)

        event_type = "other"
        for candidate, patterns in EVENT_PATTERNS:
            if any(_contains_phrase(text, pattern) for pattern in patterns):
                event_type = candidate
                break

        matched = [phrase for phrase, _ in positive + negative]
        price_only = bool(matched) and all(phrase in PRICE_ACTION_PHRASES for phrase in matched)
        has_conflict = positive_total > 0 and negative_total > 0
        importance = min(0.95, 0.42 + min(total, 4.0) * 0.11)
        if event_type in {"earnings", "guidance", "regulation", "legal"}:
            importance = min(0.95, importance + 0.12)
        elif event_type in {"order", "capital_action", "m_and_a", "capacity"}:
            importance = min(0.92, importance + 0.07)
        if price_only:
            importance = max(0.30, importance - 0.14)
        baseline_present = bool(re.search(r"(?:预期|預期|一致预期|consensus|estimates?).{0,15}\d|\d.{0,15}\b(?:vs|versus)\b.{0,20}\d", text, re.I))
        surprise = 0.78 if baseline_present and any(_contains_phrase(text, phrase) for phrase in SURPRISE_PHRASES) else 0.0
        relevance = 0.90 if accepted else 0.0
        confidence = min(
            0.82,
            0.34 + len(matched) * 0.09 + min(total, 3.0) * 0.04,
        )
        if has_conflict:
            confidence = max(0.28, confidence - 0.12)
            if any(phrase not in PRICE_ACTION_PHRASES for phrase, _ in positive) and any(phrase not in PRICE_ACTION_PHRASES for phrase, _ in negative):
                direction *= 0.5
        if accepted and all(clause["status"] == "opinion" for clause in accepted):
            confidence = min(confidence, 0.45)
            direction *= 0.45
        if not accepted or not matched:
            confidence = 0.0
        if accepted and all(clause["attribution"] in {"source_bound_company", "continuation"} for clause in accepted):
            relevance = 0.7
            confidence = min(confidence, 0.55)

        direction = _clamp(direction, -1.0, 1.0)
        if direction > 0.04:
            tone = "偏多"
        elif direction < -0.04:
            tone = "偏空"
        else:
            tone = "中性"
        evidence = "、".join(matched[:5]) if matched else "无明确方向证据"
        strength = "强" if abs(direction) >= 0.75 else "中" if abs(direction) >= 0.35 else "弱"
        risk_flags = ["本地金融语义规则，未读取原文全文"]
        if not item.summary.strip():
            risk_flags.append("当前主要依据新闻标题")
        if price_only:
            risk_flags.append("方向词主要描述已发生的价格变化")
        if has_conflict:
            risk_flags.append("标题同时包含利多与利空线索")
        rejected = [clause for clause in clauses if clause not in accepted]
        if rejected:
            risk_flags.append("已排除非本公司、否定、历史、条件或无归属引用证据")
        if not baseline_present:
            risk_flags.append("未提供可核对的预期基准，不确认超预期")
        if any(clause["status"] == "opinion" for clause in accepted):
            risk_flags.append("包含观点或评级，不等于公司已披露事实")
        if not matched:
            risk_flags.append("本公司方向证据不足")
        impact = explain_news_impact(event_type, direction, text)

        return Analysis(
            event_type=event_type,
            direction=direction,
            importance=importance,
            surprise=surprise,
            relevance=relevance,
            confidence=confidence,
            horizon=("3m+" if re.search(r"明年|next year|long.term", text) else
                     "1-3m" if re.search(r"下季度|next quarter", text) else
                     "intraday" if price_only else _default_horizon(event_type)),
            summary=(
                f"{impact['label']}：{impact['area']}。{impact['reason']}"
                f"金融语义为{tone}（{strength}信号），方向依据：{evidence}。"
            ),
            risk_flags=tuple(risk_flags),
            analyzer=self.name,
            metadata={"evidence_status": "mixed" if has_conflict else "supported" if matched else "insufficient",
                      "direction_eligible": bool(accepted), "allow_direction_fallback": bool(accepted) and not has_conflict,
                      "evidence_text": text, "clauses": clauses, "expected_baseline_available": baseline_present,
                      "event_key": news_event_key(item.market, item.symbol, item.title, item.summary, iso_utc(item.published_at)),
                      "score_kind": "uncalibrated_research_signal", "published_at": iso_utc(item.published_at)},
        )


ANALYSIS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "event_type": {
            "type": "string",
            "enum": [
                "earnings",
                "guidance",
                "regulation",
                "order",
                "capital_action",
                "capacity",
                "legal",
                "management",
                "m_and_a",
                "analyst",
                "product",
                "other",
            ],
        },
        "direction": {"type": "number", "minimum": -1, "maximum": 1},
        "importance": {"type": "number", "minimum": 0, "maximum": 1},
        "surprise": {"type": "number", "minimum": 0, "maximum": 1},
        "relevance": {"type": "number", "minimum": 0, "maximum": 1},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "horizon": {
            "type": "string",
            "enum": ["intraday", "1-5d", "5-20d", "1-3m", "3m+"],
        },
        "summary": {"type": "string"},
        "risk_flags": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "event_type",
        "direction",
        "importance",
        "surprise",
        "relevance",
        "confidence",
        "horizon",
        "summary",
        "risk_flags",
    ],
}


class OpenAIAnalyzer:
    name = "openai"

    def __init__(self, model: str):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError('未安装 OpenAI SDK，请运行: python -m pip install -e ".[ai]"') from exc
        self.model = model
        self.analysis_id = f"openai:{model}"
        self.client = OpenAI()

    def analyze(self, item: NewsItem) -> Analysis:
        response = self.client.responses.create(
            model=self.model,
            store=False,
            input=[
                {
                    "role": "system",
                    "content": (
                        "你是股票新闻事件分类器。判断事件对指定股票未来超额收益的方向，"
                        "不是判断文字语气。只使用提供的内容，不补充未经提供的事实。"
                        "重点考虑相对市场预期、基本面传导、是否可能已被定价和影响期限。"
                        "只有标题时应降低 confidence；信息矛盾时降低 direction 的绝对值。"
                        "summary 使用简洁中文，不提供买卖建议。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "market": item.market,
                            "symbol": item.symbol,
                            "source": item.source,
                            "title": item.title,
                            "summary": item.summary,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "equity_news_impact",
                    "strict": True,
                    "schema": ANALYSIS_SCHEMA,
                }
            },
        )
        raw = json.loads(response.output_text)
        return Analysis(
            event_type=str(raw["event_type"]),
            direction=_clamp(float(raw["direction"]), -1.0, 1.0),
            importance=_clamp(float(raw["importance"]), 0.0, 1.0),
            surprise=_clamp(float(raw["surprise"]), 0.0, 1.0),
            relevance=_clamp(float(raw["relevance"]), 0.0, 1.0),
            confidence=_clamp(float(raw["confidence"]), 0.0, 1.0),
            horizon=str(raw["horizon"]),
            summary=str(raw["summary"]),
            risk_flags=tuple(str(value) for value in raw["risk_flags"]),
            analyzer=f"openai:{self.model}",
            metadata={"returned_model":getattr(response,"model",self.model),
                      "response_id":getattr(response,"id",None), "raw_output":response.output_text},
        )


def select_analyzer(mode: str, model: str, *, watchlist: tuple[WatchItem, ...] = ()) -> tuple[Analyzer, str]:
    if mode == "rules":
        return RuleAnalyzer(watchlist), "使用本地规则分析"
    if mode == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("analysis.mode=openai，但没有设置 OPENAI_API_KEY")
        return OpenAIAnalyzer(model), f"使用 OpenAI 模型 {model}"
    if os.environ.get("OPENAI_API_KEY"):
        try:
            return OpenAIAnalyzer(model), f"检测到 API Key，使用 OpenAI 模型 {model}"
        except RuntimeError as exc:
            return RuleAnalyzer(watchlist), f"{exc}；已回退到本地规则"
    return RuleAnalyzer(watchlist), "未设置 OPENAI_API_KEY，使用本地规则分析"


def _find_weighted_phrases(
    text: str,
    phrases: dict[str, float],
) -> list[tuple[str, float]]:
    matched: list[tuple[str, float]] = []
    for phrase, weight in sorted(
        phrases.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if any(phrase in longer for longer, _ in matched):
            continue
        if _contains_phrase(text, phrase):
            matched.append((phrase, weight))
    return matched


def _contains_phrase(text: str, phrase: str) -> bool:
    if any(character.isascii() and character.isalnum() for character in phrase):
        escaped = re.escape(phrase).replace(r"\ ", r"\s+")
        return re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", text) is not None
    return phrase in text


def _default_horizon(event_type: str) -> str:
    if event_type in {"earnings", "guidance", "order"}:
        return "5-20d"
    if event_type in {"capacity", "m_and_a"}:
        return "1-3m"
    if event_type in {"regulation", "legal"}:
        return "1-5d"
    return "1-5d"


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))

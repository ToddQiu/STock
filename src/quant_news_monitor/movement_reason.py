"""Evidence-limited movement explanations, shared by fresh scans and old records."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from functools import lru_cache

from .analyzers import RuleAnalyzer, accepted_news_clauses, PRICE_ACTION_PHRASES
from .models import Analysis, NewsItem, parse_datetime
from .news_impact import explain_news_impact, price_target_revision

REASON_VERSION = 3


def company_search_name(name: str) -> str:
    return re.sub(r"(?:,?\s+(?:inc\.?|corp\.?|corporation|ltd\.?|limited|holdings))+$", "", name, flags=re.I).strip()


def movement_reason(item: NewsItem, analysis: Analysis, change_pct: float, detected_at) -> dict:
    result = {"version": REASON_VERSION, "status": "unconfirmed", "summary": "原因未核实", "facts": [],
              "limitation": "现有证据不足，暂不提供归因。", "relation": "unknown"}
    if not re.match(r"^https?://", item.url, re.I) or not item.source:
        return result
    if not detected_at - timedelta(hours=72) <= item.published_at <= detected_at:
        return result
    clauses = analysis.metadata.get("clauses") or accepted_news_clauses(item)
    accepted = [row for row in clauses if row.get("belongs_to_target") and row.get("status") in {"asserted", "opinion"}]
    if not any(row.get("attribution") == "explicit" for row in accepted):
        return result
    if analysis.metadata.get("direction_eligible") is False and analysis.metadata.get("evidence_status") != "mixed":
        return result
    concrete = []
    revisions = []
    for row in accepted:
        text = row["text"]
        pair = price_target_revision(text)
        matches = row.get("positive_matches", []) + row.get("negative_matches", [])
        fundamental = [word for word in matches if word not in PRICE_ACTION_PHRASES and word not in {"outperform", "overweight", "underperform", "underweight", "buy rating", "sell rating", "bullish", "bearish", "upside"}]
        # An event-specific amount is usable context; a price percentage alone is not.
        concrete_event = re.search(r"(?:\b(?:contract|agreement|deal|order|revenue|profit|eps|guidance|dividend|buyback|offering)\b|订单|合同|协议|營收|营收|净利润|指引|分红|回购|增发).{0,50}(?:\$\s*\d|\d+(?:\.\d+)?\s*(?:million|billion|mn|bn|万元|亿元|美元))", text, re.I)
        if pair or fundamental and analysis.event_type != "other" or concrete_event:
            if text not in concrete:
                concrete.append(text)
            if pair:
                if pair not in revisions:
                    revisions.append(pair)
    if not concrete:
        return result
    if len(revisions) > 1:
        result["limitation"] = "存在不同目标价修订口径，需核对各机构与发布时间，暂不合并归因。"
        return result
    revision = revisions[0] if revisions else None
    direction = analysis.direction
    category = analysis.event_type
    if revision:
        direction = (revision["new"] > revision["old"]) - (revision["new"] < revision["old"])
        unit = revision["unit"]
        delta = revision["change_pct"]
        facts = f"机构目标价由 {unit}{revision['old']:g} 调整为 {unit}{revision['new']:g}（{delta:+.2f}%，目标价变动，非股价涨跌）。"
        unchanged_rating = r"\bmaintains?\s+(?:(?:its|an?|the)\s+)?(?:outperform|underperform|overweight|underweight|equal[- ]weight|buy|sell|hold|neutral)\s+rating\b|(?:维持|維持)[\s\"'“”]*(?:原|买入|買入|增持|持有|中性|卖出|賣出|跑赢大盘|跑贏大盤)[\s\"'“”]*(?:评级|評級)"
        if any(re.search(unchanged_rating, row["text"], re.I) for row in accepted):
            facts += "同时维持原评级，不代表评级上调。"
        category = "analyst"
        mechanism = "目标价下调反映该机构估值判断转弱，可能影响市场预期。" if direction < 0 else "目标价上调反映该机构估值判断转强，可能影响市场预期。" if direction > 0 else "目标价未变，没有形成新的估值修订信号。"
    else:
        facts = ""
        mechanism = explain_news_impact(category, direction, "。".join(concrete))["reason"]
    relation = "aligned" if direction * change_pct > 0 else "opposed" if direction * change_pct < 0 else "unknown"
    mixed = analysis.metadata.get("evidence_status") == "mixed"
    if mixed:
        relation = "mixed"
        mechanism = "报道包含相反线索，净影响尚不明确，不能据此解释单一涨跌方向。"
    relation_text = {"aligned":"该线索与当前时段涨跌方向相同，但尚未验证因果。", "opposed":"该线索与当前时段涨跌方向相反，不能作为本次异动的充分解释。", "unknown":"该线索的方向尚不明确，不能据此解释本次涨跌。", "mixed":"需分别核对正反证据。"}[relation]
    result.update(status="linked", category=category, facts=[{"text":text} for text in concrete],
                  fact_summary=facts, summary=mechanism, relation=relation, relation_text=relation_text,
                  limitation="依据公开标题与摘要，未核验全文；发布时间不等于事件发生时间，不能证明这条消息导致了该时段全部涨跌。")
    return result


@lru_cache(maxsize=512)
def legacy_movement_reason(market, symbol, name, title, url, source, published_at, detected_at, change_pct, summary=""):
    """Reassess stored headlines once; never repeat legacy causal boilerplate."""
    if not title or not published_at or not detected_at:
        return {}
    try:
        for value in (published_at, detected_at):
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        detected = parse_datetime(detected_at)
        item = NewsItem(market, symbol, title, summary, source, url, parse_datetime(published_at),
                        target_name=name, target_aliases=(company_search_name(name),))
        return movement_reason(item, RuleAnalyzer().analyze(item), change_pct, detected)
    except (ValueError, TypeError, OverflowError):
        return {}

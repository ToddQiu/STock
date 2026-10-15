from __future__ import annotations

import re
from typing import Any


IMPACT_AREAS = {
    "earnings": "盈利预期",
    "guidance": "未来业绩预期",
    "order": "收入可见性",
    "capital_action": "股东回报与股本供给",
    "capacity": "中长期供需与现金流",
    "regulation": "经营许可与合规成本",
    "legal": "潜在损失与风险溢价",
    "m_and_a": "增长预期与整合风险",
    "management": "经营执行与稳定性",
    "analyst": "估值锚与资金预期",
    "product": "收入增长与竞争力",
    "other": "市场预期",
}

POSITIVE_REASONS = {
    "earnings": "营收、利润或经营表现改善，可能推动盈利预测上修。",
    "guidance": "公司对未来经营更乐观，可能抬高市场的一致预期。",
    "order": "新增订单提高未来收入的确定性，并改善业务能见度。",
    "capital_action": "资本动作可能改善每股价值、股东回报或市场信心。",
    "capacity": "新增产能或投资可能打开中长期增长空间，但仍需观察资本开支回报。",
    "regulation": "审批或监管进展降低经营障碍，并减少政策不确定性。",
    "legal": "法律风险缓解可减少潜在损失，并降低风险折价。",
    "m_and_a": "交易可能扩展业务规模或协同空间，但需要验证整合效果。",
    "management": "管理层稳定或执行力改善，有助于提升经营预期。",
    "analyst": "评级或目标价改善，可能抬升估值锚并吸引增量资金。",
    "product": "新品、研发或审批进展可能增强竞争力并带来新增收入。",
    "other": "正面信息可能改善风险偏好，但尚未形成明确的盈利传导证据。",
}

NEGATIVE_REASONS = {
    "earnings": "营收、利润或经营表现承压，可能导致盈利预测下修。",
    "guidance": "公司对未来经营更谨慎，可能压低市场的一致预期。",
    "order": "订单走弱会降低未来收入能见度，并增加业绩兑现风险。",
    "capital_action": "融资、减持或稀释风险可能压低每股价值与资金承接预期。",
    "capacity": "高资本开支或供给扩张可能压制价格和自由现金流。",
    "regulation": "监管调查或限制可能增加合规成本并影响业务开展。",
    "legal": "诉讼或违约风险可能带来损失，并提高市场要求的风险溢价。",
    "m_and_a": "交易成本或整合不确定性可能拖累盈利与估值。",
    "management": "管理层变动或执行不确定性可能削弱经营信心。",
    "analyst": "评级或目标价下调可能压低估值锚并引发资金流出。",
    "product": "产品、研发或审批受阻可能延后收入并削弱竞争力。",
    "other": "负面信息可能压低风险偏好，但尚未形成明确的盈利传导证据。",
}

PRICE_UP_TERMS = (
    "surge", "surges", "surged", "soar", "soars", "soared", "jump", "jumps",
    "jumped", "rally", "rallies", "rallied", "rebound", "rebounds", "rebounded",
    "rise", "rises", "rose", "gain", "gains", "gained",
    "closes above", "上涨", "大涨", "飙升", "反弹",
)
PRICE_DOWN_TERMS = (
    "plunge", "plunges", "plunged", "tumble", "tumbles", "tumbled", "slump",
    "slumps", "slumped", "sink", "sinks", "sank", "slide", "slides", "slid",
    "fall", "falls", "fell", "drop", "drops", "dropped", "decline", "declines",
    "declined", "dip", "dips", "dipped", "closes below", "下跌", "大跌", "暴跌", "走低",
)


_PRICE_MOVE_RE = re.compile(
    r"(?<![a-z])(?:" + "|".join(re.escape(term).replace(r"\ ", r"\s+")
                                for term in (*PRICE_UP_TERMS, *PRICE_DOWN_TERMS) if term.isascii()) + r")(?![a-z])|"
    + "|".join(re.escape(term) for term in (*PRICE_UP_TERMS, *PRICE_DOWN_TERMS) if not term.isascii())
)


def explain_news_impact(
    event_type: str,
    direction: float,
    text: str = "",
) -> dict[str, Any]:
    normalized_event = event_type if event_type in IMPACT_AREAS else "other"
    normalized_text = text.casefold()
    tone = "bullish" if direction > 0.04 else "bearish" if direction < -0.04 else "neutral"

    if normalized_event == "capital_action":
        area, positive, negative = _capital_action_impact(normalized_text)
    else:
        area = IMPACT_AREAS[normalized_event]
        positive = POSITIVE_REASONS[normalized_event]
        negative = NEGATIVE_REASONS[normalized_event]

    price_only = _PRICE_MOVE_RE.search(normalized_text) is not None
    if price_only:
        area = "短线资金与动量"
        positive = "价格强势反映买盘和动量增强，但不等同于基本面已经改善。"
        negative = "价格走弱反映卖压和风险偏好下降，但不等同于基本面已经恶化。"
        if normalized_event != "other":
            area = "价格反应与事件预期"
            positive = "报道包含价格走强与事件线索；经营影响仍需结合具体业绩、指引或公告确认。"
            negative = "报道包含价格走弱与事件线索；不能仅凭下跌推断盈利下滑或基本面恶化。"

    if tone == "bullish":
        return {
            "tone": tone,
            "label": "利多影响",
            "area": area,
            "reason": positive,
        }
    if tone == "bearish":
        return {
            "tone": tone,
            "label": "利空影响",
            "area": area,
            "reason": negative,
        }
    return {
        "tone": tone,
        "label": "影响判断",
        "area": area if normalized_event != "other" else "待确认",
        "reason": "现有标题与摘要缺少足够的方向证据，暂不能确认对盈利、估值或资金面的净影响。",
    }


def _capital_action_impact(text: str) -> tuple[str, str, str]:
    if any(term in text for term in ("回购", "buyback", "repurchase")):
        return (
            "每股价值与市场信心",
            "回购减少流通股本并释放管理层信心，可能改善每股价值预期。",
            "若回购规模或执行力度低于预期，市场信心可能回落。",
        )
    if any(term in text for term in ("增发", "配股", "offering", "placement", "dilution")):
        return (
            "股权稀释与资金供给",
            "融资增强公司资金实力，但利多程度取决于募资回报能否覆盖稀释。",
            "新增股份或潜在稀释提高股票供给，可能压低每股价值。",
        )
    if any(term in text for term in ("分红", "股息", "dividend", "payout")):
        return (
            "股东现金回报",
            "现金回报提升可能增强持有吸引力，并改善股东回报预期。",
            "分红不及预期可能削弱收益吸引力与市场信心。",
        )
    return (
        IMPACT_AREAS["capital_action"],
        POSITIVE_REASONS["capital_action"],
        NEGATIVE_REASONS["capital_action"],
    )

from __future__ import annotations

import re


# These are evidence rules, not a calibrated prediction model.
RULES = (
    (1, "方向表态", r"看多|看涨|看漲|利多|利好|\bbullish\b|\bto the moon\b"),
    (-1, "方向表态", r"看空|看跌|利空|\bbearish\b"),
    (1, "买入意向", r"买入|買入|加仓|加倉|增持|抄底|\b(?:buy|buying|bought|accumulat(?:e|ed|ing))\b"),
    (-1, "卖出意向", r"卖出|賣出|减仓|減倉|减持|減持|清仓|清倉|割肉|\b(?:sell|selling|sold|dump(?:ing|ed)?)\b"),
    (1, "估值判断", r"低估|估值.{0,6}(?:便宜|偏低|合理)|\bundervalued\b|\b(?:looks?|seems?|is|are)\s+(?:(?:very|too|still|so|really)\s+)*cheap\b"),
    (-1, "估值判断", r"高估|估值.{0,6}(?:太贵|太貴|过高|過高|偏高)|\bovervalued\b|\b(?:looks?|seems?|is|are)\s+(?:(?:very|too|still|so|really)\s+)*(?:expensive|overpriced)\b"),
    (1, "仓位增加", r"\b(?:add(?:ed|ing)?|increas(?:e|ed|ing))\s+(?:to\s+)?(?:my\s+)?(?:position|stake|holdings?|shares?)\b|\b(?:opened|opening)\s+(?:a\s+)?long\b|\b(?:am|I'm|going|stay|staying|remain)\s+long\b"),
    (-1, "仓位减少", r"\b(?:trim(?:med|ming)?|reduc(?:e|ed|ing)|clos(?:e|ed|ing)|exit(?:ed|ing)?)\s+(?:my\s+)?(?:position|stake|holdings?|shares?)\b|\b(?:am|I'm|going|opened|opening)\s+(?:a\s+)?short\b|\bshorting\b"),
    (1, "上涨预期", r"(?:预计|預計|认为|認為|我觉得|我覺得).{0,35}(?:会涨|會漲|上涨|上漲|跑赢|跑贏)|\b(?:will|should|expect(?:ed)?\s+to)\s+(?:rise|rally|outperform|double|go\s+up|be\s+(?:a\s+)?winners?)\b"),
    (-1, "下跌预期", r"(?:预计|預計|认为|認為|我觉得|我覺得).{0,35}(?:会跌|會跌|下跌|跑输|跑輸)|\b(?:will|should|expect(?:ed)?\s+to)\s+(?:fall|crash|underperform|collapse|go\s+down)\b"),
)
COMPILED_RULES = [(direction, kind, re.compile(pattern, re.I)) for direction, kind, pattern in RULES]
DRIVERS = (
    ("估值", r"估值|低估|高估|便宜|\b(?:valuation|undervalued|overvalued|cheap|expensive|multiple|p/e)\b"),
    ("业绩", r"业绩|業績|盈利|利润|利潤|营收|營收|财报|財報|\b(?:earnings|revenue|profit|margin|guidance)\b"),
    ("需求", r"需求|订单|訂單|\b(?:demand|orders|backlog)\b"),
    ("政策", r"关税|關稅|政策|监管|監管|制裁|\b(?:tariff|regulation|sanctions|policy)\b"),
    ("技术走势", r"突破|支撑位|支撐位|阻力位|均线|均線|\b(?:breakout|moving average|(?:support|resistance)\s+(?:level|at|near))\b"),
)
NEGATION = re.compile(r"(?:不再|并非|並非|不是|不太|不|没|沒|别|別|不要|无法|無法|not|never|no longer|don['’]t|do not|didn['’]t|isn['’]t|aren['’]t|wouldn['’]t|won['’]t|can['’]t)(?:\s+(?:really|very|particularly|necessarily|so|too))?\s*$", re.I)


def analyze_opinion(text: str) -> dict:
    signals = []
    # Preserve question and sentence boundaries; quoted material cannot be an author's own stance.
    body = re.sub(r'"[^"\n]*"|“[^”\n]*”', " ", text)
    body = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith(">"))
    for part in re.split(r"(?<=[。！？!?])|\n|[;；]|\.+\s+", body):
        clause = part.strip()
        if not clause or clause.endswith(("?", "？", "吗", "嗎", "么", "麼")):
            continue
        if re.search(r"\b(?:if|unless|whether|used to|years? ago|last year|last \d+ years?|analysts? (?:say|rate|rated)|someone said)\b|\b(?:don['’]t|do not|didn['’]t)\s+(?:think|believe|expect)\b|如果|假如|是否|去年|前年|不认为|不認為|别人说|別人說|分析师称|分析師稱", clause, re.I):
            continue
        if re.search(r"\b(?:not financial advice|no position)\b", clause, re.I):
            # Disclaimers alone do not invalidate an otherwise explicit view.
            clause = re.sub(r"\b(?:not financial advice|no position)\b", "", clause, flags=re.I)
        if re.search(r"(?:guaranteed|保证|保證).{0,20}(?:profit|return|收益)|(?:telegram|whatsapp).{0,30}(?:join|contact|signal)|(?:join|contact).{0,30}(?:telegram|whatsapp)", clause, re.I):
            continue
        occupied = set()
        for direction, kind, pattern in COMPILED_RULES:
            for signal in pattern.finditer(clause):
                prefix = clause[max(0, signal.start()-45):signal.start()]
                suffix = clause[signal.end():signal.end()+45]
                # "Not bullish" is NOT evidence of a bearish view, and vice versa.
                if NEGATION.search(prefix):
                    continue
                if kind in {"买入意向", "卖出意向"} and re.search(r"\b(?:puts?|calls?|cover(?:ing)?)\b|对冲|對沖|回补|回補", clause, re.I):
                    # An option trade or closing a hedge alone does not establish stock conviction.
                    continue
                if kind in {"买入意向", "卖出意向"} and re.search(r"\b(?:company|corporation|announced|announces|reports?|reported)\b|公司宣布|公告|新闻称|新聞稱", clause, re.I):
                    continue
                if kind in {"买入意向", "卖出意向"} and re.match(r"\s+(?:an?\s+|the\s+|more\s+)?(?:phones?|laptops?|products?|ads?|advertising|services?|cars?|chips?|data)\b", suffix, re.I):
                    continue
                span = set(range(signal.start(), signal.end()))
                if occupied.intersection(span):
                    continue
                occupied.update(span)
                signals.append({"direction": direction, "kind": kind, "evidence": signal.group(),
                                "excerpt": clause[:400],
                                "drivers": [label for label, regex in DRIVERS if re.search(regex, clause, re.I)]})
    positive = sum(s["direction"] > 0 for s in signals)
    negative = sum(s["direction"] < 0 for s in signals)
    mixed = bool(positive and negative)
    score = (positive-negative)/max(2, positive+negative) * (.5 if mixed else 1)
    return {"score": round(score, 3), "label": "分歧" if mixed else "看多" if score > 0 else "看空" if score < 0 else "未表态",
            "evidence": list(dict.fromkeys(s["evidence"] for s in signals)), "signals": signals,
            "confidence": .35 if mixed else .65 if signals else 0,
            "method": "明确观点规则 v3（非大模型）"}

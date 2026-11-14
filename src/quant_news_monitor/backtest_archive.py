"""Private, portfolio-scoped input snapshots; never accept client profit claims."""
import json
import math
import re
import uuid
from datetime import date, datetime, timedelta, timezone

VERSION = "stock-daily-v1"


def validate(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get("engine_version") != VERSION:
        raise ValueError("回测内核版本不匹配")
    try:
        encoded = json.dumps(snapshot, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 500000:
            raise ValueError("回测快照超过500KB，请缩短区间")
        created = datetime.fromisoformat(snapshot["created_at"].replace("Z", "+00:00"))
        if created.tzinfo is None:
            raise ValueError("快照时间缺少时区")
        created = created.astimezone(timezone.utc)
        if created > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("快照时间来自未来")
        opts = snapshot["options"]
        if opts["strategy"] not in {"buy_hold", "sma", "breakout", "frozen"}:
            raise ValueError("策略无效")
        for field in ("target", "benchmark"):
            value = opts.get(field, "")
            if (field == "target" or value) and not re.fullmatch(r"(US|HK|CN|KR):[A-Z0-9^][A-Z0-9.^=\-]{0,24}", value):
                raise ValueError("标的格式无效")
        bounds = {"capital": (1, 1e12), "allocation": (1, 100), "fee_bps": (0, 1000), "slippage_bps": (0, 1000),
                  "sell_tax_bps": (0, 1000), "fast": (2, 250), "slow": (3, 500), "breakout": (2, 250),
                  "exit_window": (2, 250), "stop_loss": (0, 90), "hold_days": (1, 365)}
        for key, (low, high) in bounds.items():
            value = opts[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError("回测参数超出范围：" + key)
            if key in {"fast", "slow", "breakout", "exit_window", "hold_days"} and value != int(value):
                raise ValueError("周期必须为整数")
        if opts["strategy"] == "sma" and opts["fast"] >= opts["slow"]:
            raise ValueError("短均线必须小于长均线")
        if opts["price_mode"] not in {"raw", "adjusted"} or not isinstance(opts["liquidate"], bool):
            raise ValueError("价格或结算口径无效")
        for key in ("start", "end"):
            if opts.get(key):
                date.fromisoformat(opts[key])
        if opts.get("start") and opts.get("end") and opts["start"] > opts["end"]:
            raise ValueError("开始日期晚于结束日期")
        if not isinstance(snapshot["history"], dict):
            raise ValueError("主标的历史行情缺失")
        for history in (snapshot["history"], snapshot.get("benchmark_history")):
            if history is None:
                continue
            if history["currency"] not in {"USD", "CNY", "HKD", "KRW"} or not isinstance(history["source"], str) or not history["source"].strip():
                raise ValueError("行情币种或来源缺失")
            points = history["points"]
            if not isinstance(points, list) or not 3 <= len(points) <= 2000:
                raise ValueError("日线数量超出范围")
            seen = set()
            for point in points:
                day = date.fromisoformat(point["time"][:10])
                if day >= created.date() or day in seen:
                    raise ValueError("日线重复、未完成或来自未来")
                seen.add(day)
                for key in ("close", "adjusted_close") if opts["price_mode"] == "adjusted" else ("close",):
                    value = point[key]
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 1e12:
                        raise ValueError("历史价格无效")
        records = snapshot.get("records", [])
        if not isinstance(records, list) or len(records) > 200:
            raise ValueError("冻结记录数量无效")
        for record in records:
            stamp = datetime.fromisoformat(record["created_at"].replace("Z", "+00:00"))
            if stamp.tzinfo is None or isinstance(record["direction"], bool) or record["direction"] not in (-1, 0, 1):
                raise ValueError("冻结记录时间或方向无效")
        model = snapshot.get("attention_model")
        if model is not None:
            if (not isinstance(model, dict) or model.get("version") not in {"attention-v2", "share-z-v1"}
                    or model.get("origin") != "historical_rule_reconstruction"
                    or not isinstance(model.get("rows"), list) or not 1 <= len(model["rows"]) <= 220
                    or model.get("threshold") != 1.5 or model.get("purge_days") != 7):
                raise ValueError("热度算法快照缺少受支持的版本或原始样本")
            for row in model["rows"]:
                if not isinstance(row, dict) or row.get("target") != opts["target"] or row.get("platform") != model.get("platform"):
                    raise ValueError("热度样本与回测标的不匹配")
                if not row.get("source") or not row.get("coverage"):
                    raise ValueError("热度样本缺少来源或覆盖口径")
                for key in ("count", "total"):
                    if isinstance(row.get(key), bool) or not isinstance(row.get(key), (int, float)) or not math.isfinite(row[key]):
                        raise ValueError("热度计数无效")
                if not 0 <= row["count"] <= row["total"] or row["total"] <= 0:
                    raise ValueError("热度样本分母无效")
                date.fromisoformat(row["date"])
                observed = datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00"))
                if observed.tzinfo is None or observed > created:
                    raise ValueError("热度样本观察时间无效")
    except (KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise ValueError("回测快照格式不完整") from exc
    # Keep only reproducible inputs, never the submitted summary or return figures.
    result = {key: snapshot.get(key) for key in ("engine_version", "created_at", "options", "history", "benchmark_history", "records")}
    if snapshot.get("attention_model") is not None:
        result["attention_model"] = snapshot["attention_model"]
    return result


def save(research, payload):
    name = payload.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValueError("回测名称须为1至80字")
    data = {"name": name.strip(), "snapshot": validate(payload.get("snapshot"))}
    row = research.ledger.observe(research.kind("portfolio_backtest"), uuid.uuid4().hex, data)
    return {"id": row["id"], "sha256": row["sha256"], "observed_at": row["observed_at"]}


def read(research, identifier=None):
    kind = research.kind("portfolio_backtest")
    with research.ledger.session() as db:
        if identifier is not None:
            row = db.execute("SELECT payload,sha256,observed_at FROM input_versions WHERE id=? AND kind=?", (int(identifier), kind)).fetchone()
            if not row:
                raise ValueError("此组合中没有这份回测")
            return {**json.loads(row["payload"]), "sha256": row["sha256"], "observed_at": row["observed_at"]}
        rows = db.execute("""SELECT id,observed_at,json_extract(payload,'$.name') AS name,
            json_extract(payload,'$.snapshot.options.target') AS target,
            json_extract(payload,'$.snapshot.options.strategy') AS strategy
            FROM input_versions WHERE kind=? ORDER BY id DESC LIMIT 50""", (kind,)).fetchall()
    return {"items": [dict(row) for row in rows]}

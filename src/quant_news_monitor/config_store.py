from __future__ import annotations

import json
import math
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .portfolio_book import get_portfolio, portfolios, validate_name
from . import portfolio_dividends
from . import portfolio_cash


class ConfigStore:
    _locks: dict[Path, Any] = {}
    _locks_guard = threading.Lock()

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        with self._locks_guard:
            self._lock = self._locks.setdefault(self.path, threading.RLock())

    def set_value(self, key: str, value: Any) -> None:
        with self._lock:
            data = self.read()
            data[key] = value
            self.write(data)

    def read(self) -> dict[str, Any]:
        with self._lock:
            with self.path.open("r", encoding="utf-8") as handle:
                return json.load(handle)

    def write(self, data: dict[str, Any]) -> None:
        with self._lock:
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            for attempt in range(5):
                try:
                    os.replace(temporary, self.path)
                    break
                except PermissionError as error:
                    # Windows readers/indexers can briefly hold the destination after closing it.
                    if getattr(error, "winerror", None) not in {5, 32, 33} or attempt == 4:
                        raise
                    time.sleep(0.05 * 2 ** attempt)

    def add_watch_item(self, item: dict[str, Any], *, replace: tuple[str, str] | None = None) -> dict[str, Any]:
        market = str(item.get("market", "")).strip().upper()
        symbol = str(item.get("symbol", "")).strip().upper()
        name = str(item.get("name", "")).strip()
        if market not in {"CN", "HK", "US", "KR"}:
            raise ValueError("市场必须是 CN、HK、KR 或 US")
        if not symbol or not name:
            raise ValueError("股票代码和公司名称不能为空")
        from .security_master import normalize
        market, symbol = normalize(market, symbol)

        query = str(item.get("query") or f"{name} {symbol}").strip()
        raw_aliases = item.get("aliases", [])
        if isinstance(raw_aliases, str):
            raw_aliases = [value.strip() for value in raw_aliases.split(",")]
        aliases = [str(value).strip() for value in raw_aliases if str(value).strip()]

        with self._lock:
            data = self.read()
            watchlist = data.setdefault("watchlist", [])
            previous = None
            if replace is not None:
                previous = next((row for row in watchlist if
                    (str(row.get("market", "")).upper(), str(row.get("symbol", "")).upper()) == replace), None)
                if previous is None:
                    raise ValueError("原股票已不存在，请刷新股票池")
            if any(
                existing is not previous and normalize(existing.get("market", ""), existing.get("symbol", "")) == (market, symbol)
                for existing in watchlist
            ):
                raise ValueError(f"{market}:{symbol} 已在股票池中")
            normalized = {
                "market": market,
                "symbol": symbol,
                "name": name,
                "query": query,
                "aliases": aliases,
            }
            if previous is None:
                watchlist.append(normalized)
            else:
                if normalize(previous["market"], previous["symbol"]) == (market, symbol):
                    normalized = {**previous, **normalized, "query": previous.get("query") or query,
                                  "aliases": previous.get("aliases", [])}
                watchlist[watchlist.index(previous)] = normalized
            self.write(data)
            return normalized

    def remove_watch_item(self, market: str, symbol: str) -> bool:
        market = market.upper()
        symbol = symbol.upper()
        with self._lock:
            data = self.read()
            watchlist = data.get("watchlist", [])
            kept = [
                item
                for item in watchlist
                if not (
                    str(item.get("market", "")).upper() == market
                    and str(item.get("symbol", "")).upper() == symbol
                )
            ]
            if len(kept) == len(watchlist):
                return False
            if not kept:
                raise ValueError("股票池至少保留一只股票")
            data["watchlist"] = kept
            self.write(data)
            return True

    def save_stock_whitelist(self, item: dict[str, Any]) -> dict[str, Any]:
        from .security_master import normalize
        from .stock_profile import normalize_profile
        profile = normalize_profile(item.get('profile', {}))
        market, symbol = normalize(str(item.get("market", "")), str(item.get("symbol", "")))
        if market not in {"CN", "HK", "US", "KR"}:
            raise ValueError("白名单市场必须是 CN、HK、KR 或 US")
        action = item.get("action", "add")
        if action not in {"add", "update"}:
            raise ValueError("不支持的白名单操作")
        notes = {}
        for field, label in (("analysis", "分析"), ("reason", "加入理由")):
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"请填写{label}")
            if len(value.strip()) > 2000:
                raise ValueError(f"{label}最多2000字")
            notes[field] = value.strip()
        with self._lock:
            data = self.read()
            rows = data.setdefault("stock_whitelist", [])
            previous = next((row for row in rows if (row["market"], row["symbol"]) == (market, symbol)), None)
            if action == "add" and previous:
                raise ValueError(f"{market}:{symbol} 已在白名单中，可编辑已有记录")
            if action == "update" and previous is None:
                raise ValueError("白名单记录已不存在，请刷新后重试")
            name = previous["name"] if previous else str(item.get("name") or "").strip()
            if not name:
                raise ValueError("请先识别证券名称")
            now = datetime.now(timezone.utc).isoformat()
            saved = {**(previous or {}), "market": market, "symbol": symbol, "name": name, **notes,
                     "profile": {**((previous or {}).get('profile') or {}), **profile},
                     "created_at": previous["created_at"] if previous else now, "updated_at": now}
            if previous is None:
                rows.append(saved)
            else:
                rows[rows.index(previous)] = saved
            watches = data.setdefault("watchlist", [])
            if not any(normalize(row["market"], row["symbol"]) == (market, symbol) for row in watches):
                watches.append({"market": market, "symbol": symbol, "name": name,
                                "query": f"{name} {symbol}", "aliases": []})
            self.write(data)
            return saved

    def remove_stock_whitelist(self, market: str, symbol: str) -> bool:
        from .security_master import normalize
        market, symbol = normalize(market, symbol)
        with self._lock:
            data = self.read()
            rows = data.get("stock_whitelist", [])
            kept = [row for row in rows if (row["market"], row["symbol"]) != (market, symbol)]
            if len(kept) == len(rows):
                return False
            data["stock_whitelist"] = kept
            self.write(data)
            return True

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self.read()
            if "poll_interval_minutes" in values:
                data["poll_interval_minutes"] = _bounded_int(
                    values["poll_interval_minutes"], 1, 1440, "轮询间隔"
                )
                data["poll_interval_seconds"] = data["poll_interval_minutes"] * 60
            if "poll_interval_seconds" in values:
                data["poll_interval_seconds"] = _bounded_int(values["poll_interval_seconds"], 1, 86400, "新闻检测间隔（秒）")
            if "lookback_hours" in values:
                data["lookback_hours"] = _bounded_int(
                    values["lookback_hours"], 1, 24 * 365, "新闻回看时间"
                )
            if "max_items_per_asset" in values:
                data["max_items_per_asset"] = _bounded_int(values["max_items_per_asset"],1,100,"每标的每来源候选数")
            if "asset_news_interval_seconds" in values:
                data["asset_news_interval_seconds"] = _bounded_int(values["asset_news_interval_seconds"],1,300,"跨资产新闻间隔（秒）")
            if "alert_threshold" in values:
                threshold = float(values["alert_threshold"])
                if not 0.05 <= threshold <= 1.0:
                    raise ValueError("告警阈值必须在 0.05 到 1.00 之间")
                data["alert_threshold"] = round(threshold, 2)
            if "analysis_mode" in values:
                mode = str(values["analysis_mode"]).lower()
                if mode not in {"auto", "rules", "openai"}:
                    raise ValueError("分析模式无效")
                data.setdefault("analysis", {})["mode"] = mode
            for key in ("news_translation_enabled", "fundamentals_enabled"):
                if key in values:
                    if not isinstance(values[key], bool):
                        raise ValueError("功能开关须为布尔值")
                    data[key] = values[key]
            if "movement_scope" in values:
                if values["movement_scope"] not in {"portfolio", "all"}:
                    raise ValueError("异动范围必须为持仓或全市场")
                data["movement_scope"] = values["movement_scope"]
            if "google_news_rss" in values:
                data.setdefault("sources", {})["google_news_rss"] = bool(
                    values["google_news_rss"]
                )
            if "yahoo_finance_rss" in values:
                data.setdefault("sources", {})["yahoo_finance_rss"] = bool(
                    values["yahoo_finance_rss"]
                )
            if "bing_news_rss" in values:
                data.setdefault("sources", {})["bing_news_rss"] = bool(
                    values["bing_news_rss"]
                )
            self.write(data)
            return data

    def set_asset_system_notifications(self, enabled: bool) -> bool:
        with self._lock:
            data = self.read()
            data["asset_system_notifications_enabled"] = bool(enabled)
            self.write(data)
            return bool(enabled)

    def add_position(self, item: dict[str, Any]) -> dict[str, Any]:
        from decimal import Decimal
        from . import portfolio_trades
        from .security_master import resolve, normalize, CURRENCIES
        market = str(item.get("market", "")).strip().upper()
        if market not in {"CN", "HK", "US", "KR", "FUND"}:
            raise ValueError("市场必须是 CN、HK、KR、US 或 FUND")
        _, symbol = normalize(market, item.get("symbol", ""))
        identity = resolve(market, symbol)
        symbol, name = identity["symbol"], identity["name"]
        quantity = _positive_number(item.get("quantity"), "持仓数量")
        average_cost = _positive_number(item.get("average_cost"), "平均成本")
        currency = str(item.get("currency") or _default_currency(market)).strip().upper()
        if currency not in {"CNY", "HKD", "USD", "KRW"}:
            raise ValueError("币种必须是 CNY、HKD、USD 或 KRW")
        if currency != CURRENCIES[market]:
            raise ValueError(f"报价币种应为 {CURRENCIES[market]}；不同币种的原始成本请填写成本币种和明确的换算率")
        cost_currency = str(item.get("cost_currency") or currency).upper()
        if cost_currency not in {"CNY", "HKD", "USD", "KRW"}:
            raise ValueError("成本币种无效")
        original_cost = average_cost
        cost_rate = 1.0
        if cost_currency != currency:
            cost_rate = _positive_number(item.get("cost_fx_rate"), "成本换算率")
            if not item.get("cost_fx_date"):
                raise ValueError("不同币种成本换算须注明汇率日期")
            datetime.strptime(str(item["cost_fx_date"]), "%Y-%m-%d")
            average_cost *= cost_rate
            if not math.isfinite(average_cost) or average_cost > 1e12:
                raise ValueError("换算后的单位成本超出允许范围")
        now = datetime.now(timezone.utc).isoformat()
        position = {
            "market": market,
            "symbol": symbol,
            "name": name,
            "quantity": round(quantity, 6),
            "average_cost": round(average_cost, 6),
            "currency": currency,
            "identity_source": identity.get("source"),
            "identity_as_of": identity.get("as_of"),
            "sector": identity.get("sector"),
            "industry": identity.get("industry"),
            "original_cost": original_cost,
            "cost_currency": cost_currency,
            "cost_fx_rate": cost_rate,
            "cost_fx_date": item.get("cost_fx_date"),
            "asset_type": identity.get("asset_type") or ("fund" if market == "FUND" else "stock"),
            "created_at": now,
            "updated_at": now,
        }
        if "plan" in item:
            from .portfolio_workspace import normalize_plan
            position["plan"] = {**normalize_plan(item["plan"]), "currency": currency}
        if market == "FUND":
            position["fund_type"] = str(item.get("fund_type") or "公募基金").strip()

        opening = item.get("opening_trade")
        request = {}
        if opening is not None:
            if not isinstance(opening, dict) or not opening.get("trade_date") or "fee" not in opening:
                raise ValueError("建仓成交须填写实际成交日期及费用，无费用请填0")
            if cost_currency != currency:
                raise ValueError("建仓成交价格与费用须用标的原币填写")
            trade_qty, price, fee, _, _, opened, note = portfolio_trades.trade_inputs({
                "quantity": item.get("quantity"), "execution_price": item.get("average_cost"),
                "trade_date": opening["trade_date"], "fee": opening["fee"], "note": opening.get("note", "")})
            plan = position.get("plan") or {}
            if plan.get("opened_date") and plan["opened_date"] != opened:
                raise ValueError("建仓日期须与实际建仓成交日期一致")
            total_cost = trade_qty * price + fee
            unit_cost = total_cost / trade_qty
            if unit_cost > Decimal("1e12") or round(float(unit_cost), 6) <= 0:
                raise ValueError("计入费用后的平均成本超出允许范围")
            position.update(quantity=float(trade_qty), average_cost=round(float(unit_cost), 6),
                            cost_basis_total=str(total_cost), cost_fees_complete=True)
            request = portfolio_trades.request_metadata(opening, {"origin": "opening", "market": market,
                "symbol": symbol, "quantity": str(trade_qty.normalize()), "execution_price": str(price.normalize()),
                "fee": str(fee.normalize()), "fund_type": position.get("fund_type"), "note": note, "plan": plan})

        with self._lock:
            data = self.read()
            portfolio = get_portfolio(data, item.get("portfolio_id", "default"))
            positions = portfolio.setdefault("positions", [])
            previous = portfolio_trades.previous_request(portfolio, request)
            if previous:
                current = next((row for row in positions if row.get("cycle_id") == previous["id"]), None)
                if current is None:
                    raise ValueError("这笔建仓已登记，当前持仓已结束，请重新打开建仓表单")
                return dict(current)
            if any(_same_position(existing, market, symbol) for existing in positions):
                raise ValueError(f"{market}:{symbol} 已在持仓中")
            pending = next((row for row in portfolio.get("pending_positions", [])
                            if _same_position(row, market, symbol)), None)
            source = (pending or {}).get("whitelist_source") or _position_whitelist_source(data, market, symbol, now)
            if source:
                from .portfolio_workspace import normalize_plan
                # Omitted fields inherit; explicit edits (including an empty analysis) win.
                plan = {**{key: (source.get("profile") or {}).get(key, "") for key in ("sector", "usage", "rating", "business", "asset_class")}, "entry_analysis": source["analysis"], "buy_reason": source["reason"],
                        **(item.get("plan") or {})}
                position["plan"] = {**normalize_plan(plan), "currency": currency}
                position["whitelist_source"] = source
            if opening is not None:
                latest_close = max((row["trade_date"] for row in portfolio.get("trades", [])
                                    if _same_position(row, market, symbol) and row.get("closed")
                                    and row.get("origin") == "adjust"), default="")
                if opened < latest_close:
                    raise ValueError("建仓成交早于该标的上次清仓，请补录历史成交")
                plan = position["plan"] = {**(position.get("plan") or {}), "opened_date": opened, "currency": currency}
                trade = portfolio_trades.record(position=position, action="buy", quantity=trade_qty,
                    execution_price=price, cost_amount=total_cost, fee=fee, extra_buy_fee=Decimal(0),
                    fee_complete=True, trade_date=opened, note=note, origin="opening",
                    rationale={key: plan[key] for key in ("entry_analysis", "buy_reason") if key in plan})
                position["cycle_id"] = trade["id"]
                trade.update(request, cycle_id=trade["id"])
                if source:
                    trade["whitelist_source"] = dict(source)
                portfolio.setdefault("trades", []).append(trade)
            positions.append(position)
            if pending is not None:
                portfolio["pending_positions"].remove(pending)
            self._ensure_position_in_watchlist(data, position)
            self.write(data)
            return dict(position)

    def set_portfolio_principal(self, value: Any, portfolio_id: str = "default") -> float:
        try:
            amount = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("本金必须是有效金额") from exc
        if isinstance(value, bool) or not math.isfinite(amount) or not 0 <= amount <= 1_000_000_000_000:
            raise ValueError("本金须为 0 到 1 万亿元之间的金额")
        amount = round(amount, 2)
        with self._lock:
            data = self.read()
            portfolio = get_portfolio(data, portfolio_id)
            portfolio["principal_cny"] = amount
            if "principal" in portfolio:
                portfolio["principal"], portfolio["principal_currency"] = amount, "CNY"
            self.write(data)
        return amount

    def set_portfolio_account(self, values: dict[str, Any]) -> dict[str, Any]:
        currencies = {"CNY", "USD", "HKD", "KRW"}
        with self._lock:
            data = self.read()
            portfolio = get_portfolio(data, values.get("portfolio_id", "default"))
            for key, label in (("receivable_balances", "应收账款"), ("payable_balances", "应付账款")):
                if key in values:
                    value = values[key]
                    portfolio[key] = {} if value == {} else portfolio_cash.normalize_balances(value, label=label)
            if "cash_balances" in values:
                if "cash" in values or "cash_currency" in values:
                    raise ValueError("请只提交多币种现金明细")
                portfolio["cash_balances"] = portfolio_cash.normalize_balances(values["cash_balances"])
                portfolio.pop("cash", None)
                portfolio.pop("cash_currency", None)
                portfolio["cash_mode"] = "manual"
            elif "cash_balances" in portfolio and ("cash" in values or "cash_currency" in values):
                raise ValueError("请通过库存现金明细修改多币种余额")
            if "cash_mode" in values:
                if values["cash_mode"] not in {"auto", "manual"}:
                    raise ValueError("现金口径无效")
                portfolio["cash_mode"] = values["cash_mode"]
            elif "cash" in values:
                portfolio["cash_mode"] = "manual"
            for key in ("base_currency", "principal_currency", "cash_currency"):
                if key in values:
                    if values[key] not in currencies:
                        raise ValueError("账户币种无效")
                    portfolio[key] = values[key]
            for key in ("principal", "cash"):
                if key in values:
                    if key == "cash" and portfolio.get("cash_mode") == "auto":
                        continue
                    value = values[key]
                    if isinstance(value, bool):
                        raise ValueError("金额无效")
                    try:
                        amount = float(value)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("金额无效") from exc
                    if not math.isfinite(amount) or not 0 <= amount <= 1e12:
                        raise ValueError("金额须在 0 到 1 万亿元之间")
                    portfolio[key] = amount
            if portfolio_cash.mode(portfolio) == "manual" and not portfolio_cash.balances(portfolio):
                raise ValueError("请填写实际库存现金，或选择自动估算")
            for key in ("position_limit", "sector_limit", "top5_limit"):
                if key in values:
                    try:
                        if isinstance(values[key], bool):
                            raise ValueError("上限不能是布尔值")
                        amount = float(values[key])
                    except (TypeError, ValueError) as exc:
                        raise ValueError("集中度上限须为数值") from exc
                    if not math.isfinite(amount) or not 0 < amount <= 100:
                        raise ValueError("集中度上限须大于 0 且不超过 100%")
                    portfolio[key] = amount
            for key in ("owner", "strategy"):
                if key in values:
                    value = values[key]
                    if not isinstance(value, str) or len(value) > 60 or any(ord(c) < 32 for c in value):
                        raise ValueError("负责人和策略须为60字以内的单行文字")
                    portfolio[key] = value.strip()
            if "benchmark" in values:
                from .portfolio_research import normalize_target
                value = values["benchmark"]
                if value and str(value).split(":")[0].upper() not in {"US", "CN", "HK", "KR"}:
                    raise ValueError("比较基准须为股票或ETF市场代码")
                portfolio["benchmark"] = ":".join(normalize_target(value)) if value else ""
            self.write(data)
            return {k: v for k, v in portfolio.items() if k != "positions"}

    def set_position_plan(self, values: dict[str, Any]) -> dict[str, Any]:
        from .portfolio_workspace import normalize_plan
        market = str(values.get("market", "")).upper()
        symbol = _normalize_position_symbol(market, values.get("symbol"))
        plan = normalize_plan(values.get("plan"))
        with self._lock:
            data = self.read()
            position = next((p for p in get_portfolio(data, values.get("portfolio_id", "default")).get("positions", [])
                             if _same_position(p, market, symbol)), None)
            if position is None:
                raise ValueError("持仓不存在，请刷新后重试")
            previous = position.get("plan") or {}
            rationale = {key: previous[key] for key in ("entry_analysis", "buy_reason") if key in previous}
            position["plan"] = {**previous, **rationale, **plan, "currency": position["currency"]}
            self.write(data)
            return dict(position)

    def set_position_risk(self, values: dict[str, Any]) -> dict[str, Any]:
        from .portfolio_alerts import normalize_risk_settings

        market = str(values.get("market", "")).strip().upper()
        symbol = _normalize_position_symbol(market, values.get("symbol"))
        settings = normalize_risk_settings(values.get("risk_settings"))
        with self._lock:
            data = self.read()
            position = next((p for p in get_portfolio(data, values.get("portfolio_id", "default")).get("positions", [])
                             if _same_position(p, market, symbol)), None)
            if position is None:
                raise ValueError("持仓不存在，无法设置警戒线")
            if position.get("risk_settings") != settings:
                position["risk_revision"] = uuid.uuid4().hex
            position["risk_settings"] = settings
            self.write(data)
            return dict(position)

    def correct_position_identity(self, values: dict[str, Any]) -> dict[str, Any]:
        from .security_master import resolve, normalize
        market, symbol = normalize(values.get("market"), values.get("symbol"))
        if values.get("confirmed") is not True:
            raise ValueError("请明确确认原始成本及币种")
        identity = resolve(market, symbol)
        source_currency = values.get("cost_currency")
        if source_currency not in {"CNY", "USD", "HKD", "KRW"}:
            raise ValueError("原始成本币种无效")
        original = _positive_number(values.get("original_cost"), "原始单位成本")
        rate = 1.0
        if source_currency != identity["currency"]:
            rate = _positive_number(values.get("cost_fx_rate"), "明确的成本换算率")
            datetime.strptime(str(values.get("cost_fx_date", "")), "%Y-%m-%d")
        cost = original * rate
        if not math.isfinite(cost) or cost > 1e12:
            raise ValueError("单位成本超出允许范围")
        with self._lock:
            data = self.read()
            row = next((r for r in get_portfolio(data, values.get("portfolio_id", "default")).get("positions", []) if _same_position(r, market, symbol)), None)
            if row is None:
                raise ValueError("持仓已不存在")
            if len(row.get("identity_corrections", [])) >= 100:
                raise ValueError("校正记录已达100条，请先导出归档")
            row.setdefault("identity_corrections", []).append({"at": datetime.now(timezone.utc).isoformat(),
                "name": row.get("name"), "currency": row.get("currency"), "average_cost": row.get("average_cost")})
            row.update(name=identity["name"], currency=identity["currency"], average_cost=cost,
                original_cost=original, cost_currency=source_currency, cost_fx_rate=rate, cost_fx_date=values.get("cost_fx_date"),
                identity_source=identity.get("source"), sector=identity.get("sector"), industry=identity.get("industry"))
            # A user cost correction replaces the remaining cost pool, never frozen past trades.
            row.pop("cost_basis_total", None)
            row["cost_fees_complete"] = False
            self.write(data)
            return dict(row)

    def adjust_position(self, values: dict[str, Any]) -> dict[str, Any] | None:
        from decimal import Decimal
        from . import portfolio_trades
        from .portfolio_workspace import normalize_rationale
        market = str(values.get("market", "")).strip().upper()
        symbol = _normalize_position_symbol(market, values.get("symbol"))
        action = str(values.get("action", "")).strip().lower()
        if action not in {"buy", "sell"}:
            raise ValueError("操作必须是 buy 或 sell")
        quantity, execution_price, fee, extra_fee, confirmed, when, note = portfolio_trades.trade_inputs(values)
        submitted_rationale = normalize_rationale(values)
        if action == "buy" and extra_fee:
            raise ValueError("补充分摊买费仅适用于卖出；本次买入费用请填写本次交易费用")
        request = portfolio_trades.request_metadata(values, {"origin": "adjust", "market": market,
            "symbol": symbol, "action": action, "quantity": str(quantity.normalize()),
            "execution_price": str(execution_price.normalize()), "fee": str(fee.normalize()) if fee is not None else None,
            "extra_buy_fee": str(extra_fee.normalize()), "cost_fees_confirmed": confirmed, "note": note,
            **submitted_rationale})

        with self._lock:
            data = self.read()
            account = get_portfolio(data, values.get("portfolio_id", "default"))
            positions = account.setdefault("positions", [])
            previous = portfolio_trades.previous_request(account, request)
            if previous:
                current = next((item for item in positions if _same_position(item, market, symbol)), None)
                return None if previous["closed"] or current is None else dict(current)
            latest_date = max((row["trade_date"] for row in account.get("trades", [])
                               if row.get("origin") in {"opening", "adjust"} and _same_position(row, market, symbol)), default="")
            if when < latest_date:
                raise ValueError("成交日期早于该标的最近一次持仓调整，请使用历史补录，避免按当前成本池倒算历史盈亏")
            position = next((item for item in positions if _same_position(item, market, symbol)), None)
            if position is None:
                raise ValueError("持仓不存在")
            source = position.get("whitelist_source")
            current_quantity = Decimal(str(position["quantity"]))
            current_cost = Decimal(str(position["average_cost"]))
            cost_pool = Decimal(str(position.get("cost_basis_total", current_quantity * current_cost)))
            if not cost_pool.is_finite() or cost_pool <= 0:
                raise ValueError("持仓成本异常，请核对记录")
            fee_complete = fee is not None and (confirmed or position.get("cost_fees_complete") is True)
            closed = False
            if action == "sell":
                if quantity > current_quantity:
                    raise ValueError("减仓数量不能超过当前持仓")
                remaining = current_quantity - quantity
                closed = remaining == 0
                allocated_cost = cost_pool if closed else cost_pool * quantity / current_quantity
                plan = position.get("plan") or {}
                rationale = {key: plan[key] for key in ("entry_analysis", "buy_reason") if key in plan}
                rationale.update({key: submitted_rationale[key] for key in ("exit_analysis", "sell_reason")
                                  if key in submitted_rationale})
                trade = portfolio_trades.record(position=position, action=action, quantity=quantity,
                    execution_price=execution_price, cost_amount=allocated_cost, fee=fee,
                    extra_buy_fee=extra_fee, fee_complete=fee_complete, trade_date=when, note=note,
                    closed=closed, rationale=rationale)
                if closed:
                    positions.remove(position)
                else:
                    position["quantity"] = float(remaining)
                    position["cost_basis_total"] = str(cost_pool - allocated_cost)
                    # Confirmation with extra fees only verifies this sold slice, not the remaining holding.
                    if extra_fee:
                        position["cost_fees_complete"] = False
                    elif confirmed:
                        position["cost_fees_complete"] = True
            else:
                updated_quantity = current_quantity + quantity
                updated_cost = cost_pool + quantity * execution_price + (fee or Decimal(0))
                plan = position.get("plan") or {}
                source = source or _position_whitelist_source(data, market, symbol)
                rationale = ({"entry_analysis": source["analysis"], "buy_reason": source["reason"]}
                             if source else {})
                rationale.update({key: plan[key] for key in ("entry_analysis", "buy_reason") if key in plan})
                rationale.update({key: submitted_rationale[key] for key in ("entry_analysis", "buy_reason")
                                  if key in submitted_rationale})
                trade = portfolio_trades.record(position=position, action=action, quantity=quantity,
                    execution_price=execution_price, cost_amount=Decimal(0), fee=fee,
                    extra_buy_fee=extra_fee, fee_complete=fee is not None, trade_date=when, note=note,
                    rationale=rationale)
                position["quantity"] = float(updated_quantity)
                position["average_cost"] = round(float(updated_cost / updated_quantity), 6)
                position["cost_basis_total"] = str(updated_cost)
                position["cost_fees_complete"] = fee is not None and position.get("cost_fees_complete") is True
            trade.update(request)
            if source:
                trade["whitelist_source"] = dict(source)
            account.setdefault("trades", []).append(trade)
            position["last_action"] = action
            position["last_execution_price"] = round(float(execution_price), 6)
            position["updated_at"] = datetime.now(timezone.utc).isoformat()
            self.write(data)
            return None if closed else dict(position)

    def portfolio_records(self, portfolio_id="default"):
        from .portfolio_records import snapshot
        return snapshot(get_portfolio(self.read(), portfolio_id))

    def save_portfolio_records(self, values):
        from .portfolio_records import update
        with self._lock:
            data = self.read()
            result = update(get_portfolio(data, values.get("portfolio_id", "default")), values)
            self.write(data)
            return result

    def save_pending_position(self, values: dict[str, Any]) -> dict[str, Any]:
        from .security_master import CURRENCIES, normalize

        portfolio_id = values.get("portfolio_id", "default")
        market, symbol = normalize(values.get("market", ""), values.get("symbol", ""))
        action = values.get("action")
        if action not in ("add", "remove"):
            raise ValueError("待建仓操作无效")
        with self._lock:
            data = self.read()
            account = get_portfolio(data, portfolio_id)
            rows = account.get("pending_positions", [])
            existing = next((row for row in rows if _same_position(row, market, symbol)), None)
            if action == "add" and existing is None:
                if any(_same_position(row, market, symbol) for row in account.get("positions", [])):
                    raise ValueError("该标的已在当前组合持仓中")
                source = _position_whitelist_source(data, market, symbol)
                if source is None:
                    raise ValueError("请先将标的加入白名单")
                rows = [*rows, {"market": market, "symbol": symbol, "name": source["name"],
                               "currency": CURRENCIES[market], "created_at": source["linked_at"],
                               "whitelist_source": source}]
                account["pending_positions"] = rows
                self.write(data)
            elif action == "remove" and existing is not None:
                rows = [row for row in rows if row is not existing]
                account["pending_positions"] = rows
                self.write(data)
            return {"portfolio_id": portfolio_id, "items": rows}

    def add_historical_trade(self, values: dict[str, Any]) -> dict[str, Any]:
        if values.get("action") == "template_update":
            from .portfolio_trades import template_metadata
            with self._lock:
                data = self.read()
                account = get_portfolio(data, values.get("portfolio_id", "default"))
                trade = next((row for row in account.get('trades', []) if row['id'] == values.get('id')), None)
                if trade is None:
                    raise ValueError('成交记录不存在')
                trade.update(template_metadata(values, trade['trade_date']))
                self.write(data)
                return dict(trade)
        if values.get("action") == "complete_fees":
            return self.complete_trade_fees(values)
        from .portfolio_trades import manual_record, previous_request
        trade = manual_record(values)
        with self._lock:
            data = self.read()
            account = get_portfolio(data, values.get("portfolio_id", "default"))
            request = {k: trade[k] for k in ("request_id", "request_sha256") if k in trade}
            previous = previous_request(account, request)
            if previous:
                return dict(previous)
            account.setdefault("trades", []).append(trade)
            self.write(data)
        return trade

    def complete_trade_fees(self, values: dict[str, Any]) -> dict[str, Any]:
        from decimal import Decimal
        from . import portfolio_trades
        identifier = values.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("缺少待补全成交编号")
        fee = portfolio_trades.number(values.get("fee"), "本次交易费用")
        extra = portfolio_trades.number(values.get("extra_buy_fee", 0), "未计入成本的买入费用")
        if values.get("cost_fees_confirmed") is not True:
            raise ValueError("请确认已核对成本和未计入成本的买入费用")
        request = portfolio_trades.request_metadata(values, {"origin": "complete_fees", "id": identifier,
            "fee": str(fee.normalize()), "extra_buy_fee": str(extra.normalize()), "cost_fees_confirmed": True})
        with self._lock:
            data = self.read()
            account = get_portfolio(data, values.get("portfolio_id", "default"))
            row = next((r for r in account.get("trades", []) if r["id"] == identifier), None)
            if row is None or row["action"] != "sell":
                raise ValueError("待补全的卖出成交不存在")
            if request and row.get("fees_request_id") == request["request_id"]:
                if row.get("fees_request_sha256") != request["request_sha256"]:
                    raise ValueError("同一补全请求的费用已改变，请重新打开表单")
                return dict(row)
            if row.get("fee_complete"):
                raise ValueError("该成交费用已经完整，不能重复补全")
            stamp = datetime.now(timezone.utc).isoformat()
            row["fees_completion"] = {"previous": {key: row.get(key) for key in ("fee", "extra_buy_fee", "net_pnl", "fee_complete")},
                                      "completed_at": stamp, "cost_fees_confirmed": True}
            row.update(fee=portfolio_trades.amount(fee), extra_buy_fee=portfolio_trades.amount(extra),
                       net_pnl=portfolio_trades.amount(Decimal(str(row["gross_amount"])) - Decimal(str(row["cost_amount"])) - fee - extra),
                       fee_complete=True, fees_completed_at=stamp)
            if request:
                row.update(fees_request_id=request["request_id"], fees_request_sha256=request["request_sha256"])
            self.write(data)
            return dict(row)

    def remove_position(self, market: str, symbol: str, portfolio_id: str = "default") -> bool:
        market = market.strip().upper()
        normalized_symbol = _normalize_position_symbol(market, symbol)
        with self._lock:
            data = self.read()
            portfolio = get_portfolio(data, portfolio_id)
            positions = portfolio.setdefault("positions", [])
            kept = [
                item for item in positions
                if not _same_position(item, market, normalized_symbol)
            ]
            if len(kept) == len(positions):
                return False
            portfolio["positions"] = kept
            self.write(data)
            return True

    def clear_positions(self, portfolio_id: str = "default") -> int:
        with self._lock:
            data = self.read()
            portfolio = get_portfolio(data, portfolio_id)
            count = len(portfolio.get("positions") or [])
            if not count:
                return 0
            now = datetime.now(timezone.utc)
            folder = self.path.parent / "data" / "backups" / "portfolio"
            folder.mkdir(parents=True, exist_ok=True)
            backup = folder / f"positions-{now.strftime('%Y%m%d-%H%M%S-%f')}.json"
            with backup.open("x", encoding="utf-8") as handle:
                json.dump({"saved_at": now.isoformat(), "portfolio_id": portfolio_id, "portfolio": portfolio}, handle, ensure_ascii=False, indent=2)
            portfolio["positions"] = []
            self.write(data)
            return count

    def save_portfolio(self, name: str, portfolio_id: str | None = None) -> dict[str, Any]:
        name = validate_name(name)
        with self._lock:
            data = self.read()
            if any(row["id"] != portfolio_id and row["name"].casefold() == name.casefold() for row in portfolios(data)):
                raise ValueError("组合名称已存在")
            if portfolio_id is None:
                if len(portfolios(data)) >= 100:
                    raise ValueError("组合数已达100个，请先整理空组合")
                portfolio_id = uuid.uuid4().hex
                row = {"id": portfolio_id, "positions": [], "base_currency": "CNY"}
                data.setdefault("portfolios", []).append(row)
            else:
                row = get_portfolio(data, portfolio_id)
            row["name"] = name
            self.write(data)
            return {"id": portfolio_id, "name": name}

    def save_dividend(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self.read()
            account = get_portfolio(data, payload.get("portfolio_id", "default"))
            records = account.setdefault("dividends", [])
            old = next((r for r in records if r["id"] == payload.get("id")), None)
            row = portfolio_dividends.normalize(payload, account, old)
            portfolio_dividends.reconcile_cash(account, old, row)
            account["dividends"] = [r for r in records if r["id"] != row["id"]] + [row]
            self.write(data)
            return row

    def delete_dividend(self, record_id: str, portfolio_id: str = "default") -> None:
        with self._lock:
            data = self.read()
            account = get_portfolio(data, portfolio_id)
            records = account.get("dividends", [])
            old = next((r for r in records if r["id"] == record_id), None)
            if old is None:
                return
            portfolio_dividends.reconcile_cash(account, old, None)
            account["dividends"] = [r for r in records if r["id"] != record_id]
            self.write(data)

    def delete_portfolio(self, portfolio_id: str) -> None:
        with self._lock:
            data = self.read()
            row = get_portfolio(data, portfolio_id)
            if row.get("positions") or row.get("pending_positions") or any(portfolio_cash.balances(row).values()) or any(row.get("receivable_balances", {}).values()) or any(row.get("payable_balances", {}).values()) or row.get("principal") or row.get("principal_cny") or row.get("dividends") or row.get("trades"):
                raise ValueError("组合仍有持仓、待建仓标的、本金、现金、股息记录或成交记录，不能删除")
            if len(portfolios(data)) == 1:
                raise ValueError("请至少保留一个组合")
            # Research remains in the ledger even after an empty account is removed.
            if portfolio_id == "default":
                data["portfolio"] = {"deleted": True}
            else:
                data["portfolios"] = [r for r in data["portfolios"] if r["id"] != portfolio_id]
            self.write(data)

    @staticmethod
    def _ensure_position_in_watchlist(data: dict[str, Any], position: dict[str, Any]) -> None:
        market = str(position["market"])
        symbol = str(position["symbol"])
        watchlist = data.setdefault("watchlist", [])
        if any(
            str(item.get("market", "")).upper() == market
            and str(item.get("symbol", "")).upper() == symbol
            for item in watchlist
        ):
            return
        name = str(position["name"])
        watchlist.append(
            {
                "market": market,
                "symbol": symbol,
                "name": name,
                "query": f"{name} {symbol}",
                "aliases": [name, symbol],
            }
        )


def _bounded_int(value: Any, lower: int, upper: int, label: str) -> int:
    parsed = int(value)
    if not lower <= parsed <= upper:
        raise ValueError(f"{label}必须在 {lower} 到 {upper} 之间")
    return parsed


def _positive_number(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须是数字") from exc
    if not math.isfinite(parsed) or parsed <= 0 or parsed > 1_000_000_000_000:
        raise ValueError(f"{label}必须大于 0 且在合理范围内")
    return parsed


def _position_whitelist_source(data: dict[str, Any], market: str, symbol: str,
                               linked_at: str | None = None) -> dict[str, Any] | None:
    """Freeze the matching registration; later watch-list edits never rewrite a holding."""
    row = next((row for row in data.get("stock_whitelist", []) if _same_position(row, market, symbol)), None)
    if row is None:
        return None
    return {key: row.get(key, "") for key in
            ("market", "symbol", "name", "analysis", "reason", "created_at", "updated_at")} | {"profile": dict(row.get("profile") or {}),
                "linked_at": linked_at or datetime.now(timezone.utc).isoformat()}


def _normalize_position_symbol(market: str, value: Any) -> str:
    symbol = str(value or "").strip().upper()
    if not symbol:
        raise ValueError("股票代码不能为空")
    if market == "FUND":
        if not (symbol.isdigit() and len(symbol) == 6):
            raise ValueError("中国基金代码必须是 6 位数字")
        return symbol
    if market == "KR" and symbol.isdigit():
        return f"{symbol.zfill(6)}.KS"
    if market == "HK" and symbol.isdigit():
        return f"{symbol.zfill(4)}.HK"
    if market == "CN" and symbol.isdigit() and len(symbol) == 6:
        suffix = ".SS" if symbol.startswith(("5", "6", "9")) else ".SZ"
        return f"{symbol}{suffix}"
    return symbol


def _same_position(item: dict[str, Any], market: str, symbol: str) -> bool:
    return (
        str(item.get("market", "")).upper() == market
        and str(item.get("symbol", "")).upper() == symbol
    )


def _default_currency(market: str) -> str:
    return {
        "CN": "CNY",
        "HK": "HKD",
        "US": "USD",
        "KR": "KRW",
        "FUND": "CNY",
    }[market]

from __future__ import annotations

import json
import math
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
            os.replace(temporary, self.path)

    def add_watch_item(self, item: dict[str, Any]) -> dict[str, Any]:
        market = str(item.get("market", "")).strip().upper()
        symbol = str(item.get("symbol", "")).strip().upper()
        name = str(item.get("name", "")).strip()
        if market not in {"CN", "HK", "US", "KR"}:
            raise ValueError("市场必须是 CN、HK、KR 或 US")
        if not symbol or not name:
            raise ValueError("股票代码和公司名称不能为空")

        query = str(item.get("query") or f"{name} {symbol}").strip()
        raw_aliases = item.get("aliases", [])
        if isinstance(raw_aliases, str):
            raw_aliases = [value.strip() for value in raw_aliases.split(",")]
        aliases = [str(value).strip() for value in raw_aliases if str(value).strip()]

        with self._lock:
            data = self.read()
            watchlist = data.setdefault("watchlist", [])
            if any(
                str(existing.get("market", "")).upper() == market
                and str(existing.get("symbol", "")).upper() == symbol
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
            watchlist.append(normalized)
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
        market = str(item.get("market", "")).strip().upper()
        if market not in {"CN", "HK", "US", "KR", "FUND"}:
            raise ValueError("市场必须是 CN、HK、KR、US 或 FUND")
        symbol = _normalize_position_symbol(market, item.get("symbol"))
        name = str(item.get("name", "")).strip()
        if not name:
            raise ValueError("标的名称不能为空")
        quantity = _positive_number(item.get("quantity"), "持仓数量")
        average_cost = _positive_number(item.get("average_cost"), "平均成本")
        currency = str(item.get("currency") or _default_currency(market)).strip().upper()
        if market == "FUND":
            currency = "CNY"
        if currency not in {"CNY", "HKD", "USD", "KRW"}:
            raise ValueError("币种必须是 CNY、HKD、USD 或 KRW")
        now = datetime.now(timezone.utc).isoformat()
        position = {
            "market": market,
            "symbol": symbol,
            "name": name,
            "quantity": round(quantity, 6),
            "average_cost": round(average_cost, 6),
            "currency": currency,
            "asset_type": "fund" if market == "FUND" else "stock",
            "created_at": now,
            "updated_at": now,
        }
        if market == "FUND":
            position["fund_type"] = str(item.get("fund_type") or "公募基金").strip()

        with self._lock:
            data = self.read()
            portfolio = data.setdefault("portfolio", {"base_currency": "CNY", "positions": []})
            positions = portfolio.setdefault("positions", [])
            if any(_same_position(existing, market, symbol) for existing in positions):
                raise ValueError(f"{market}:{symbol} 已在持仓中")
            positions.append(position)
            self._ensure_position_in_watchlist(data, position)
            self.write(data)
            return dict(position)

    def set_portfolio_principal(self, value: Any) -> float:
        try:
            amount = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("本金必须是有效金额") from exc
        if isinstance(value, bool) or not math.isfinite(amount) or not 0 <= amount <= 1_000_000_000_000:
            raise ValueError("本金须为 0 到 1 万亿元之间的金额")
        amount = round(amount, 2)
        with self._lock:
            data = self.read()
            portfolio = data.setdefault("portfolio", {"base_currency": "CNY", "positions": []})
            portfolio["principal_cny"] = amount
            self.write(data)
        return amount

    def adjust_position(self, values: dict[str, Any]) -> dict[str, Any] | None:
        market = str(values.get("market", "")).strip().upper()
        symbol = _normalize_position_symbol(market, values.get("symbol"))
        action = str(values.get("action", "")).strip().lower()
        if action not in {"buy", "sell"}:
            raise ValueError("操作必须是 buy 或 sell")
        quantity = _positive_number(values.get("quantity"), "调整数量")
        execution_price = _positive_number(values.get("execution_price"), "成交价格")

        with self._lock:
            data = self.read()
            positions = data.setdefault(
                "portfolio", {"base_currency": "CNY", "positions": []}
            ).setdefault("positions", [])
            position = next((item for item in positions if _same_position(item, market, symbol)), None)
            if position is None:
                raise ValueError("持仓不存在")
            current_quantity = float(position.get("quantity") or 0.0)
            current_cost = float(position.get("average_cost") or 0.0)
            if action == "sell":
                if quantity > current_quantity + 1e-9:
                    raise ValueError("减仓数量不能超过当前持仓")
                remaining = current_quantity - quantity
                if remaining <= 1e-9:
                    positions.remove(position)
                    self.write(data)
                    return None
                position["quantity"] = round(remaining, 6)
            else:
                updated_quantity = current_quantity + quantity
                weighted_cost = (
                    current_quantity * current_cost + quantity * execution_price
                ) / updated_quantity
                position["quantity"] = round(updated_quantity, 6)
                position["average_cost"] = round(weighted_cost, 6)
            position["last_action"] = action
            position["last_execution_price"] = round(execution_price, 6)
            position["updated_at"] = datetime.now(timezone.utc).isoformat()
            self.write(data)
            return dict(position)

    def remove_position(self, market: str, symbol: str) -> bool:
        market = market.strip().upper()
        normalized_symbol = _normalize_position_symbol(market, symbol)
        with self._lock:
            data = self.read()
            portfolio = data.setdefault("portfolio", {"base_currency": "CNY", "positions": []})
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

    def clear_positions(self) -> int:
        with self._lock:
            data = self.read()
            portfolio = data.get("portfolio") or {}
            count = len(portfolio.get("positions") or [])
            if not count:
                return 0
            now = datetime.now(timezone.utc)
            folder = self.path.parent / "data" / "backups" / "portfolio"
            folder.mkdir(parents=True, exist_ok=True)
            backup = folder / f"positions-{now.strftime('%Y%m%d-%H%M%S-%f')}.json"
            with backup.open("x", encoding="utf-8") as handle:
                json.dump({"saved_at": now.isoformat(), "portfolio": portfolio}, handle, ensure_ascii=False, indent=2)
            portfolio["positions"] = []
            self.write(data)
            return count

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

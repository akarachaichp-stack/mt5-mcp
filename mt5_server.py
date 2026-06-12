# -*- coding: utf-8 -*-
"""
MCP server for MetaTrader 5 (Windows).

Exposes a running MT5 desktop terminal (any broker build: MetaQuotes, Pepperstone,
IC Markets, ...) as MCP tools: account status, symbol quotes, candles, open
positions, market orders and position closing.

Requires the official `MetaTrader5` Python package, which attaches to a desktop
terminal installed on Windows. MT5 web terminal / mobile apps are NOT supported
(they have no local API).

Environment variables:
  MT5_TERMINAL_PATH   full path to terminal64.exe (default: auto-detect — uses
                      the last terminal the package finds installed)
  MT5_ALLOW_REAL      set to "1" to allow trading tools on REAL accounts
                      (default: trading tools refuse anything but demo)

Safety model:
  - Read-only tools (status, quotes, candles, positions, history) work on any account.
  - Trading tools (mt5_order_open, mt5_close) abort on non-demo accounts unless
    MT5_ALLOW_REAL=1. This is deliberate: an LLM should not place real-money
    orders because of a config oversight.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import MetaTrader5 as mt5
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("mt5")

TERMINAL_PATH = os.environ.get("MT5_TERMINAL_PATH", "")
ALLOW_REAL = os.environ.get("MT5_ALLOW_REAL", "") == "1"

TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1, "W1": mt5.TIMEFRAME_W1, "MN1": mt5.TIMEFRAME_MN1,
}

_connected = False


def _ok(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1, default=str)


def _connect() -> None:
    global _connected
    if _connected and mt5.terminal_info() is not None:
        return
    kwargs = {"path": TERMINAL_PATH} if TERMINAL_PATH else {}
    if not mt5.initialize(**kwargs):
        raise RuntimeError(
            f"mt5.initialize failed: {mt5.last_error()}. Is the MT5 desktop terminal "
            "installed and logged in? Set MT5_TERMINAL_PATH if auto-detection fails."
        )
    _connected = True


def _guard_trading() -> None:
    acc = mt5.account_info()
    if acc is None:
        raise RuntimeError(f"account_info failed: {mt5.last_error()}")
    if acc.trade_mode != 0 and not ALLOW_REAL:  # 0 = demo
        raise RuntimeError(
            f"Account '{acc.server}' is NOT a demo account (trade_mode={acc.trade_mode}). "
            "Trading tools are demo-only unless MT5_ALLOW_REAL=1 is set."
        )


def _filling(symbol: str):
    info = mt5.symbol_info(symbol)
    if info is None:
        return mt5.ORDER_FILLING_IOC
    fm = info.filling_mode
    if fm & 2:
        return mt5.ORDER_FILLING_IOC
    if fm & 1:
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


def _pos_dict(p) -> dict:
    return {
        "ticket": p.ticket, "symbol": p.symbol,
        "direction": "LONG" if p.type == 0 else "SHORT",
        "volume": p.volume, "open_price": p.price_open,
        "current_price": p.price_current, "profit": p.profit,
        "sl": p.sl, "tp": p.tp, "magic": p.magic, "comment": p.comment,
        "opened_at": datetime.fromtimestamp(p.time, tz=timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------- tools

@mcp.tool()
def mt5_status() -> str:
    """MT5 terminal and account status: server, login, demo/real, balance, equity, margin."""
    _connect()
    acc = mt5.account_info()
    term = mt5.terminal_info()
    if acc is None:
        return _ok({"connected": False, "error": str(mt5.last_error())})
    return _ok({
        "connected": True,
        "server": acc.server, "login": acc.login,
        "account_type": "demo" if acc.trade_mode == 0 else ("real" if acc.trade_mode == 2 else "contest"),
        "trading_allowed_by_this_server": acc.trade_mode == 0 or ALLOW_REAL,
        "balance": acc.balance, "equity": acc.equity,
        "margin": acc.margin, "free_margin": acc.margin_free,
        "currency": acc.currency, "leverage": acc.leverage,
        "terminal": term.name if term else None,
        "terminal_path": term.path if term else None,
    })


@mcp.tool()
def mt5_symbol_info(symbol: str) -> str:
    """Quote and contract details for a symbol (bid/ask, spread, contract size, lot limits)."""
    _connect()
    if not mt5.symbol_select(symbol, True):
        return _ok({"error": f"symbol '{symbol}' not found in Market Watch"})
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    return _ok({
        "symbol": info.name, "description": info.description,
        "bid": tick.bid if tick else None, "ask": tick.ask if tick else None,
        "spread_points": info.spread, "digits": info.digits,
        "contract_size": info.trade_contract_size,
        "volume_min": info.volume_min, "volume_max": info.volume_max,
        "volume_step": info.volume_step,
        "currency_base": info.currency_base, "currency_profit": info.currency_profit,
    })


@mcp.tool()
def mt5_candles(symbol: str, timeframe: str = "M15", count: int = 50) -> str:
    """Last N OHLCV candles for a symbol. timeframe: M1,M5,M15,M30,H1,H4,D1,W1,MN1.
    The LAST candle is still forming — do not treat it as closed."""
    _connect()
    tf = TIMEFRAMES.get(timeframe.upper())
    if tf is None:
        return _ok({"error": f"invalid timeframe '{timeframe}', use {list(TIMEFRAMES)}"})
    count = min(max(count, 1), 1000)
    if not mt5.symbol_select(symbol, True):
        return _ok({"error": f"symbol '{symbol}' not found"})
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    if rates is None or len(rates) == 0:
        return _ok({"error": f"no data: {mt5.last_error()}"})
    return _ok([{
        "time": datetime.fromtimestamp(int(r["time"]), tz=timezone.utc).isoformat(),
        "open": float(r["open"]), "high": float(r["high"]),
        "low": float(r["low"]), "close": float(r["close"]),
        "volume": int(r["tick_volume"]),
    } for r in rates])


@mcp.tool()
def mt5_positions(symbol: str = "", magic: int = 0) -> str:
    """Open positions, optionally filtered by symbol and/or magic number (0 = no filter)."""
    _connect()
    poss = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    poss = poss or []
    if magic:
        poss = [p for p in poss if p.magic == magic]
    return _ok({"count": len(poss), "positions": [_pos_dict(p) for p in poss]})


@mcp.tool()
def mt5_order_open(symbol: str, direction: str, lot: float,
                   sl: float = 0.0, tp: float = 0.0,
                   magic: int = 777000, comment: str = "mcp") -> str:
    """Open a market position. direction: 'LONG' or 'SHORT'. sl/tp are absolute prices (0 = none).
    DEMO-ONLY unless the server was started with MT5_ALLOW_REAL=1."""
    _connect()
    _guard_trading()
    direction = direction.upper()
    if direction not in ("LONG", "SHORT"):
        return _ok({"error": "direction must be LONG or SHORT"})
    if not mt5.symbol_select(symbol, True):
        return _ok({"error": f"symbol '{symbol}' not found"})
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return _ok({"error": "no tick data"})
    req = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": lot,
        "type": mt5.ORDER_TYPE_BUY if direction == "LONG" else mt5.ORDER_TYPE_SELL,
        "price": tick.ask if direction == "LONG" else tick.bid,
        "deviation": 30, "magic": magic, "comment": comment[:31],
        "type_time": mt5.ORDER_TIME_GTC, "type_filling": _filling(symbol),
    }
    if sl:
        req["sl"] = sl
    if tp:
        req["tp"] = tp
    res = mt5.order_send(req)
    if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
        return _ok({"error": f"order_send failed retcode={res.retcode if res else None}",
                    "detail": str(mt5.last_error())})
    return _ok({"opened": True, "ticket": res.order, "price": res.price,
                "direction": direction, "volume": lot, "magic": magic})


@mcp.tool()
def mt5_close(ticket: int = 0, symbol: str = "", magic: int = 0) -> str:
    """Close positions: a specific ticket, or all matching symbol and/or magic filters.
    At least one filter is required (refuses to blindly close everything).
    DEMO-ONLY unless the server was started with MT5_ALLOW_REAL=1."""
    _connect()
    _guard_trading()
    if not ticket and not symbol and not magic:
        return _ok({"error": "provide ticket, symbol or magic — refusing to close all positions blindly"})
    poss = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    poss = [p for p in (poss or [])
            if (not ticket or p.ticket == ticket) and (not magic or p.magic == magic)]
    if not poss:
        return _ok({"closed": 0, "note": "no matching positions"})
    results = []
    for p in poss:
        tick_data = mt5.symbol_info_tick(p.symbol)
        res = mt5.order_send({
            "action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": p.volume,
            "type": mt5.ORDER_TYPE_SELL if p.type == 0 else mt5.ORDER_TYPE_BUY,
            "position": p.ticket,
            "price": tick_data.bid if p.type == 0 else tick_data.ask,
            "deviation": 30, "magic": p.magic, "comment": "mcp_close",
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": _filling(p.symbol),
        })
        ok = res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
        results.append({"ticket": p.ticket, "closed": ok,
                        "pnl": p.profit if ok else None,
                        "retcode": res.retcode if res else None})
    return _ok({"closed": sum(1 for r in results if r["closed"]), "results": results})


@mcp.tool()
def mt5_history(days: int = 7, symbol: str = "", magic: int = 0) -> str:
    """Closed deals from the last N days, optionally filtered by symbol/magic.
    Returns entry/exit pairs with realized P&L."""
    _connect()
    frm = datetime.now(timezone.utc) - timedelta(days=days)
    deals = mt5.history_deals_get(frm, datetime.now(timezone.utc) + timedelta(days=1)) or []
    out = []
    for d in deals:
        if d.type not in (0, 1):  # buy/sell deals only
            continue
        if symbol and d.symbol != symbol:
            continue
        if magic and d.magic != magic:
            continue
        out.append({
            "time": datetime.fromtimestamp(d.time, tz=timezone.utc).isoformat(),
            "symbol": d.symbol, "type": "BUY" if d.type == 0 else "SELL",
            "entry_or_exit": "entry" if d.entry == 0 else "exit",
            "volume": d.volume, "price": d.price,
            "profit": d.profit, "magic": d.magic, "ticket": d.ticket,
        })
    realized = sum(x["profit"] for x in out if x["entry_or_exit"] == "exit")
    return _ok({"days": days, "deals": out, "realized_pnl": round(realized, 2)})


if __name__ == "__main__":
    mcp.run()

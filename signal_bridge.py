# -*- coding: utf-8 -*-
"""
Signal Bridge: TradingView Desktop (CDP) -> MetaTrader 5.

Reads the BUY/SELL shapes an indicator paints on the TradingView chart
(via the internal chartModel, CLOSED bars only) and mirrors them as market
orders in MT5 with stop-and-reverse semantics:

  BUY  on a closed bar -> close SHORT (if any), open LONG
  SELL on a closed bar -> close LONG  (if any), open SHORT

It only ever touches positions tagged with its own magic number, and refuses
to trade on non-demo accounts unless --allow-real is passed.

Usage:
  python signal_bridge.py --list-studies                 # find your study id
  python signal_bridge.py --study XXXXXX --once          # read signals, no orders
  python signal_bridge.py --study XXXXXX --test          # demo round-trip (open+close 0.01)
  python signal_bridge.py --study XXXXXX                 # run the daemon

Options (see --help): --symbol, --lot, --magic, --plot-buy, --plot-sell, --allow-real.

Finding the plot indexes: study data rows look like [time, plot_0, plot_1, ...].
Shape plots ('shapes' in metaInfo) hold 0/1 per bar. Run --once and trigger a
known signal to confirm the indexes; defaults (3, 4) fit scripts whose plots are
[line, colorer, buy_shape, sell_shape, ...].

Requirements: TradingView Desktop running with the CDP port (use the MCP's
tv_launch, or pass --remote-debugging-port=9222 yourself), and a logged-in
MT5 desktop terminal.
"""
import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import server  # CDP client from the MCP server module

LOG_CSV = Path(__file__).parent / "bridge_trades.csv"
cdp = server.client


# ------------------------------------------------------------------ TV side

def list_studies() -> None:
    studies = cdp.eval("TradingViewApi.activeChart().getAllStudies()") or []
    meta = cdp.eval(
        "({symbol: TradingViewApi.activeChart().symbol(),"
        " resolution: String(TradingViewApi.activeChart().resolution())})"
    )
    print(f"Chart: {meta['symbol']} @ {meta['resolution']}")
    for s in studies:
        plots = cdp.eval(f"""
(() => {{
  const src = TradingViewApi.activeChart().chartModel().dataSources()
    .find(s => s.id && s.id() === {json.dumps(s['id'])});
  if (!src || !src.metaInfo) return null;
  return (src.metaInfo().plots || []).map(p => p.type);
}})()
""")
        print(f"  id={s['id']:8}  {s['name']}")
        if plots:
            for i, t in enumerate(plots):
                marker = "  <-- shape plot (candidate signal)" if t == "shapes" else ""
                print(f"      value[{i + 1}] = {t}{marker}")


def read_signals(cfg, n_bars: int = 5) -> list[dict]:
    """Last n_bars CLOSED bars of the study (excludes the forming bar)."""
    rows = cdp.eval(f"""
(() => {{
  const src = TradingViewApi.activeChart().chartModel().dataSources()
    .find(s => s.id && s.id() === {json.dumps(cfg.study)});
  if (!src) return {{error: 'study {cfg.study} is not on the chart'}};
  const d = src.data();
  const li = d.lastIndex();   // forming bar -> excluded
  const out = [];
  for (let i = li - {n_bars}; i < li; i++) {{
    const r = d.search(i);
    if (r && r.value) {{
      const v = Array.from(r.value);
      out.push({{i: r.index, time: v[0], buy: v[{cfg.plot_buy}] || 0, sell: v[{cfg.plot_sell}] || 0}});
    }}
  }}
  return out;
}})()
""")
    if isinstance(rows, dict) and "error" in rows:
        raise RuntimeError(rows["error"])
    return rows or []


def chart_meta() -> dict:
    return cdp.eval(
        "({symbol: TradingViewApi.activeChart().symbol(),"
        " resolution: String(TradingViewApi.activeChart().resolution())})"
    )


# ------------------------------------------------------------------ MT5 side

import MetaTrader5 as mt5  # noqa: E402


def mt5_connect(cfg) -> None:
    kwargs = {"path": cfg.terminal} if cfg.terminal else {}
    if not mt5.initialize(**kwargs):
        raise RuntimeError(f"mt5.initialize failed: {mt5.last_error()}")
    acc = mt5.account_info()
    if acc is None:
        raise RuntimeError(f"account_info None: {mt5.last_error()}")
    if acc.trade_mode != 0 and not cfg.allow_real:
        raise RuntimeError(
            f"Account is NOT demo (trade_mode={acc.trade_mode}). Pass --allow-real to override.")
    kind = "DEMO" if acc.trade_mode == 0 else "REAL"
    print(f"[mt5] {acc.server} login={acc.login} balance={acc.balance} {acc.currency} ({kind})")
    if not mt5.symbol_select(cfg.symbol, True):
        raise RuntimeError(f"Could not select symbol {cfg.symbol}")


def _filling(symbol):
    info = mt5.symbol_info(symbol)
    if info is None:
        return mt5.ORDER_FILLING_IOC
    fm = info.filling_mode
    if fm & 2:
        return mt5.ORDER_FILLING_IOC
    if fm & 1:
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


def open_position(cfg, direction: str, comment: str):
    tick = mt5.symbol_info_tick(cfg.symbol)
    if tick is None:
        print("[mt5] no tick data"); return None, None
    res = mt5.order_send({
        "action": mt5.TRADE_ACTION_DEAL, "symbol": cfg.symbol, "volume": cfg.lot,
        "type": mt5.ORDER_TYPE_BUY if direction == "LONG" else mt5.ORDER_TYPE_SELL,
        "price": tick.ask if direction == "LONG" else tick.bid,
        "deviation": 30, "magic": cfg.magic, "comment": comment[:31],
        "type_time": mt5.ORDER_TIME_GTC, "type_filling": _filling(cfg.symbol),
    })
    if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"[mt5] order_send FAIL rc={res.retcode if res else None} | {mt5.last_error()}")
        return None, None
    return res.order, res.price


def close_ours(cfg, comment: str = "bridge_exit") -> float:
    total = 0.0
    for pos in (mt5.positions_get(symbol=cfg.symbol) or []):
        if pos.magic != cfg.magic:
            continue
        tick = mt5.symbol_info_tick(cfg.symbol)
        res = mt5.order_send({
            "action": mt5.TRADE_ACTION_DEAL, "symbol": cfg.symbol, "volume": pos.volume,
            "type": mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY,
            "position": pos.ticket,
            "price": tick.bid if pos.type == 0 else tick.ask,
            "deviation": 30, "magic": cfg.magic, "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": _filling(cfg.symbol),
        })
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            total += pos.profit
            print(f"[mt5] closed ticket={pos.ticket} pnl={pos.profit:+.2f}")
        else:
            print(f"[mt5] close FAIL ticket={pos.ticket} rc={res.retcode if res else None}")
    return total


def our_direction(cfg) -> str:
    for pos in (mt5.positions_get(symbol=cfg.symbol) or []):
        if pos.magic == cfg.magic:
            return "LONG" if pos.type == 0 else "SHORT"
    return "FLAT"


# ------------------------------------------------------------------ logging

def log_row(event: str, **kw) -> None:
    new = not LOG_CSV.exists()
    with LOG_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts_utc", "event", "signal_time", "direction", "price", "ticket", "pnl", "note"])
        w.writerow([datetime.now(timezone.utc).isoformat(timespec="seconds"), event,
                    kw.get("signal_time", ""), kw.get("direction", ""), kw.get("price", ""),
                    kw.get("ticket", ""), kw.get("pnl", ""), kw.get("note", "")])


# ------------------------------------------------------------------ modes

def _check_chart(cfg) -> dict:
    meta = chart_meta()
    if cfg.symbol.split(".")[0] not in meta["symbol"]:
        raise RuntimeError(
            f"Chart shows {meta['symbol']} but bridge is configured for {cfg.symbol}. "
            "Change the chart symbol or pass --symbol.")
    return meta


def mode_once(cfg) -> None:
    meta = _check_chart(cfg)
    print(f"[tv] chart: {meta['symbol']} @ {meta['resolution']} | study {cfg.study}")
    for r in read_signals(cfg, 10):
        ts = datetime.fromtimestamp(r["time"], tz=timezone.utc).strftime("%H:%M")
        sig = "BUY" if r["buy"] else ("SELL" if r["sell"] else "-")
        print(f"  bar {r['i']} {ts}utc  {sig}")


def mode_test(cfg) -> None:
    meta = _check_chart(cfg)
    print(f"[tv] OK: {meta['symbol']} @ {meta['resolution']}, "
          f"readable bars: {len(read_signals(cfg, 10))}")
    mt5_connect(cfg)
    if our_direction(cfg) != "FLAT":
        raise RuntimeError("Bridge already has an open position; close it before testing.")
    print(f"[test] opening LONG {cfg.lot} test order...")
    ticket, price = open_position(cfg, "LONG", "bridge_test")
    if ticket is None:
        raise RuntimeError("test order failed")
    print(f"[test] opened ticket={ticket} @ {price}")
    log_row("TEST_OPEN", direction="LONG", price=price, ticket=ticket)
    time.sleep(5)
    pnl = close_ours(cfg, "bridge_test_close")
    log_row("TEST_CLOSE", pnl=f"{pnl:.2f}")
    print(f"[test] closed. P&L={pnl:+.2f} (≈ -spread, expected)")
    mt5.shutdown()
    print("[test] ROUND-TRIP OK")


def mode_daemon(cfg) -> None:
    meta = _check_chart(cfg)
    tf_min = int(meta["resolution"]) if meta["resolution"].isdigit() else 1
    mt5_connect(cfg)
    print(f"[bridge] daemon: {meta['symbol']} @ {tf_min}m | study={cfg.study} "
          f"| magic={cfg.magic} | lot={cfg.lot}")
    last_acted = 0
    # startup: ignore historical signals — only trade what appears from now on
    sigs = [r for r in read_signals(cfg, 5) if r["buy"] or r["sell"]]
    if sigs:
        last_acted = sigs[-1]["time"]
        print(f"[bridge] ignoring signals up to "
              f"{datetime.fromtimestamp(last_acted, tz=timezone.utc)}")
    while True:
        # sleep until 10s after the next candle close
        now = time.time()
        nxt = (int(now // (tf_min * 60)) + 1) * tf_min * 60 + 10
        time.sleep(max(5, nxt - now))
        try:
            rows = read_signals(cfg, 3)
        except Exception as e:
            print(f"[bridge] TV read error: {e} — retrying in 30s")
            time.sleep(30)
            continue
        for r in rows:
            if r["time"] <= last_acted or not (r["buy"] or r["sell"]):
                continue
            direction = "LONG" if r["buy"] else "SHORT"
            cur = our_direction(cfg)
            ts = datetime.fromtimestamp(r["time"], tz=timezone.utc).strftime("%H:%M")
            print(f"[bridge] {direction} signal at bar {ts}utc (current state: {cur})")
            if cur == direction:
                print("[bridge] already in that direction, skip")
            else:
                if cur != "FLAT":
                    pnl = close_ours(cfg, "bridge_reverse")
                    log_row("CLOSE", direction=cur, pnl=f"{pnl:.2f}", signal_time=r["time"])
                ticket, price = open_position(cfg, direction, f"bridge_{direction.lower()}")
                if ticket:
                    log_row("OPEN", direction=direction, price=price, ticket=ticket,
                            signal_time=r["time"])
                    print(f"[bridge] {direction} opened ticket={ticket} @ {price}")
            last_acted = r["time"]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--study", default="", help="TradingView study id (see --list-studies)")
    ap.add_argument("--symbol", default="XAUUSD", help="MT5 symbol (default XAUUSD)")
    ap.add_argument("--lot", type=float, default=0.01)
    ap.add_argument("--magic", type=int, default=20260612,
                    help="magic number tagging this bridge's positions")
    ap.add_argument("--plot-buy", type=int, default=3,
                    help="index of the BUY shape in the study's value row (default 3)")
    ap.add_argument("--plot-sell", type=int, default=4,
                    help="index of the SELL shape in the study's value row (default 4)")
    ap.add_argument("--terminal", default="", help="path to terminal64.exe (default: auto)")
    ap.add_argument("--allow-real", action="store_true",
                    help="allow trading on a REAL account (default: demo only)")
    ap.add_argument("--list-studies", action="store_true", help="list chart studies and plot types")
    ap.add_argument("--once", action="store_true", help="read signals only, no orders")
    ap.add_argument("--test", action="store_true", help="demo round-trip test order")
    cfg = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    if cfg.list_studies:
        list_studies()
        sys.exit(0)
    if not cfg.study:
        ap.error("--study is required (find it with --list-studies)")
    if cfg.once:
        mode_once(cfg)
    elif cfg.test:
        mode_test(cfg)
    else:
        mode_daemon(cfg)

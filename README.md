# mt5-mcp

Control a **MetaTrader 5 desktop terminal** (Windows) from any MCP-compatible AI agent — Claude Code, Claude Desktop, OpenAI Codex CLI, Cursor, and others. Quotes, candles, ticks, indicators, pre-trade risk math, market and pending orders, SL/TP management, partial closes and trade history — plus a read-only event watcher for fast markets.

> Ask your agent things like *"snapshot XAUUSD"*, *"what's the margin and risk for 0.01 lot long with SL at 4150?"*, *"move my SL to break-even"* or *"close half of ticket 123456"*.

## How it works

```
┌──────────────┐   MCP (stdio)   ┌───────────────┐   MetaTrader5 pkg   ┌──────────────┐
│ Claude/Codex │ ◄─────────────► │ mt5_server.py │ ◄─────────────────► │ MT5 terminal │
└──────────────┘                 └───────────────┘                     └──────────────┘
```

The official `MetaTrader5` Python package attaches to the terminal you already have open and logged in. No broker API keys, no scraping.

## Requirements

- **Windows 10/11** (the `MetaTrader5` package is Windows-only)
- **Python 3.10+**
- Any **MT5 desktop terminal** (MetaQuotes, Exness, Pepperstone, IC Markets, …) installed and logged in. The MT5 **web terminal and mobile apps are not supported** — they have no local API.
- For trading tools: **Algo Trading** enabled in the terminal toolbar.

## Install

```bash
git clone https://github.com/akarachaichp-stack/mt5-mcp
cd mt5-mcp
pip install -r requirements.txt
```

## Register the server in your client

### Claude Code

```bash
claude mcp add mt5 -- python C:/path/to/mt5-mcp/mt5_server.py
```

### Claude Desktop

`%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mt5": {
      "command": "python",
      "args": ["C:/path/to/mt5-mcp/mt5_server.py"]
    }
  }
}
```

### OpenAI Codex CLI

`~/.codex/config.toml`:

```toml
[mcp_servers.mt5]
command = "python"
args = ["C:/path/to/mt5-mcp/mt5_server.py"]
```

### Cursor

`.cursor/mcp.json` in your project (or `~/.cursor/mcp.json` globally) — same JSON shape as Claude Desktop.

## Tools

| Tool | Description |
|---|---|
| `mt5_status` | Server, login, demo/real, balance, equity, margin, Algo Trading button state |
| `mt5_symbol_info` | Bid/ask, spread, point/tick size/tick value, min stop distance, freeze level, swaps, lot limits |
| `mt5_candles` | Last N OHLCV candles (M1…MN1) with real volume and spread — last candle is still forming |
| `mt5_ticks` | Recent ticks + spread/momentum summary |
| `mt5_indicators` | EMA, RSI, ATR (Wilder) and session VWAP on closed candles |
| `mt5_snapshot` | One compact call: equity, quote, positions, orders, M1/M5 indicators, last bars, range |
| `mt5_calc` | Pre-trade margin, loss at SL, profit at TP, reward:risk |
| `mt5_positions` | Open positions, filterable by symbol/magic |
| `mt5_orders` | Active pending orders, filterable by symbol/magic |
| `mt5_order_open` | Market order with optional SL/TP and slippage — **demo-only by default** |
| `mt5_pending_open` | Buy/Sell Limit/Stop with SL/TP and optional expiry — **demo-only by default** |
| `mt5_order_cancel` | Cancel a pending order — **demo-only by default** |
| `mt5_modify` | Change SL/TP of an open position (break-even, trailing) — **demo-only by default** |
| `mt5_close` | Close by ticket; closing by symbol/magic needs `confirm_all=True` — **demo-only by default** |
| `mt5_close_partial` | Close part of a position — **demo-only by default** |
| `mt5_history` | Closed deals with position_id, commission/swap/fee and net realized P&L |

Directions are `LONG`/`SHORT` (`BUY`/`SELL` also accepted). SL/TP are absolute prices.

Failed trade requests return the retcode with a readable reason (e.g. `10027` → Algo Trading disabled in the terminal) plus the broker's comment.

Environment variables: `MT5_TERMINAL_PATH` (default: auto-detect), `MT5_ALLOW_REAL=1` to enable trading tools on real accounts (**off by default, on purpose**).

> Symbol names depend on the broker — e.g. Exness uses suffixed names like `XAUUSDm`. Check with `mt5_symbol_info`.

## Event watcher (`mt5_watch.py`)

Read-only companion: polls every 2 s and prints one line per event, so an agent can react to fast markets without polling MCP tools continuously.

```bash
python mt5_watch.py --symbol XAUUSDm [--interval 2] [--fast 1.5] [--fast-window 15]
```

Events: `OPEN` / `CLOSE` / `PARTIAL`, `ORDER_GONE`, `PROFIT` thresholds, `NEAR_SL`, `FAST` moves, M1 `BREAKOUT`, `ERROR`.

## Smoke test

Read-only — never opens orders:

```bash
python test_mt5_server.py XAUUSDm
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `mt5.initialize failed` | Terminal not running/logged in, or auto-detect picked the wrong one — set `MT5_TERMINAL_PATH` |
| `symbol 'XAUUSD' not found` | Broker uses a different name (e.g. `XAUUSDm`) — check the Market Watch in the terminal |
| Retcode `10027` | Algo Trading is disabled — press the Algo Trading button in the terminal |
| Trading tools refuse to run | Account is not demo. Set `MT5_ALLOW_REAL=1` only if you really mean it |

## Security notes

- Trading tools are **demo-only by default**. Enabling `MT5_ALLOW_REAL=1` is your decision and your risk.
- This project is not affiliated with MetaQuotes or any broker.
- Nothing here is financial advice. Test on demo before you trust anything with money.

## Credits

Forked from [Unjoselo/tradingview-desktop-mcp](https://github.com/Unjoselo/tradingview-desktop-mcp); the TradingView Desktop server and signal bridge were removed to focus on MT5.

## License

MIT

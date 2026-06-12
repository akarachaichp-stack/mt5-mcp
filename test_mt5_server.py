# -*- coding: utf-8 -*-
"""Smoke test de las tools del MCP de MT5 (solo lectura — no abre ordenes)."""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import mt5_server as s

print("--- mt5_status ---");      print(s.mt5_status())
print("--- mt5_symbol_info ---"); print(s.mt5_symbol_info("XAUUSD"))
print("--- mt5_candles ---");     print(s.mt5_candles("XAUUSD", "M15", 3))
print("--- mt5_positions ---");   print(s.mt5_positions(symbol="XAUUSD"))
print("--- mt5_history 1d ---");  print(s.mt5_history(days=1, symbol="XAUUSD")[:1500])

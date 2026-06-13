#!/usr/bin/env python3
"""Forensic analyzer — for each losing trade in the offline backtest, dump:
  - the 10 candles before entry (so we can see the run-up)
  - the 5 candles after entry (so we can see the death)
  - all the indicators at the entry candle
  - all reasons the engine fired
The goal is to find the *pattern* across losses, not just describe them.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.backtest_offline import _patched_fetch  # noqa: E402
import signals.ohlcv  # noqa: E402

signals.ohlcv.fetch_ohlcv_df = _patched_fetch
import backtest as bt  # noqa: E402

bt.fetch_ohlcv_df = _patched_fetch


def _dump_candle(df, idx, label, atr_entry):
    """Print one candle's worth of state."""
    r = df.iloc[idx]
    ts = df.index[idx]
    move_atr = (r["close"] - df.iloc[idx - 1]["close"]) / atr_entry if atr_entry else 0
    print(
        f"  {label:>4s} {str(ts)[:16]} O={r['open']:>9,.0f} H={r['high']:>9,.0f} "
        f"L={r['low']:>9,.0f} C={r['close']:>9,.0f}  "
        f"RSI={r.get('RSI_14',0):.1f} VWAP={r.get('VWAP_24',0):>9,.0f} "
        f"ATR={r.get('ATR_14',0):>6.0f} BBu={r.get('BB_Upper',0):>9,.0f} "
        f"BBl={r.get('BB_Lower',0):>9,.0f} | move={move_atr:+.2f}ATR"
    )


def _analyze_loss(df, t):
    """Print full forensic dump for one losing trade."""
    print("\n" + "=" * 95)
    print(f"  LOSS — {t['type']} entered {t['entry_time'][:16]} @ ${t['entry_price']:,.0f}")
    print(f"         exit ${t['exit_price']:,.0f} ({t['pnl_pct']:+.2f}%) in {t['candles_held']} candles  str={t['strength']:.1f} {t['confidence']}")
    print("=" * 95)

    # Find entry index
    import pandas as pd
    ts = pd.Timestamp(t["entry_time"])
    try:
        i = df.index.get_loc(ts)
    except KeyError:
        i = df.index.get_indexer([ts], method="nearest")[0]

    atr_entry = df.iloc[i].get("ATR_14", 0)

    print(f"\n  Reasons engine cited:")
    for r in (t.get("reasons") or "").split(";"):
        if r.strip():
            print(f"    • {r.strip()}")

    # Pre-entry context (last 10 candles)
    print(f"\n  Pre-entry (10c):  close=$ entries.  Look for run-up / over-extension.")
    for j in range(max(0, i - 10), i):
        _dump_candle(df, j, "pre", atr_entry)
    _dump_candle(df, i, "ENT", atr_entry)

    # Post-entry path
    print(f"\n  Post-entry path:  did price reverse immediately?  SL = ${t['stop_loss']:,.0f}  TP = ${t['take_profit']:,.0f}")
    for j in range(i + 1, min(len(df), i + 1 + min(t["candles_held"] + 2, 8))):
        _dump_candle(df, j, "pos", atr_entry)

    # Mean-reversion check: was entry well above EMA200?
    r = df.iloc[i]
    ema200 = r.get("EMA_200", 0)
    vwap = r.get("VWAP_24", 0)
    bb_upper = r.get("BB_Upper", 0)
    bb_lower = r.get("BB_Lower", 0)
    atr = r.get("ATR_14", 0)
    close = r["close"]
    print(f"\n  Position vs structure:")
    print(f"    close − EMA200 = {(close - ema200):>+8.0f}  ({(close - ema200) / atr:+.2f} ATR)" if atr else "    no atr")
    print(f"    close − VWAP   = {(close - vwap):>+8.0f}  ({(close - vwap) / atr:+.2f} ATR)" if atr else "")
    if bb_upper and bb_lower:
        bb_pct = (close - bb_lower) / (bb_upper - bb_lower)
        print(f"    BB position   = {bb_pct:>+.2f}  (0=lower, 1=upper)")

    # How many of the prior 5 closes were higher than this one? (overextension test)
    higher = sum(1 for k in range(max(0, i - 5), i) if df.iloc[k]["close"] < close)
    print(f"    candles below entry in last 5 = {higher}/5 (overextension if 5)")

    # Pullback test: was the lowest low in last 5 less than entry by >= 0.5×ATR?
    low5 = df.iloc[max(0, i - 5):i + 1]["low"].min()
    pulled = (close - low5) / atr if atr else 0
    print(f"    pullback room  = {pulled:.2f}×ATR from recent low (>0.5 = no pullback)")


def main():
    print("Loading and running both backtests...\n")
    for mode in ("futures", "spot"):
        tf = "4h" if mode == "spot" else "1h"
        print(f"\n#### {mode.upper()} ({tf}) ####")
        trades, stats = bt.run_backtest(symbol="BTC/USDT", timeframe=tf, mode=mode)
        losses = [t for t in trades if t["outcome"] == "LOSS"]
        if not losses:
            print("  no losses — nothing to forensic")
            continue
        df = _patched_fetch(timeframe=tf, limit=10000, vwap_period=24 if tf == "1h" else 6)
        for t in losses:
            _analyze_loss(df, t)


if __name__ == "__main__":
    main()

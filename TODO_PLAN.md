# TODO_PLAN — post audit #12

State at time of writing: audits #8–#12 committed (e67124a). DB + adaptive
threshold state reset 2026-06-13-2315. Backup at `data/backups/*-2026-06-13-2315`.

The 90-day backtest is exhausted as a tuning sample — every parameter has
been calibrated against it, so more iterations on the same data will overfit.
The plan below is about **validating in conditions we haven't seen** and
deciding what graduates to live.

---

## Phase 1 — Forward paper-trade (≈ 1 week)

The single highest-leverage thing to do next. The bot runs on live OHLCV,
emits paper trades to `data/signal_history.db`, and writes every cycle to
`data/cycle_log` (35+ columns).

### Start

```bash
source venv/bin/activate
nohup python3 run_bot.py --loop 60 > logs/paper_$(date +%F).log 2>&1 &
echo $! > .bot.pid
```

### Monitor (any time)

```bash
# Last 50 lines of the most recent log
tail -50 logs/paper_*.log | less

# Each gate fires with a distinct prefix — confirm gates are actually catching things
grep -E "No-chase|Counter-trend|Anti-FOMO|Short-term down|Entry wick|R:R .* below|SL at swing|SL capped" logs/paper_*.log

# Live signal cadence
sqlite3 data/signal_history.db \
  "SELECT mode, type, COUNT(*) FROM cycle_log GROUP BY mode, type"

# Open paper positions
sqlite3 data/signal_history.db \
  "SELECT id, mode, type, entry_price, stop_loss, take_profit, created_at FROM paper_positions WHERE outcome IS NULL"
```

### Stop

```bash
kill "$(cat .bot.pid)" && rm .bot.pid
```

---

## Phase 2 — 24h checkpoint

After the first full day. Three questions:

1. **Did any gate fire?** Run the `grep` above. If zero matches, the gates
   are dormant and we're flying without them — that's a problem because the
   90-day backtest leaned on them. Likely cause: live HTF data is shaped
   differently from the resampled backtest HTF. Diagnose before continuing.

2. **Are there open positions?** If yes, snapshot them so you can see how
   they exit:
   ```bash
   sqlite3 data/signal_history.db \
     ".headers on" \
     "SELECT * FROM paper_positions WHERE outcome IS NULL" > snapshot_24h.txt
   ```

3. **Cycle count sanity.** With `--loop 60`, expect ~24 cycles in 24h.
   ```bash
   sqlite3 data/signal_history.db \
     "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM cycle_log"
   ```
   Materially fewer → loop is crashing or stalling.

---

## Phase 3 — 7-day decision point

After ≈ 1 week of paper-trade, three possible outcomes — each maps to a
concrete next action.

### Outcome A — WR ≥ 50%, ≥ 5 closed trades

Gates are validated in live. Action: **port the futures BE-cushion to live**.

- File: `trading/paper.py` lines ~236 (BUY) and ~311 (SELL).
- Currently the spot branch sets `new_sl = entry − 0.5×ATR` after TP1; the
  futures branch still snaps to `entry`. Mirror the spot logic for futures.
- Re-run `scripts/backtest_offline.py` to confirm no regression.
- Then `git push origin develop`.

### Outcome B — WR < 50% live, < 5 closed trades

Two possibilities:
- Gates too tight → very few signals → not enough sample. Loosen one
  parameter at a time (FOMO 1.25→1.5, momentum −0.5→−0.7, wick 50→60).
  Run `scripts/loss_forensics.py` on losses if any.
- Gates fine, market conditions just produced no setups. Wait another week.

### Outcome C — WR < 50% live, ≥ 5 closed trades

Real signal quality problem. Action: **forensic the live losses**.

```bash
# Adapt loss_forensics.py to read from signal_history.db (not CSV)
# Each LOSS row has entry_time → look up the corresponding cycle_log row
sqlite3 data/signal_history.db \
  "SELECT p.entry_time, p.entry_price, p.outcome, p.pnl_pct, c.reasons
   FROM paper_positions p
   LEFT JOIN cycle_log c ON c.timestamp = p.entry_time
   WHERE p.outcome = 'LOSS' ORDER BY p.entry_time DESC LIMIT 10"
```

Look for a third pattern not caught by audits #8–#12. Most likely candidates
based on what's already in the engine:
- Entering on macro news window we don't have CSV for
- Funding rate flip (only checked at scoring, not at entry quality)
- Crypto-specific event-driven moves (DXY swings, BTC.D crashes)

---

## Phase 4 — Out-of-sample validation (parallel work)

The 90-day window was BTC +15.5%. The gates haven't seen chop, a flash
crash, or sustained downtrend. Pull a longer, mixed-regime dataset and
re-run.

```bash
source venv/bin/activate
python3 -c "
from signals.market_data import exchange
import pandas as pd
for tf, fn in [('1h','data/btc_1h_180d.csv'),('4h','data/btc_4h_180d.csv')]:
    bars = exchange.fetch_ohlcv('BTC/USDT', tf, limit=180*(24 if tf=='1h' else 6))
    df = pd.DataFrame(bars, columns=['timestamp','open','high','low','close','volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.to_csv(fn, index=False); print(fn, len(df))
"
```

Then point `scripts/backtest_offline.py:CSV_MAP` at the 180d files and
rerun. Expected results:

| Condition | Read-out |
|-----------|----------|
| PF > 1 on both modes | Gates generalise → confidence ↑ |
| PF drops below 1 on one mode | Mode-specific tuning needed |
| Both modes PF < 1 | Likely overfit to the bull regime; loosen by reverting one gate at a time and seeing which restores PF |

---

## Known risks / open questions

1. **Overfit on gate thresholds.** 1.25×ATR FOMO, −0.5×ATR momentum, 50%
   wick, 1.5 R:R — all chosen against the same 90-day sample. Phase 4 is
   the primary mitigation.

2. **Backtest HTF ≠ live HTF.** Backtest resamples 1H → 4H/1D in-process.
   Live calls the exchange separately. There may be small differences in
   EMA200 placement near tf boundaries that change which signals fire.
   Watch the gate-firing distribution in Phase 2 logs.

3. **Adaptive threshold cold start.** Reset means threshold sits at base
   (5.2 / 4.3) until 72h of signal data accumulates. During that window
   the bot may emit more signals than steady-state.

4. **SELL signals untested.** The 90-day sample had no qualifying SELL
   that passed all gates. The counter-trend block, FOMO/momentum SELL
   mirrors, and wick SELL gate are all logically symmetric but
   empirically unverified. If Phase 1 produces a SELL, it's worth
   forensic-reviewing it even if it wins.

---

## Quick reference — useful commands

```bash
# Restart bot (after a code change)
kill "$(cat .bot.pid 2>/dev/null)"; nohup python3 run_bot.py --loop 60 > logs/paper_$(date +%F).log 2>&1 & echo $! > .bot.pid

# All-time stats per mode
sqlite3 data/signal_history.db "
  SELECT mode, outcome, COUNT(*) AS n, ROUND(AVG(pnl_pct),2) AS avg_pnl
  FROM paper_positions WHERE outcome IS NOT NULL
  GROUP BY mode, outcome
"

# Latest 10 closed trades
sqlite3 data/signal_history.db "
  SELECT mode, type, entry_price, exit_price, outcome, pnl_pct, created_at
  FROM paper_positions WHERE outcome IS NOT NULL
  ORDER BY id DESC LIMIT 10
"

# Re-run offline backtest after any engine change
source venv/bin/activate && python3 scripts/backtest_offline.py --mode both

# Re-run loss forensics on backtest losses
source venv/bin/activate && python3 scripts/loss_forensics.py

# Restore the pre-reset state (panic button)
B=data/backups
T=2026-06-13-2315
cp $B/signal_history.db.bak-$T data/signal_history.db
cp $B/signal_history.csv.bak-$T data/signal_history.csv
cp $B/threshold_state.json.bak-$T data/threshold_state.json
cp $B/spot_threshold_state.json.bak-$T data/spot_threshold_state.json
```

---

## Audit checklist (work to land *after* validation)

- [ ] Port futures TP1 BE-cushion (`trading/paper.py`) — gated on Phase 3 Outcome A
- [ ] Push `develop` to `origin/develop` — gated on Phase 3 Outcome A
- [ ] Tag a release (`v2.8.0-rc2`?) — gated on 14 days clean paper-trade
- [ ] Decide whether to commit `data/btc_*_90d.csv` and `data/btc_*_180d.csv` to git or keep them local — they're inputs, not state, and useful for reproducible backtests
- [ ] Optional: extend `loss_forensics.py` to also read from `signal_history.db` (live losses), not just the offline backtest output
- [ ] Optional: write a `scripts/sweep.py` that grid-searches gate thresholds against the 180-day CSV and reports the PF surface — surfaces the overfit risk quantitatively

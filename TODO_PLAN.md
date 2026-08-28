# TODO_PLAN — post audit #12

State: audits #8–#12 committed (`e67124a`), `develop` at `ce85bc1` and already
pushed to `origin/develop`. Nothing has moved since 2026-06-13.

**The paper-trade run in Phase 1 has never actually been started.** Last bot
activity was 2026-06-13 22:01; the DB was reset at 2026-06-13-2315 with the
backup at `data/backups/*-2026-06-13-2315` (797 signals, 10 positions, 797
cycle_log rows). `data/signal_history.db` currently holds an **empty** schema.

The 90-day backtest is exhausted as a tuning sample — every parameter has
been calibrated against it, so more iterations on the same data will overfit.
The plan below is about **validating in conditions we haven't seen** and
deciding what graduates to live.

> Supersedes `PAPER_TRADE_PLAN.md` (v2.8.0-rc1, 2026-05-14), now deleted. That
> plan called for 2 weeks of paper validation *before* tagging `v2.8.0`, but
> `v2.8.0` (`0f0c2c7`) was tagged the same day as `v2.8.0-rc1` (`e0a7d08`) and
> the validation never ran. Its still-useful parts — weekly analysis queries,
> red-flag table, tuning levers — are folded in below.

---

## Phase 0 — Pre-flight (before starting the loop)

- [ ] `mkdir -p logs` — Phase 1's `nohup` redirect fails without it
- [ ] Activate venv: `source venv/bin/activate`
- [ ] **Decide the DB starting point.** `data/signal_history.db` is currently
      an empty schema. Either start clean, or restore the pre-reset state:
      ```bash
      B=data/backups; T=2026-06-13-2315
      cp $B/signal_history.db.bak-$T          data/signal_history.db
      cp $B/signal_history.csv.bak-$T         data/signal_history.csv
      cp $B/threshold_state.json.bak-$T       data/threshold_state.json
      cp $B/spot_threshold_state.json.bak-$T  data/spot_threshold_state.json
      ```
      Starting clean means the adaptive threshold cold-starts (see risk #3).
- [ ] Fresh backup before the run: `cp -r data data.backup-$(date +%Y%m%d)`
- [ ] Verify migrations applied:
      ```bash
      sqlite3 data/signal_history.db "PRAGMA table_info(paper_positions)" | grep exit_price
      sqlite3 data/signal_history.db ".tables" | grep signal_blocks
      ```
      Expected: `exit_price` column present, `signal_blocks` table present.
- [ ] Verify `.env` if using Telegram: `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` set
- [ ] Smoke test one cycle — `python3 run_bot.py` (no `--loop`); Phases 1–4
      must complete without an exception

---

## Phase 1 — Forward paper-trade (≈ 1 week)

The single highest-leverage thing to do next. The bot runs on live OHLCV,
emits paper trades to `data/signal_history.db`, and writes every cycle to
`data/cycle_log` (35+ columns).

### Start

```bash
source venv/bin/activate
mkdir -p logs
nohup python3 run_bot.py --loop 60 > logs/paper_$(date +%F).log 2>&1 &
echo $! > .bot.pid
```

### Monitor (any time)

```bash
# Last 50 lines of the most recent log
tail -50 logs/paper_*.log | less

# Each gate fires with a distinct prefix — confirm gates are actually catching things
grep -E "No-chase|Counter-trend|Anti-FOMO|Short-term down|Entry wick|R:R .* below|SL at swing|SL capped" logs/paper_*.log

# Errors since start
grep -E 'ERROR|CRITICAL' spotsignal.log | tail -10

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

## Phase 2b — Weekly analysis (≈ 30 min/week)

Carried over from the v2.8.0-rc1 plan. Run these at each week boundary.

### a. Frequency vs old baseline
```sql
SELECT mode, type, COUNT(*) FROM cycle_log
WHERE timestamp >= datetime('now','-7 days')
GROUP BY mode, type;
```
**Baseline:** the old code (May 9–13 live) produced ~3–5 trades/week. New code
below 50% of that → gates are too tight.

### b. Gate-block distribution
```sql
SELECT mode, gate, COUNT(*) AS n FROM signal_blocks
WHERE timestamp >= datetime('now','-7 days')
GROUP BY mode, gate ORDER BY n DESC LIMIT 15;
```
**Red flag:** any single gate > 60% of all blocks → likely too restrictive for
the current regime.

### c. Win rate & profit factor
```sql
SELECT
  mode,
  SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) AS wins,
  SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END) AS losses,
  ROUND(100.0 * SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) / NULLIF(SUM(CASE WHEN outcome IN ('WIN','LOSS') THEN 1 ELSE 0 END),0), 1) AS wr_pct,
  ROUND(SUM(pnl_pct), 2) AS total_pnl_pct,
  ROUND(AVG(pnl_pct), 3) AS avg_pnl_pct
FROM paper_positions
WHERE closed_at IS NOT NULL
GROUP BY mode;
```
**Target:** WR ≥ 50% with ≥ 5 closed trades (Phase 3 Outcome A), PF > 1.2.

> The superseded v2.8.0-rc1 plan used a looser bar — WR > 35% futures, > 45%
> spot. Audits #8–#12 tightened entry quality specifically to raise WR, so the
> stricter ≥ 50% applies. Fall back to the looser numbers only as a deliberate
> decision, and record it here if you do.

### d. PnL by F&G zone (is the F&G signal worth anything?)
```sql
SELECT
  CASE WHEN s.fear_greed <= 25 THEN 'extreme-fear'
       WHEN s.fear_greed <= 45 THEN 'fear'
       WHEN s.fear_greed <= 55 THEN 'neutral'
       WHEN s.fear_greed <= 75 THEN 'greed'
       ELSE 'extreme-greed' END AS fng_zone,
  COUNT(*) AS n,
  ROUND(AVG(p.pnl_pct), 3) AS avg_pnl
FROM signals s JOIN paper_positions p ON s.id = p.signal_id
WHERE p.outcome IN ('WIN','LOSS')
GROUP BY fng_zone ORDER BY avg_pnl DESC;
```

### e. Daily P&L curve
```sql
SELECT
  date(closed_at) AS day,
  mode,
  ROUND(SUM(pnl_pct), 2) AS pnl_pct,
  COUNT(*) AS trades
FROM paper_positions
WHERE closed_at >= datetime('now','-14 days')
GROUP BY day, mode ORDER BY day, mode;
```

---

## Red flags — stop and investigate

| Indicator | Action |
|---|---|
| Equity < 92% of starting balance | Stop, review losing trades, consider rollback |
| 5+ consecutive losses in 24h | Pause, check for regime mismatch |
| Single gate > 80% of blocks | Tune (see the levers below) |
| 0 trades for 5+ consecutive days | Gate combination too tight → tune |
| `daily_loss_limit` breaker fires > 1×/week | Lower position size or raise threshold |
| Exception > 3× in `spotsignal.log` | Fix root cause before continuing |

---

## Tuning levers (mid-period adjustments)

Edit `config.py` and restart the bot — no rollback needed. All five knobs
verified present as of 2026-08-29.

| Symptom | Lever |
|---|---|
| Frequency down > 50% | Lower `min_initial_confidence` to `"WEAK"` in `RISK_CONFIG["pyramid"]` (`config.py`) |
| Futures SHORT never opens | Check the dominant gate; if `trend_confluence`, loosen to 1/3 in `_trend_confluence_for_direction` (`run_bot.py`) |
| Spot pyramid blocked by aggregate risk | Raise `max_aggregate_risk_pct` 5.0 → 6.0 |
| VOL_EXIT too frequent | `vol_expansion_exit_mult` 2.0 → 2.5 |
| TIME_EXIT too frequent | `max_position_hours_spot` / `max_position_hours_futures` 72 → 96 |

**Every tune gets a CHANGELOG.md entry.**

---

## Phase 3 — 7-day decision point

After ≈ 1 week of paper-trade, three possible outcomes — each maps to a
concrete next action.

### Outcome A — WR ≥ 50%, ≥ 5 closed trades

Gates are validated in live. Action: **port the futures BE-cushion to live**.

- File: `trading/paper.py` — BUY branch at line ~247, SELL branch at line ~330.
- The spot path sets `new_sl = entry ∓ 0.5×ATR` after TP1; the futures path
  still snaps to `entry`. The in-code comment gates this on futures PF
  clearing 1.0 — Phase 1 is what supplies that evidence. Mirror the spot logic.
- Re-run `scripts/backtest_offline.py` to confirm no regression.
- `develop` is already pushed, so this becomes a new commit on top.

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

3. **Adaptive threshold cold start — now certain, not just possible.**
   `data/threshold_state.json` and `spot_threshold_state.json` no longer
   exist, so unless Phase 0 restores them the threshold sits at base
   (5.2 / 4.3) until 72h of signal data accumulates. Expect more signals
   than steady-state during that window.

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

# Bot still alive?
ps aux | grep run_bot | grep -v grep

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

# Trades closed in the last 24h
sqlite3 data/signal_history.db "
  SELECT mode, type, outcome, pnl_pct, closed_at
  FROM paper_positions
  WHERE closed_at >= datetime('now','-1 day')
  ORDER BY closed_at DESC
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

## Rollback (if paper-trade fails badly)

Multiple red flags, or PnL < −5%. `v2.8.0` (`0f0c2c7`) is already on `main`;
`develop` carries the 3 unmerged audit commits on top of it.

```bash
# Stop the bot first
kill "$(cat .bot.pid)" && rm .bot.pid

# Preserve the failed run's data
mv data data.rc-failed-$(date +%Y%m%d)
cp -r data.backup-<original-date> data

# Drop back to the last released state
git checkout v2.8.0          # 0f0c2c7 — current main
# or, to go back further:
git checkout v2.7.0          # 09f0ba6
```

> The superseded plan named `cd1f378` as "the v2.7.0 state" — that is wrong.
> `cd1f378` is an ancestor of the `v2.7.0` tag (`09f0ba6`), not the tag itself.

---

## Audit checklist (work to land *after* validation)

- [ ] Port futures TP1 BE-cushion (`trading/paper.py:247` / `:330`) — gated on Phase 3 Outcome A
- [x] Push `develop` to `origin/develop` — done, `origin/develop` == `ce85bc1`
- [ ] Tag a release (`v2.8.1`? `v2.9.0`?) — gated on 14 days clean paper-trade.
      Note `v2.8.0` and `v2.8.0-rc1` are both already taken.
- [ ] Decide whether to commit `data/btc_*_90d.csv` and `data/btc_*_180d.csv` to git or keep them local — they're inputs, not state, and useful for reproducible backtests
- [ ] Decide whether `data/` belongs in `.gitignore` — it is currently neither tracked nor ignored, so it shows up as untracked noise in every `git status`
- [ ] Optional: extend `loss_forensics.py` to also read from `signal_history.db` (live losses), not just the offline backtest output
- [ ] Optional: write a `scripts/sweep.py` that grid-searches gate thresholds against the 180-day CSV and reports the PF surface — surfaces the overfit risk quantitatively

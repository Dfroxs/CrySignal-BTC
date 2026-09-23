# Paper Run — Operations Note

Written 2026-08-30. Read this first when you come back to the project.

---

## What is running

| | |
|---|---|
| Host | `45.151.155.178` — Kamatera, Singapore, Ubuntu 24.04 |
| Login | `ssh dmonk@45.151.155.178` (SSH key, password login being phased out) |
| Path | `~/playground/CrySignal-BTC` |
| Service | `spotsignal.service` — systemd, `Restart=always` |
| Version | `v2.9.0` at `b10f34e` on `main` |
| Started | 2026-08-30 00:07:43 UTC |
| Schedule | full cycle at `:01`, position check at `:31`, every hour |

Spec: 1 GB RAM / 1 shared core / 20 GB. Measured need: ~125 MB idle, ~320 MB for
six seconds per cycle. A 2 GB swapfile is configured as insurance with
`swappiness=10` — swap is a floor under the hourly spike, not a working store.

---

## The one rule

> **Change nothing while the run is live.** No threshold, no weight, no gate, no
> risk parameter.

A parameter edit mid-run voids the sample. That is exactly how the previous
attempt at this (PAPER_TRADE_PLAN.md, May) ended up unusable — it was never
started, and the parameters kept moving.

`data/paper_run_manifest.json` on the server pins what this run is testing: the
commit, every threshold, the risk config, and the row counts that predate it.
If you must change something, note it and apply it to the *next* run.

Work continues on `develop`. `main` is what the server tracks.

---

## Weekly check — five minutes

```bash
ssh dmonk@45.151.155.178
cd ~/playground/CrySignal-BTC

systemctl status spotsignal          # active (running)?
./venv/bin/python analyze.py         # the full report
```

### The silent-failure check — do not skip

If Binance becomes unreachable, the bot does **not** error. Funding, L/S, open
interest, basis and taker ratio all degrade to NEUTRAL and the system keeps
running as a different, weaker system — 7.5 of the 26.5-point futures ceiling,
gone, with nothing in the logs to tell you.

```bash
./venv/bin/python -c "
import sqlite3; c = sqlite3.connect('data/signal_history.db')
n = c.execute(\"SELECT COUNT(*) FROM cycle_log WHERE mode='futures' AND funding_rate=0\").fetchone()[0]
print(f'{n} cycles ran without futures data')"
```

Anything above zero means those cycles are contaminated. Note the dates so they
can be excluded from analysis.

---

## What to expect

The system fires roughly **12–15 signals a year** in backtest. **Days of nothing
but HOLD are normal**, not a malfunction.

What accumulates first is `cycle_log` (every cycle, including HOLDs) and
`signal_blocks` (every gate rejection, with its reason). The second table is the
most informative thing in the early weeks — it tells you which gate is holding
signals back.

At roughly **300 rows of `cycle_log`** — about 8–10 days — there is enough data
for a visual report to be worth building.

---

## Open items, in priority order

### 1. Prove the reboot survives — 40 seconds, do it first
The entire reason we chose systemd over `nohup`. If the unit is not properly
enabled, you find out now instead of three weeks from now after an unattended
kernel update reboots the box.

```bash
sudo reboot
# wait ~40s
ssh dmonk@45.151.155.178
systemctl status spotsignal          # must be active without you touching it
```

### 2. Back up the database
The server can be rebuilt in 15 minutes with `deploy/setup.sh`. **Weeks of paper
run data cannot.** The database is the asset.

```bash
crontab -e
```
```
0 3 * * * cp ~/playground/CrySignal-BTC/data/signal_history.db ~/playground/CrySignal-BTC/data/backups/db-$(date +\%Y\%m\%d).db 2>/dev/null
```

And pull a copy to the Mac occasionally:
```bash
scp dmonk@45.151.155.178:~/playground/CrySignal-BTC/data/signal_history.db ~/Downloads/
```

### 3. Verify the ForexFactory timezone — never checked
`signals/sentiment.py:check_upcoming_macro_events()` assumes the calendar XML
timestamps are `America/New_York`. If the feed's timezone setting differs, the
2-hour macro gate is off by hours — and that gate **force-closes every open
position**. One fetch of the feed settles it.

### 4. Finish the security hardening
Started but incomplete:
```bash
sudo ufw default deny incoming && sudo ufw default allow outgoing
sudo ufw allow 22/tcp && sudo ufw enable
sudo sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sudo systemctl restart ssh
sudo apt install -y unattended-upgrades
```
Already done: non-root user, SSH key, fail2ban (confirmed working — 30 failed
attempts and 4 bans within hours of the server coming online).

### 5. Live vs backtest consistency — once there are a few days of data
Replay the same window through `backtest.py --start/--end` and compare its
signals against `cycle_log` day by day. Divergence on conditions both paths can
see is a bug. This validates *implementation*, not edge — it is the cheap way to
catch the remaining drift after today's ten backtest fixes.

### 6. Visual report — at ~300 cycle_log rows
Equity curve, `gap_to_fire` over time, and the `signal_blocks` distribution.
Those three are genuinely hard to read as text. A periodic static report, not a
live dashboard — the system acts once a month; there is no stream to watch.

---

## Context worth remembering

### What today's work actually established
The backtest — the only instrument that had ever judged this strategy — carried
ten defects, including a futures run that simulated ~30 days and reported 90,
and an HTF condition that was structurally disabled on spot. Every parameter in
`config.py` was tuned against those broken numbers.

### What the fixed tools then said
- **Almost nothing in the scoring stack is stable across years.** Tested 2024
  against 2025 in both modes: `ema200` runs +3.5 then −2.2, `macd` +1.4 then
  −4.7, `mfi` and `rsi` flip sign *significantly in both directions*. Only `htf`
  keeps its sign, and it is negative in all four samples.
- **The gates thin rather than select.** Trades they allow are worse per trade
  (−0.799%) than the ones they reject (−0.557%). Total loss falls only because
  the count does — what any filter of that severity achieves.
- **Pruning the nine worst conditions made results worse out-of-sample**, so it
  was measured and not shipped. `config.DISABLED_CONDITIONS` is empty; the
  machinery stays for a better-evidenced attempt.

### What is and is not known
Over 2024 and 2025 the unmodified system is profitable on spot in both years
(+3.71% PF 1.58, +0.55% PF 1.23) and on futures 2024 H1 (+0.64% PF 1.39).

That is **roughly 15 trades**. It is not an edge, it is a hint. Distinguishing a
55% win rate from 50% needs hundreds of trades; at 12–15 signals a year that is
years away. This is the structural problem the project has not solved, and no
amount of tuning addresses it.

### Two conclusions that were overturned within a day
Recorded so the same mistakes are recognisable next time:

1. *"The gates are carrying the system"* — from comparing sums across unequal
   trade counts. The per-trade figures said the opposite.
2. *"Trend-following works, mean-reversion doesn't"* — from two **overlapping**
   2026 samples. It did not survive a genuine out-of-sample test.

Both were confident, both were wrong, and both came from the same root cause:
measuring on data that was not independent.

---

## Command reference

```bash
# status
systemctl status spotsignal
tail -f ~/playground/CrySignal-BTC/paper_run.log
journalctl -u spotsignal -n 50

# stop / start  (stopping leaves a hole in the sample — note the times)
sudo systemctl stop spotsignal
sudo systemctl start spotsignal

# analysis  (run on the Mac for the heavy ones — 1 GB is too tight)
./venv/bin/python analyze.py
./venv/bin/python analyze.py --section gates
./venv/bin/python backtest.py --days 180 --walk-forward 6
./venv/bin/python backtest.py --days 180 --gates
./venv/bin/python scripts/condition_ic.py --start 2025-01-01 --end 2025-12-31 --horizons 3,6,12,24

# update the server after a new release is merged to main
cd ~/playground/CrySignal-BTC && git pull && sudo systemctl restart spotsignal
```

`backtest.py` and `scripts/condition_ic.py` load frames of thousands of rows and
can exhaust 1 GB. **Run analysis on the Mac; let the VPS run the bot.**

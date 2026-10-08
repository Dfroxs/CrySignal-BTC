# Paper Run — Operations Note

**Rewritten 2026-10-04, when run 2 started. Read this first when you come back.**

---

## New session? Do this first

```bash
bash scripts/morning_check.sh
```

Both bots, one command, exits non-zero if anything needs attention. Expect
`>>> semua bersih <<<`. If something is flagged, the line names it; the rest of this
document explains the context.

**Checkpoints, as of 2026-10-08 19:50 UTC** (both bots checked; `morning_check.sh` clean):

1. ✅ **nakhoda-alloc crosses a UTC midnight on its own.** Unprompted rebalances at
   ~00:01–00:02 UTC on 10-05, 10-06, 10-07 and 10-08, none triggered by a restart.
2. ✅ **spotsignal run 2 completes full days.** 136 cycles since 07:37 UTC 2026-10-04,
   `contributions` NULL on **0** of them.
3. ✅ **spotsignal veto-reason fix — seen in production.** `⛔` survives in `reasons` on
   35 futures and 12 spot cycles of run 2 (run 1: 3 of 201 vetoed cycles).
4. ⏳ **spotsignal `_net_pnl` cost fix — still unproven in the field.** Run 2 has opened
   **no positions yet**; the 3 rows in `paper_positions` all predate it. When the first
   run-2 trade hits TP1, check that `pnl_pct` reflects a full round trip on both halves.
   **This is the only checkpoint left open.**

**Waiting on `develop`, for run 3 — do not deploy into run 2:**
`notifier/telegram.py`, `run_bot.py`, `signals/market_data.py`, `signals/spot.py`,
`signals/futures.py` — the unopenable-signal alert and the controller counting opens
instead of fires. See `docs/superpowers/specs/2026-10-04-deadzone-results.md` for why,
and note that neither is a route to profit: that experiment's H1 failed.

**allocbot's demo gate: two weeks elapsed 2026-10-08 04:17 UTC, clean** — zero `ERROR` /
`Traceback` in its journal since 2026-09-24, no missed daily decision. (2026-10-01's
rebalance was held ~20 min for a Tankan event and then ran — designed behaviour.) Read
`../Nakhoda/docs/OOS-FINDING-2026-09-08.md` before treating this pass as permission to
trade real money — the rule failed out of domain and its timing is indistinguishable from
matched random entry outside crypto. The pass proves the plumbing, not the edge.

---

## Two bots are running, on one VPS

| | **spotsignal** | **nakhoda-alloc** |
|---|---|---|
| Repo | `~/playground/CrySignal-BTC` | `~/playground/Nakhoda` |
| Service | `spotsignal.service` (system) | `nakhoda-alloc.service` (**user scope**) |
| Runs as | `dmonk` | `dmonk` |
| Started | **run 2: 2026-10-04 07:37 UTC** | 2026-09-24 04:17 UTC |
| Touches an exchange? | **No.** Zero API keys, zero order calls. Paper positions live in SQLite. | **Yes** — real orders on OKX **Demo** (virtual money) |
| Strategy | 22 conditions, 5 veto gates, adaptive threshold, BTC only | Donchian 20/10 across 10 coins, daily, no stops |

Host `45.151.155.178` — Kamatera, Singapore, Ubuntu 24.04, **960 MB RAM, 1 core**.
`ssh dmonk@45.151.155.178`. Roughly 380 MB free with both bots up.

**There is no passwordless sudo**, and `sudo` needs a real TTY — neither Claude Code's
Bash tool nor its `!` prefix provides one, so anything needing root has to be pasted into
a terminal application. Two consequences that cost a previous session time:
- `nakhoda-alloc` is a **user-scope** unit (`systemctl --user ...`, `journalctl --user -u ...`)
  with `loginctl enable-linger` set — that needs no root and still survives reboot.
  The runbook in Nakhoda's CLAUDE.md said `sudo systemctl`; that was wrong and is fixed.
- `spotsignal` is system-scope, but its process runs as `dmonk` and the unit is
  `Restart=always` / `RestartSec=30`. So `pkill -TERM -f run_bot.py` **is** a restart —
  systemd brings it back on the code then on disk. That is how run 2 was started.

---

## The one rule

> **Change nothing while a run is live.** No threshold, no weight, no gate, no risk
> parameter.

Work lands on `develop`. `main` is what the server tracks, and it is updated **only
between runs**. `data/paper_run_manifest.json` pins what the current run is testing;
`paper_run_manifest.run1.json` beside it is the previous run's record.

---

## Run 2 — what changed from run 1

Run 1: 2026-08-30 → 2026-10-04. **1,055 cycles, 3 spot positions, 0 futures positions.**
Final database backed up to `data/backups/FINAL-run1-20261004-signal_history.db` (verified
`integrity_check: ok`) and to the Mac. Run 2 keeps writing to the **same** database —
filter on `timestamp >= started_at` from the manifest, and note its warning that the 3
positions predate it, so P&L and drawdown do **not** start from zero.

Runtime delta `b913a9f → ddd2b65`, 143 insertions across four files. **`config.py` is not
among them. No threshold, weight, gate or risk parameter moved.**

1. **`signals/ohlcv.py` — the engine no longer scores an unclosed bar.** It used to:
   `fetch_ohlcv_df` returned the exchange's forming candle as the last row and the engine
   scores `df.iloc[-1]`. The bot runs at `:01`, so that bar was **one minute old** — open,
   high, low and close within a few dollars, every rolling indicator ending on a stub, and
   the entry-wick gate dividing by a range of a few dollars. Measured over 1,200 candles:
   **13.6% of verdicts differ**, up to 3.75 of SPOT_MAX_SCORE 22.50. This is also what
   finally makes `backtest.py` and live comparable — before it, no backtest figure this
   repo produced described the system that was running.
2. **`trading/paper.py` — a partial exit now pays a full round trip.** It was charged 1.5
   sides: the TP1 half paid its own entry and exit, the remainder paid only its exit. Every
   trade that hit TP1 recorded ~0.075pp (spot) better than it should. `backtest.py`'s
   mirror carried the same defect, which is why comparing them never revealed it.
3. **`trading/history.py` — veto reasons survive, and contributions are stored.**
   `reasons[:10]` dropped every `⛔` line because they are appended last; across run 1,
   201 cycles were vetoed and the reason survived for 3. A new `contributions` column holds
   the engine's per-condition `(buy, sell)` deltas as JSON.
4. `signals/engine.py` — `gates_disabled`, research only, default no-op.

---

## Check it in five minutes

```bash
bash scripts/morning_check.sh          # both bots, run 2's health, backups — one command
```

It exits non-zero if anything needs attention, so it can be cron'd. The contributions
check starts at the **run's** start from `paper_run_manifest.json`, not a fixed 24-hour
window — rows from the previous run are legitimately NULL and would otherwise read as a
fault every morning for a day after any restart.

By hand:

```bash
ssh dmonk@45.151.155.178
cd ~/playground/CrySignal-BTC
systemctl status spotsignal                 # active (running)?
systemctl --user status nakhoda-alloc       # the other bot — note --user
./venv/bin/python analyze.py                # the full report
```

**The silent-failure check — do not skip.** If Binance becomes unreachable the bot does
not error; funding, L/S, OI, basis and taker ratio all degrade to NEUTRAL and it keeps
running as a quieter, weaker system with nothing in the logs to say so.

```bash
sqlite3 data/signal_history.db \
  "SELECT COUNT(*) FROM cycle_log WHERE mode='futures' AND funding_rate=0;"
```

Anything above zero means those cycles are contaminated. It was **0** across all of run 1.

**Backups run daily at 03:00 UTC** via `deploy/backup_db.sh` (cron, no sudo). It uses
`sqlite3 .backup`, not `cp` — the bot writes hourly and `cp` of a live SQLite file can
capture a torn write that restores corrupt. The script runs `integrity_check` and fails
loudly. Keeps 30 days. Pull one to the Mac occasionally.

---

## Open items

1. ~~Verify run 2's first cycles actually carry the fixes~~ — **done 2026-10-08.**
   `contributions` is JSON on every run-2 cycle, and vetoed cycles carry `⛔`. Only the
   TP1 cost fix remains, and it waits on a trade (see the checkpoints at the top).
2. ~~`PermitRootLogin yes`~~ — **done 2026-10-04.** Set to `no` and sshd restarted.
   Access is unaffected: both operator and tooling log in as `dmonk`.
3. ~~The reboot test~~ — **done 2026-10-04, and it passed.** First real reboot since the
   host came online. Nothing was touched afterwards:

   | | |
   |---|---|
   | machine booted | 08:52:53 UTC |
   | `spotsignal` active | 08:53:00 — **7 s later** |
   | `nakhoda-alloc` active | 08:53:01 — **8 s later** |

   Both resumed working, not merely `active`: spotsignal re-entered loop mode with zero
   errors, allocbot reconnected to OKX and still held its 9 sleeves (equity 28,739 USDT
   virtual) — its state is the wallet, so it picked up exactly where it was.

   **On allocbot's demo clock:** the process restarted, so `ActiveEnterTimestamp` and
   `NRestarts` reset. That was a deliberate reboot with clean automatic recovery, not a
   fault, and no error was logged either side of it. Judge the two-week requirement on
   errors and missed daily decisions, not on one process's uptime.
4. **`nakhoda-alloc`'s demo clock — two weeks clean as of 2026-10-08.** Nakhoda's
   CLAUDE.md requires **two weeks clean** before real money is considered. Started
   2026-09-24 04:17 UTC; zero errors and no missed daily decision through 2026-10-08. The
   10-08 rebalance cut it from 10 sleeves to 5 (Donchian exits in a falling market, equity
   27,940 USDT virtual) — normal, not a fault. But read `Nakhoda/docs/OOS-FINDING-2026-09-08.md` first: the rule passed a
   locked crypto holdout and then **failed out of domain** — edge −2.2%/yr, breadth 2/22,
   and timing indistinguishable from matched random entry. Passing the demo gate proves the
   plumbing, not the edge.

---

## What to expect, and what the evidence says

The system fires roughly 12–15 signals a year in backtest. **Days of nothing but HOLD are
normal.** Run 1 produced 3 trades in 35 days and **zero futures positions from 29 fired
signals** — futures signals die in a dead zone: the engine fires at `strength ≥ 1.0 ×
threshold` but a position only opens at `≥ 1.2 ×`, and the mean ratio was 1.106.

Before proposing any improvement, read `docs/superpowers/specs/` — particularly
`2026-09-24-entry-results.md`. The assembled entry has been tested against a
count-matched random baseline: it **lost** on 2018–2025 tuning data and **beat** it on the
2025–2026 holdout, intervals disjoint both times. The sign of every headline flips with
the period. That is the same thing STEP 1 found, and it is the strongest result this
project has.

---

## Traps that cost a previous session real time

- **`pgrep -f <name>` matches the shell running it**, because that shell's own command line
  contains `<name>`. It produced a wait loop that never exited (two of them ran for ten
  days) and a false report that a live bot had died. Match on `/proc/<pid>/cmdline` or the
  venv's binary path instead.
- **`ps -eo command` truncates at terminal width.** A long venv path pushes the module name
  past the cut, so `ps | grep` misses a process that is plainly running. Use `ps -ww`.
- **Local `main` can be stale.** A whole analysis was done against `main` at `0f0c2c7`
  while the VPS ran `b913a9f`, producing a confident and completely wrong claim that a
  merge would retune the strategy. Check `git rev-parse HEAD` **on the VPS** first.
- **`git show <ref>:<file> > out` wrote an empty file here.** `git cat-file -p <ref>:<file> | cat > out`
  works. And in zsh, `$br:config.py` loses characters to a history modifier — brace it:
  `"${br}:config.py"`.
- **OKX is DNS-blocked on the owner's home network** (`www.okx.com` resolves to
  `internetpositif.id`). It resolves correctly from the VPS. A ccxt `NetworkError` at
  `load_markets()` on the laptop is that, not a code bug. Check `dig +short www.okx.com`.

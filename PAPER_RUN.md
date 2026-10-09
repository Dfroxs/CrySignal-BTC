# Paper Run — Operations Note

**Rewritten 2026-10-09, when run 3 started. Read this first when you come back.**

**Run 3 started 2026-10-09 05:41:31 UTC at `c0e8764`** (manifest on the host; run 2's
is archived as `paper_run_manifest.run2.json`, its final DB as
`data/backups/FINAL-run2-20261009-signal_history.db`, integrity ok, also on the Mac).
Ops-report fixes followed at `01d3e71` without a bot restart: the report runs from cron
as its own process, so pulling a commit that touches only `agents/ops_report.py` does
not change what the running bot executes. **Day 30 = 2026-11-08.**

**Restart 2026-10-09 06:15 UTC at `9b61770`** (notifier, shadow and docs only; no
signal, gate, config or trading change): compact Telegram messages; shadow agents judge
the managed trade per mode and run in a background thread. **H-S counts only shadow
opinions with `timestamp >= 2026-10-09T06:16:00Z`** (prereg amendment). None existed
before it.

**Restart 2026-10-09 07:15 UTC at `10b9628`** (agents only; trading path diff empty):
exit shadow (`shadow_exit_opinions`, scored by H-X), richer ops report with LLM cost,
**hourly alarm** (cron `10 * * * *`, `agents/alarm.py`, no LLM, state in
`data/alarm_state.json`), and the **owner Q&A bot**: user service `spotsignal-qa`
(`systemctl --user status spotsignal-qa`, `journalctl --user -u spotsignal-qa`), which
answers only the owner's chat, has a read-only DB, and allows 30 questions a day.
**H-X counts exit opinions with `timestamp >= 2026-10-09T07:16:00Z`.**

**Restart 2026-10-09 ~07:45 UTC** (notifier + backtest only; trading-path diff from
`c0e8764` empty, `backtest._costs` identical): the futures card now shows the levels a
position opens with, and `backtest.py` scores each candle on live's own windows (spot
VWAP was 96h). No hypothesis window changes. The owner has ruled that real findings
ship mid-run, recorded like this.

**Restart 2026-10-09 09:15 UTC at `9580b8a`.** The Q&A bot now answers with Claude
Haiku 4.5 (its own `QA_LLM_MODEL`; the shadow agents keep Opus 5.5 / DeepSeek). The
shadow context names levels `<level>_vs_price_pct`, after Claude misread `ema200_pct`.
Also loaded: the futures gate switch (`disabled_gates: []`, so behaviour is identical).
Shadow tables held 0 / 0 rows right before the restart.
**H-S and H-X count only opinions with `timestamp >= 2026-10-09T09:17:00Z`**, which
supersedes the 06:16 and 07:16 cut-offs (prereg amendment).

**Restart 2026-10-09 09:27 UTC at `1ca9aef`.** The 09:15 context fix would have crashed
the shadow agents on the first real signal (`numpy.bool_` is not JSON-serialisable), and
outage placeholders are now sent to the agents as null. No BUY/SELL fired between
09:16 and 09:27, and the shadow tables were 0 / 0, so nothing was lost. A VPS probe with
numpy values recorded all four opinions. **H-S and H-X count opinions with
`timestamp >= 2026-10-09T09:28:00Z`.**


**Restart 2026-10-09 10:55:39 UTC at `4dc837b` (PR #17). Run 3's scoring window restarts
here.** Futures now opens from WEAK (`min_confidence` NORMAL → WEAK, an owner design
decision for data yield; spot unchanged), and the backtest reads the same minimum. This
is a `config.py` change, so the 05:41 start is discarded per the prereg's clause. It
cost nothing: 0 signals, 0 positions and 0 shadow opinions existed before it. **All
run-3 hypotheses, H-S and H-X included, count from the regenerated manifest's
`started_at = 2026-10-09T11:12:48Z`** (old manifest archived as `paper_run_manifest.run3-0541.json`). Day 30 is
still 2026-11-08. See the prereg amendment "~11:00 UTC". The Q&A bot restarted at 10:55:19
on Sonnet 5.5 with project knowledge, chat memory and engine reasons. The trading-path
diff check is now measured from `4dc837b`.

**Restart 2026-10-09 12:34:11 UTC at `316e0dc` (PR #18). Run 3's window restarts again.**
Spot runs without `no_chase`/`anti_fomo`/`entry_wick` (`config.VETOES_DISABLED`, owner
design decision; 09-24 H-B FAILED to show they pay), and the R:R gate no longer vetoes
an exact 1.5 on float noise. Nothing existed since 11:12 (0 signals, positions or shadow
opinions). **All run-3 hypotheses count from the manifest's `started_at =
2026-10-09T12:34:20Z`** (previous one archived as `paper_run_manifest.run3-1112.json`).
Day 30 is still 2026-11-08. The diff check now runs from `316e0dc`.

**Restart 2026-10-09 17:14:32 UTC at `0c61921` (PR #19). Run 3 does NOT restart.**
Notifier only: a signal blocked in Phase 3 now shows as `⛔ … blocked by <gate>, no
position` instead of reading like a trade. The trading-path diff from `316e0dc` is empty and the
manifest is unchanged (`started_at = 2026-10-09T12:34:20Z`).
---

## New session? Do this first

```bash
bash scripts/morning_check.sh
```

Both bots, one command, exits non-zero if anything needs attention. From run 3 the VPS
also sends its own **daily Telegram report at 03:30 UTC** (`agents/ops_report.py`, user
crontab). Rules decide the anomalies; an LLM only writes the summary.

## Run 3 — what it is for, and the one date that matters

**Goal changed (owner, 2026-10-09):** collect data *and* improve toward profit. Zero
positions is the anomaly, not a safe state. See CLAUDE.md.

**Scoring date: no hypothesis may be scored before day 30** of run 3 —
`docs/superpowers/specs/2026-10-09-run3-prereg.md` (H-L live IC, H-V variants, H-B BTC
vs random, H-S shadow agents). Interim looks are health only. Instruments:
`scripts/live_ic.py`, `scripts/variant_books.py`, `scripts/shadow_eval.py`, and
`scripts/entry_ic.py` for H-B.

**What changed from run 2** (all on `main` at promotion; see CHANGELOG 2026-10-09):

| change | why | evidence |
|---|---|---|
| re-entry anchor ages out after 168h | a 09-12 WIN locked spot out for 3 weeks | design decision — its test was INCONCLUSIVE |
| futures trail 1.5 → 3.5×ATR, stop/target ×2 | shorts were not late; the trail killed them | **pre-registered PASS**, +0.243pp, still −0.05pp absolute |
| controller lowers a mode that never opened; state versioned | futures could never be lowered; run-2 fire timestamps would have read as opens | bug fix |
| `NameError` in the re-entry guard | would have killed Phase 3 on every worse-price signal | bug fix |
| live score variants (`cycle_log.variants`) | funding/L/S/basis scored 0 of 92 cycles — absolute bands never fire | logged only, scored by H-V |
| shadow agents (`shadow_opinions`) | Claude + DeepSeek judge each signal | logged only, scored by H-S |
| daily ops report | morning_check automated on the host | — |

**The VPS now holds LLM API keys** (`ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY` in `.env`,
mode 600). Still **no exchange keys**: spotsignal places no orders. If a key leaks,
revoke it in its console and replace it in `.env`.

**allocbot's demo gate: two weeks elapsed 2026-10-08 04:17 UTC, clean.** Read
`../Nakhoda/docs/OOS-FINDING-2026-09-08.md` before treating that as permission to trade
real money. The pass proves the plumbing, not the edge.

---

## Two bots are running, on one VPS

| | **spotsignal** | **nakhoda-alloc** |
|---|---|---|
| Repo | `~/playground/CrySignal-BTC` | `~/playground/Nakhoda` |
| Service | `spotsignal.service` (system) | `nakhoda-alloc.service` (**user scope**) |
| Runs as | `dmonk` | `dmonk` |
| Started | **run 3: 2026-10-09 (see manifest)** · run 2: 2026-10-04 07:37 | 2026-09-24 04:17 UTC |
| Touches an exchange? | **No.** No exchange keys, zero order calls (LLM keys only, for the agents). Paper positions live in SQLite. | **Yes** — real orders on OKX **Demo** (virtual money) |
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

- **The VPS venv was made with `uv` and has no `pip`.** `./venv/bin/pip` does not exist
  and `python -m pip` fails. Install with
  `~/.local/bin/uv pip install --python venv/bin/python -r requirements.txt`.
- **Restart without sudo by killing the service's MainPID**, not `pkill -f`:
  `kill -TERM $(systemctl show -p MainPID --value spotsignal)`. `Restart=always` brings it
  back on the code on disk within ~30 s. `pkill -f run_bot.py` can match the SSH shell
  running it.
- **A cron job reading user-scope journals needs `XDG_RUNTIME_DIR=/run/user/$(id -u)`.**
  The ops-report crontab line sets it.

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

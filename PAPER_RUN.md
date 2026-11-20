# Paper Run — Operations Note

**Rewritten 2026-10-09, when run 3 started. Read this first when you come back.**

**Run 5's start moved to 2026-11-20 17:05:36 UTC at `ac5ab68`** (PR #69, S/R condition
repaired, H-SR pre-registered).
- Bot restarted at 17:05:03, pid 422848. `spotsignal-dash` and `spotsignal-qa` restarted.
- **The 15:19 manifest** is archived as `data/paper_run_manifest.run5-1519.json`.
  - Nothing was lost: in between there were no positions, 3 SELLs blocked `already_open`
    and 1 shadow opinion.
  - The archive's name keeps it out of the dashboard's run history, as `run3-0541` did.
- **What changed:** condition 16 now finds its own levels (`tolerance=0.0`). It had fired
  in 0 of 1,297 cycles.
- **H-SR-1 FAILED** the same day (5 of 10 cells at horizon 6, needed 8). Per the prereg,
  `support_resistance` goes into `DISABLED_CONDITIONS` at the next restart. That is the
  shadow-context restart (owner: now). Ceilings become futures 24.25 and spot 21.75, and
  thresholds 4.76 and 4.16.
- **Windows restart at 17:05:03:** H-VG to 2027-02-18 17:05:03, and H-VR/H-BS to
  2027-01-19 17:05:03.
- **Scanner window not cut:** it begins 2026-11-21 00:00, after this.
- **Unchanged:** the dashboard account ($1,000) and #20 (carried, run 4's).
- **The 15:19 block below** still describes run 5's reset; its window lines are superseded.

**Run 5 started 2026-11-20 15:19:48 UTC at `d954cc1`** (PR #66, gold/VIX `range=5d`).
Owner's go: "reset balance dan 90 harinya".
- Bot restarted by `pkill -f '[r]un_bot.py'`; systemd respawned it, pid 421157, active
  15:19:44. `spotsignal-dash` and `spotsignal-qa` restarted.
- **Run 4 archived:** `data/paper_run_manifest.run4.json`, plus
  `data/backups/FINAL-run4-20261120-1519-signal_history.db` (integrity ok).
- **New manifest:** made with `start_paper_run.py --force`. Thresholds spot 4.3 / futures
  4.91. The dashboard account restarts at $1,000.
- **Position #20 was open at the reset** (futures SELL $62,778). The owner chose to reset
  now, not to wait or force-close it.
  - It belongs to run 4, since runs are keyed on `opened_at`, and closes on its own levels.
  - Until it closes, run 5's futures cannot open (one position at a time).
- **What changed (trading path):** gold and VIX were blank in 740 and 191 of 2,493 cycles
  (weekends and Yahoo's daily rollover), so `gold_vix` scored nothing there. Now fixed.
- **Windows restart at 15:19:44:**
  - H-VG to 2027-02-18 15:19:44;
  - H-VR/H-BS to 2027-01-19 15:19:44;
  - H-FLP-C and scoring day follow the manifest.
- **Scanner reset too (owner, later the same day: "reset saldo + jendela 90 hari"):**
  - Everything counts from the bar close 2026-11-21 00:00 UTC (`agents.scanner.WINDOW_FROM`):
    H-SC, H-SC2 and H-SCD, plus all six books (pure, hybrid and random, futures and spot),
    which start again at $1,000.
  - Scanner positions opened before it are **carried**: they run out within 24h and are in
    no balance. BTC #20 is carried the same way on the dashboard.
  - The gold/VIX fix never reaches the scanner (`market_structure=None`); the reset is the
    owner's decision.
  - Live only once the PR is pulled (scanner is cron, so no restart; dashboard restart). If
    pulled after 00:05, the first positions are sized on the old balance.
- **Unchanged:** H-DR. It scores the Binance derivs archive, not the bot or the scanner.

**Run 4 started 2026-11-17 18:44:50 UTC at `3377027`** (PR #46, run-4 part A). Owner's go.
Bot restarted by killing the MainPID; systemd respawned it, pid 383220, active 18:44:35.
- **No position was open at the restart.**
- **Run 3 archived:** `data/paper_run_manifest.run3.json`, plus
  `data/backups/FINAL-run3-20261117-1844-signal_history.db` (integrity ok).
- **New manifest** made with `start_paper_run.py --force`. It freezes `futures_fee_pct`
  0.05; thresholds are spot 4.3 / futures 4.91. 19 earlier positions stay in the DB.
- **What changed (trading path):**
  - HTF conflict penalty applied last;
  - session bump at the bar's close hour;
  - a macro-forced HOLD clears its levels;
  - futures fee 0.05%;
  - stop checked before flip;
  - flip shadow (H-FLP-C, `shadow_flip_opinions`).
- **H-VG** window: 2026-11-17 18:44:50 to 2027-02-15 18:44:50 UTC (PR #47, `030af16`). H-SC,
  H-SC2 and H-DR are unaffected (separate processes and data).
- **Part B** of the run-4 plan (veto gate, L/S, `rel_ic_dir`, basis) waits for its own
  results.
- **2026-11-20 03:22:51 UTC: bot restarted at `f7310d5`** (PRs #63 + #64), pid 413608.
  - **Open position:** #20 (futures SELL at $62,778, trail $63,966.66). It has no
    `last_checked_at`, so its 1m walk starts at the first check after the restart,
    never backwards.
  - **Changes, both design decisions:**
    - stops and targets rest like exchange orders (`trading/paper._apply_resting_orders`),
      walking the closed 1m bars since the last check;
    - the scanner book enters at the answer's perp price, from the next scanner run.
  - **Also deployed:** the H-SCD prereg and instrument (scoring-day H-SCD-1/-2, dry run
    "not yet"), the Mac pull `RunAtLoad`, and `spotsignal-dash` and `spotsignal-qa`
    restarted.
  - **Window change:** H-VG runs 2026-11-20 03:22:51 to 2027-02-18 03:22:51 (prereg
    amendment, exits changed).
  - **Unchanged:** H-VR/H-BS (signals unchanged), H-FLP-C (scored by replay), H-SC, H-SC2,
    H-DR and the manifest.
  - **PR #65 (H-VG amendment)** pulled 2026-11-20 03:29 at `67e7a63`; dashboard restart only.
    Its trading-path diff is empty, and the bot stayed at pid 413608.
- **2026-11-19 16:34:53 UTC: bot restarted at `8ca245a`** (PR #61), pid 406603. No position
  was open. The re-entry gate now ignores anchors opened before the manifest's `started_at`.
  Run 3's SHORT WIN at $68,900 had blocked 26 futures SHORTs. Spot is covered too; that has
  no effect today. Design decision for data yield.
  - **Window change:** H-VG runs 2026-11-19 16:34:53 to 2027-02-17 16:34:53 (prereg
    amendment 2).
  - **Unchanged:** H-VR/H-BS (signals unchanged), the manifest, H-SC, H-SC2, H-DR and
    H-FLP-C.
  - **Scanner books:** levels on the 24h scale (ATR(1h) × √24) from the next scanner run.
    No restart; the H-SC windows are unchanged.
  - **PR #62 (H-VG amendment)** pulled 2026-11-19 at `0f4eca9`; dashboard restart only. Its
    trading-path diff is empty, and the bot stayed at pid 406603.
- **2026-11-19 01:13:53 UTC: bot restarted at `9a3d271`** (PRs #57 + #58), pid 398486. No
  position was open. The −0.5 BUY penalty at F&G ≥ 70 is gone (H-FG2 FAIL, owner: now). The
  fear leg stays.
  - **Signal change:** H-VG now runs to 2027-02-17 01:13:53, and H-VR/H-BS to 2027-01-18
    01:13:53 (prereg amendments).
  - **Unchanged:** the manifest (run 4's `started_at`), and the H-SC, H-SC2, H-DR and
    H-FLP-C windows.
- **2026-11-18:** pulled `9c6ed5d` (PR #48, H-VR / H-BS prereg and its instrument) and
  restarted `spotsignal-dash` only. Those tests are scored on or after 2027-01-16 18:44:50
  UTC.
- **2026-11-17 20:56 UTC:** pulled `b82fc22` (PRs #49 + #50). Bot restarted by MainPID, new
  pid 385048, active 20:56:22. No position was open.
  - **#50:** a live TP2 exit pays 2 cost sides and records its exit price. Booking only, so
    no signal changes and no test window is cut.
  - **#49:** scoring day. Cron `20 4 * * *` runs `agents.scoring_day`, logging to
    `data/scoring/scoring_day.log`. Dry run: every test "not yet".
- **2026-11-17 21:48 UTC:** pulled `dfba14f` (PR #51, every Telegram message reformatted
  and the hourly scanner digest added). Bot restarted, pid 386025. No position was open,
  and no signal changes.
- **2026-11-18:** pulled `375cbf7` (PR #52, only real failures count as log errors; the
  report's "1 baris error" was a recovered news-feed WARNING). Dashboard restart only.
- **2026-11-18 14:06 UTC:** pulled `66bb998` (PR #53). The Telegram cards gain sizing
  (leverage, margin, liquidation, loss at the stop) and the top 3 reasons. The dashboard gains
  the market strip, position detail, account statistics and run history. Bot restarted, pid
  392860; dashboard restarted. No position was open, and no signal changes.

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

**2026-10-10 02:18:54 UTC: pulled `a67c93a` (PR #22), restarted `spotsignal-qa` only.
`spotsignal` was NOT restarted.** The trading-path diff from `28dabb5` is empty (only
agents, scripts, docs, tests). The Q&A knowledge now explains `stale_cache` (the hourly
replay of a 4H analysis) and the 4H ADX/DI regime gate. The backup anomaly no longer
fires between 00:00 and 03:15 UTC. Run 3 does not restart.

**2026-10-10 17:32 UTC: pulled `717a472` (PR #24), restarted `spotsignal-qa` only.**
Trading-path diff from `9721529` empty. The Q&A facts now list each gate block
(`recent_blocks`). `spotsignal` untouched, run 3 does not restart.

**Restart 2026-10-10 17:52:56 UTC at `85f190b` (PRs #25, #26). Run 3 does NOT restart.**
Notifier and agents only: HTML-safe truncation, Q&A answers escaped before fitting, a
card error no longer swallows the open/close messages, WEAK cards name the confidence
bar, close cards coloured by P&L, TP1-only headed "Partial take-profit". The trading-path
diff from `316e0dc` (`signals/ config.py trading/paper.py backtest.py`) is empty;
`run_bot.py` differs only by the 17:14 notifier annotation. `spotsignal` came back at
17:53:26 (pid 69162) with position #4 intact; `spotsignal-qa` at 17:53:37.
**Trap:** `pkill -f run_bot.py` sent over ssh also matches the ssh shell's own command
line and kills the session (exit 255) — the bot still restarts. Use
`pkill -f '[r]un_bot.py'`.

**Restart 2026-10-14 17:34:58 UTC at `c717b21` (PRs #28–#32). Futures hypotheses
restart; spot and H-L do NOT.** The first trading-path change since `316e0dc`, shipped
mid-run by owner decision (amendments 2026-10-15 in `2026-10-09-run3-prereg.md`):
- **futures:** basis and taker pruned (`DISABLED_CONDITIONS`), so `SIGNAL_MAX_SCORE`
  26.50 → 25.00, threshold 5.20 → 4.91 and band 3.77–7.55;
- FLIP/BREAKER_CLOSE P&L priced with costs, and both closes now reach the Telegram card;
- shadow agents run on Claude only (`SHADOW_PROVIDERS` default `anthropic`);
- every Telegram card rewritten in Indonesian.

Spot's 22.50 ceiling and 4.30 threshold are unchanged (verified on the VPS before
restart). Diff `9d0e6c4..c717b21`: `config.py`, `signals/engine.py`, `run_bot.py`,
`notifier/`, `agents/`, `scripts/score_run3.py`; `trading/` and `backtest.py` untouched.
No position was open at restart. The manifest gained `futures_restart_at =
2026-10-14T17:34:58+00:00` (pre-edit copy: `paper_run_manifest.pre-futures-restart.json`).
`score_run3.py` scores H-V1, H-V2 futures, H-S and H-X from it, so their day 30 is
**2026-11-13**. H-L, H-V2 spot, H-B, H-D and H-RGL keep 2026-11-08 / 11-11.
`spotsignal-qa` was restarted with it. The restart's diff check from here on runs from `c717b21`.
First cycle after it (18:01 UTC): spot HOLD at 4.30; futures HOLD at **4.66** (4.91 base −
0.25, the controller lowering a mode with no open in 72h); basis and taker contributions
`[0, 0]` on both; Telegram card sent; no error in `paper_run.log`. No shadow row yet
(nothing fired, nothing open), so Claude-only is unverified until the first signal.

**Restart 2026-10-17 14:39:46 UTC at `d1d490b` (PRs #36, #37). Futures hypotheses
restart again; spot and H-L do NOT.** Futures now runs without `no_chase`, `anti_fomo`
and `entry_wick` (`VETOES_DISABLED["futures"]`, the spot set), an owner design decision
for data yield, not a test result. Since 10-14 17:34 futures had fired 0 signals: of 10
over-threshold cycles, 4 BUYs died on no_chase alone. `counter_trend` and `short_term`
stay. Futures has no Phase 3 `breakout_chase`, so it now has no chase limit.
Trading-path diff `c717b21..d1d490b` is `config.py` only; ceilings and thresholds
unchanged (25.00 / 4.91, spot 22.50 / 4.30). No position open at restart. The manifest's
`futures_restart_at` is now `2026-10-17T14:39:46+00:00` (pre-edit copy:
`paper_run_manifest.pre-futures-restart-2.json`), so H-V1, H-V2 futures, H-S and H-X
score from it, **day 30 = 2026-11-16** (prereg amendment 2026-10-17). H-L, H-V2 spot,
H-B, H-D and H-RGL keep 2026-11-08 / 11-11. Restarted with `pkill -f '[r]un_bot.py'`
(systemd respawn, pid 133552); `spotsignal-qa` restarted 14:40:08. The diff check now
runs from `d1d490b`. First cycle after it (15:01 UTC): spot HOLD 3.0/3.80, futures HOLD
3.0/4.91, funding non-zero, Telegram sent, no error in `paper_run.log`. The change is
unverified until the first futures BUY above VWAP+0.5×ATR fires.

**2026-11-11: `nakhoda-alloc` removed from the VPS.** Service stopped, disabled and its user
unit deleted; `~/playground/Nakhoda` deleted. The code is intact on the Mac
(`~/Playground/Python/Nakhoda`). Its OKX **demo** wallet was not touched (equity about
25,838 USDT virtual, flat), and the demo keys that were in the VPS `.env` are gone with it:
revoke them in OKX if they are no longer wanted. `spotsignal` and `spotsignal-qa` were not
touched and were `active` after. `scripts/morning_check.sh` no longer checks allocbot.
Where this file still says "two bots" or describes allocbot, it is history. Free memory
went from ~70 to ~195 MB.

**2026-11-16 19:04 UTC: pulled `e7b6755` (PR #39), no bot restart, no window restarts.**
Two new standalone processes; the trading-path diff `d1d490b..e7b6755` is empty (new
files only: `agents/scanner.py`, `agents/dashboard.py`, `scripts/scanner_eval.py`, the
H-SC prereg, `deploy/spotsignal-dash.service`, tests). (1) **Dashboard**: user unit
`spotsignal-dash`, `127.0.0.1:8080`, read-only DB; open with
`ssh -N -L 8080:localhost:8080 dmonk@45.151.155.178`, then `http://localhost:8080`.
(2) **Claude scanner (H-SC)**: cron `5 * * * *` → `data/scanner.db`, log `data/scanner.log`,
cost in `data/llm_usage.jsonl` (about $0.022 a call, ~$17/month). First cycle 19:04 UTC (bar
19:00, 9 coins, no error). H-SC's scored window starts at that first error-free opinion;
verdict needs 90 days and 300 picks. `spotsignal` and `spotsignal-qa` stayed `active`. The
flip shadow (`develop`, touches `run_bot.py`) is **not** deployed.
**Same evening: pulled `5ed6fc3` (PR #40), restarted `spotsignal-dash` only.** Scanner demo
books (futures LONG/SHORT and an independent spot LONG-only book, 1,000 USD each, table
`scan_positions` in `data/scanner.db`, created by the next scanner run) and the plain-language
dashboard (cards for BTC bot, BTC + Claude filter, scanner futures, scanner spot; tabs).
Trading-path diff empty; `spotsignal` not restarted. PR #41 (hybrid arm H-SC2) is open.
**2026-11-17 04:33 UTC: pulled `7acd78d` (PR #41), restarted `spotsignal-dash` only.** Hybrid
scanner arm H-SC2 live: same cron, a second Claude call per hour with the engine's score per
coin, `data/scanner_hybrid.db` with its own futures and spot books; first cycle bar 04:00, no
error, $0.025. Dashboard now the dark trading-app design. `spotsignal` not restarted. H-SC2
pairs from the hybrid arm's first cycle (2026-11-17 04:00 bar).
**2026-11-17 16:45 UTC: pulled `7d0cf02` (PR #42), restarted `spotsignal-dash` only.**
- **Backups:** `backup_db.sh` now also copies `scanner-`, `scanner_hybrid-` and
  `scanner_random-YYYYMMDD.db`. It was run once by hand, so today's copies exist.
- **Alarm and daily report:** the nakhoda-alloc check is gone, and its stale key was removed
  from `data/alarm_state.json` by hand, so no "pulih" message was sent. New checks cover the
  scanner arms, backup age and archive age. The daily report has a Scanner section.
- **Archive:** gains ADA/LINK/AVAX. Run once by hand: 696 backfilled rows per stat.
- **Random arm** `data/scanner_random.db`: first cycle on bar 16:00, run by hand. It dealt
  pure's 1 LONG + 2 SHORT to ADA, BTC and BNB.
- **H-DR** is pre-registered; it is scored on or after 2027-02-06.

`spotsignal` was not restarted.
**2026-11-17 18:17 UTC: pulled `3a90822` (PRs #43 + #44), restarted `spotsignal-dash` only.**
- **Scanner prompt v2** (table input, picks-only reply, about 55% cheaper; same Opus, same
  hourly cadence).
  - The first v2 cycle is the 19:00 bar (cron 19:05).
  - H-SC and H-SC2 count only v2 cycles (prereg addendum). The 18:00 bar and earlier are v1
    and are never scored.
- **Dashboard revamp:**
  - scoreboard versus random;
  - test progress meters;
  - positions grouped by account;
  - picks matrix.

  The 8081 preview worktree was removed.

`spotsignal` was not restarted.
**2026-11-17 18:27 UTC: pulled `31becb8` (PR #45, dashboard light/dark toggle), restarted
`spotsignal-dash` only.**
---

## New session? Do this first

```bash
bash scripts/morning_check.sh
```

Both bots, one command, exits non-zero if anything needs attention. From run 3 the VPS
also sends its own **daily Telegram report at 03:30 UTC** (`agents/ops_report.py`, user
crontab). Rules decide the anomalies; an LLM only writes the summary.

**Derivatives archive (from 2026-10-10):** `scripts/archive_binance_derivs.py` runs daily
at 03:40 UTC from the user crontab and appends Binance's 30-day L/S, OI and taker stats
to `data/derivs/` (log: `data/derivs/archive.log`). That data exists nowhere else once it
is older than 30 days, so **a gap of more than ~25 days is permanent loss**. It is scored
by H-D (`2026-10-10-derivs-archive-prereg.md`) on 2026-11-08, and no IC may be computed
from it before then.

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

**allocbot's demo gate (history; removed from the VPS 2026-11-11): two weeks elapsed
2026-10-08 04:17 UTC, clean.** Read `../Nakhoda/docs/OOS-FINDING-2026-09-08.md` before
treating that as permission to trade real money. The pass proves the plumbing, not the
edge.

---

## One bot runs on the VPS (nakhoda-alloc was removed 2026-11-11)

| | **spotsignal** |
|---|---|
| Repo | `~/playground/CrySignal-BTC` |
| Service | `spotsignal.service` (system), plus `spotsignal-qa` (user scope) |
| Runs as | `dmonk` |
| Started | **run 3: 2026-10-09 (see manifest)** · run 2: 2026-10-04 07:37 |
| Touches an exchange? | **No.** No exchange keys, zero order calls (LLM keys only, for the agents). Paper positions live in SQLite. |
| Strategy | 22 conditions, 5 veto gates, adaptive threshold, BTC only |

Host `45.151.155.178` — Kamatera, Singapore, Ubuntu 24.04, **960 MB RAM, 1 core**.
`ssh dmonk@45.151.155.178`. Roughly 195 MB free (page cache included in `available`: ~525 MB) after Nakhoda's removal.

**There is no passwordless sudo**, and `sudo` needs a real TTY — neither Claude Code's
Bash tool nor its `!` prefix provides one, so anything needing root has to be pasted into
a terminal application. Two consequences that cost a previous session time:
- `spotsignal-qa` is a **user-scope** unit (`systemctl --user ...`, `journalctl --user -u ...`)
  with `loginctl enable-linger` set — that needs no root and still survives reboot. The
  same was true of `nakhoda-alloc` while it ran.
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
bash scripts/morning_check.sh          # the bot's health and backups — one command
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
systemctl --user status spotsignal-qa       # the Q&A bot — note --user
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

**Watching the watchers (2026-11-19).**
- **The Mac's pull is also a watchdog.** It flags a VPS that does not answer, a bot with no
  cycle in 3 h, or an alarm with no heartbeat (`data/alarm.ok`) in 3 h, through a macOS
  notification and Telegram sent from the Mac.
- **Deployed 2026-11-19 03:52 UTC** (`ece8545`, PR #60), dashboard restart only.
  `data/ops_report.ok` was seeded with today's report time, 03:30:08 UTC: that report ran on
  the old code (its summary call is in `llm_usage.jsonl`) and wrote no heartbeat.
- **The VPS alarm flags:**
  - the daily report not sent in 26 h (`data/ops_report.ok`);
  - scoring day not run in 26 h;
  - `macro_events.csv` older than 6 h (the macro rule is blind);
  - exit/flip opinion providers whose last 3 all failed.

**Off-site copy on the Mac (2026-11-19).** `scripts/pull_backups.sh` runs from launchd
(`com.spotsignal.pullbackups`, 11:00 and 20:00 Mac time; a missed run happens at wake).
- It pulls `data/backups/`, `data/derivs/`, `data/scoring/` and the run manifests into
  `~/Backups/SpotSignal/`, without `--delete`. Dated copies are kept 90 days, plus the 1st of
  every month for good.
- It checks the newest copy of every database with `integrity_check`, then stamps
  `data/backups/.offsite_ok` on the VPS.
- The hourly alarm and the dashboard's Sistem tab flag a stamp older than 72 h, or none.
- Reinstall with `bash scripts/install_pull_backups.sh`; run it now with
  `launchctl kickstart gui/$(id -u)/com.spotsignal.pullbackups`.
- First pull 2026-11-18 22:24 UTC: 165 MB, all four databases ok.
- **2026-11-19:** `data/news/` (the headline and macro-calendar archive) and
  `llm_usage.jsonl` (the Claude cost ledger) are added to the pull. After the owner's "nothing missed again" audit, it also takes `data/*.json` (bot state and
  caches), every log (`paper_run.log`, `spotsignal.log`, `data/*.log`) and
  `signal_history.csv`. Only `.env` stays behind: it holds secrets, and the owner keeps it.

**News archive (2026-11-19).** `news_scraper.py` overwrites `crypto_news_sentiment.csv`
(about 50 headlines) and `macro_events.csv` every cycle; `cycle_log` keeps only the label and
Fear & Greed. `scripts/archive_news.py` runs from cron at `3 * * * *`, after the bot's :01. It
appends new rows to `data/news/news-YYYY-MM.csv` (unique on link + title) and
`macro-YYYY-MM.csv` (unique on the whole row), each stamped `archived_at`. The alarm and the
Sistem tab flag an archive that has not grown in 26 h. Headlines from before 2026-11-19 are
lost.

**Backups run daily at 03:00 UTC** via `deploy/backup_db.sh` (cron, no sudo). It uses
`sqlite3 .backup`, not `cp` — the bot writes hourly and `cp` of a live SQLite file can
capture a torn write that restores corrupt. The script runs `integrity_check` and fails
loudly. Keeps 30 days. Pull one to the Mac occasionally.

---

## Run 4 plan: one restart, everything bundled (written 2026-11-18)

**Amended the same day (owner): split in two.** Waiting to 2027-02-15 would have cost three
months of data from a more correct bot, to protect an H-VG window that was one day old. So
**part A**, the six decided items below, ships now in one restart. H-VG restarts with it (prereg
amendment). **Part B**, the candidates further down, still waits for its own results. The
scoring calendar below is updated for H-VG at the restart.

Every change to signals, gates, exits, costs or booking needs a bot restart, and each such
restart cuts the windows of the hypotheses that read the bot's stream. So they ship
**together, once**, not one by one. **Planned restart: on or after 2027-02-15**, when the
last result that feeds run 4's design (H-VG) is in.

**Scoring calendar.** These must not be read early. Each instrument refuses to score before
its date.

| date (UTC) | test | instrument | feeds run 4? |
|---|---|---|---|
| ~2027-01-16 (60 paired days) | H-SC2 hybrid vs pure scanner | `scanner_eval.py --hybrid-db` | no (scanner only) |
| 2027-02-06 | H-DR global L/S momentum, 8 alts + BTC | `hdr_eval.py` | yes: an entry condition, if both PASS |
| ~2027-02-14 (90 days + 300 picks) | H-SC Claude scanner vs universe | `scanner_eval.py` | no (scanner only) |
| 2027-02-15 | H-VG Claude veto gate, by episode | `veto_gate_eval.py` | yes: the gate, if PASS and the owner decides |

**Already decided. These ship in run 4 whatever the results say:**

1. **Futures fee 0.04% → 0.05%** (`config.py` `futures_fee_pct`). The Binance USD-M VIP0
   taker fee is 0.05% (0.045% with the BNB discount). The live book understates each futures
   round trip by about 0.02pp. The scanner books already use 0.05%. It changes every
   booked futures P&L, so it is a trading-path change.
2. **Stops checked before a flip** (`0cfcd0c`, develop). A position already through its stop
   is booked LOSS, not FLIP. The fill price is unchanged, but it **can move signals**:
   `market_data._get_recent_win_rate` counts only WIN/LOSS outcomes (FLIP is excluded), and
   that rate drives the adaptive threshold.
3. **Flip shadow** (`7dae3db`, develop: `run_bot.py` hook plus `shadow_flip_opinions`). This is
   logging only, for H-FLP-C (20 events). It is the only item that could go earlier: it
   changes no signal, so a plain restart for it alone would not cut H-VG's window. Earlier
   deployment is the owner's call.

4. **HTF conflict penalty hits the leader at the wrong moment** (engine review 2026-11-18,
   `signals/engine.py:301`). The −1.0 for a 4H-vs-1D conflict goes to whichever side leads
   after conditions 1–6, about 6 of 22. When the other side wins at the end, the final signal
   is never penalised. Fix: apply it after all conditions, to the side that would fire.
5. **Session bump reads the bar's OPEN hour** (`signals/engine.py:575`). `df.index` is the
   bar's open time (ccxt), so futures is shifted 1 h and spot up to 4 h. For example, the spot
   decision at 08:01 is treated as the Asia session (+0.5). Fix: use the close hour (open +
   timeframe), the moment the decision is made. Backtest and live agree today, so this is a
   labelling fix, not a mismatch.
6. **A macro-forced HOLD keeps its levels** (`integrate_news_with_signal`,
   `signals/engine.py:1021`). `stop_loss`, `take_profit` and `confidence` stay set on a HOLD,
   so `cycle_log` can record a HOLD with confidence NORMAL. This is cosmetic: no trade opens.
   Fix: clear them as every other gate does.

**Review of `trading/paper.py` + `run_bot.py` gates (2026-11-18).** One booking bug was fixed
at once, since it changes no signal: the TP2 remainder paid one cost side, so every live TP2
win was about 0.05pp (futures) / 0.075pp (spot) too good, and the TP2 close had no exit price.
These fidelity notes go to part B, each a design decision with its own prereg:
- **Exits are checked on the hourly close only.** The backtest triggers stops and targets
  intrabar from the bar's high and low, as a real exchange stop would, so live and replay
  differ on any bar that wicks through a level and closes back. Candidate: check the closed
  bar's high/low live too.
- **Spot positions are checked against the perp price** (`run_bot.py` `current_price`),
  not spot. The gap is the basis, usually under 0.1%.
- **Re-entry gate observation, not a bug:** SELL #19 (WIN at $68,900, closed 2026-11-15)
  blocks every lower futures SELL until its 168 h cap, 2026-11-22. 9 blocks since 11-16. This
  is the tested design (`2026-10-09-reentry-age-results.md`).

Each of 4–6 gets a failing test first, then the fix, and each changes signals. Trivial items
from the same review, not worth their own entries: OBV slope spans 4 bars over a 5-bar volume
denominator, and ADX is computed twice per cycle.

`backtest.py` research knobs on develop (`be_lock_r`, `flip_at`) are off by default and do not
change live behaviour.

**Candidates. Each needs its own result or prereg before it ships:**

- **Claude veto gate:** only if H-VG PASSes and the owner overrides the standing rule that an
  LLM never gates a live trade (run-3 plan). It fails open on errors.
- **L/S momentum entry condition:** only if H-DR1 and H-DR2 both PASS, and then after its own
  condition-vs-random test.
- **`rel_ic_dir` bias rule:** only if **H-VR** PASSes. It must beat base *and* predict on its
  own; H-V1's own IC was ~0.
- **Basis direction:** the condition has been **disabled since 2026-10-15**, so the review's
  "read backwards" was stale. **H-BS** decides the direction: BULLISH re-enables the original
  reading, BEARISH re-enables it flipped, INCONCLUSIVE keeps it off. H-F (history, bearish)
  and H-L1 (run 3 live, bullish) conflict.
- H-VR and H-BS: `2026-11-18-run4b-prereg.md`, `scripts/run4b_eval.py`, scored on or after
  **2027-01-16 18:44:50 UTC**, on run 4's first 60 days.
- **Fear & Greed overlay** (H-FG, `2026-11-19-macro-fng-results.md`):
  - **Licensed:** remove the −0.5 BUY penalty at F&G ≥ 70. H-FG2 FAILed: greed comes before
    +0.69 pp better 72 h returns, CI above 0.
  - **Candidate, owner decision:** the fear leg (+1.5 BUY at ≤ 20, −0.5 SELL at ≤ 30). H-FG1
    was INCONCLUSIVE, so the leg is unsupported.
  - It changes signals, so it ships with a restart.
- **Macro-event rule** (H-MC): INCONCLUSIVE, +0.22 pp, CI [−0.07, +0.50]. It stays. It acts
  on 51% of futures signals.

**On restart day:** archive the run-3 manifest and DB the way run 2's were. Then:
- score H-VG on its window, up to the restart;
- regenerate the manifest with `scripts/start_paper_run.py`;
- record the trading-path diff here.

The scanner arms are separate processes and keep running through it.

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
4. ~~`nakhoda-alloc`'s demo clock~~ — **moot: removed from the VPS 2026-11-11.** History:
   two weeks clean as of 2026-10-08. Nakhoda's
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

# Pre-registration: does closing-and-flipping help futures? (H-FLP)

**Written 2026-11-16, before the instrument, the simulator knob or any figure below
exists.** Fourth in the futures series after H-TPF, H-TRL and H-PLK (all FAIL), and the
first about the one live exit nobody has tested: the close-and-flip. Companion Claude arm
at the end.

## Why, and how much it is worth

Run 3's futures book (`2026-11-16-run3-futures-results.md`), 10 closed positions. Three
closed as `FLIP`: #14 -2.56%, #15 -1.61%, #16 -1.52%, together -5.69pp, against +4.54pp for
all ten. Each was an opposite signal arriving while a position was open: the bot closes at
market, then (if the flip gates allow) opens the other way. #15 and #16 were two such flips
in a row inside one chop (11-08 to 11-10).

Read it at its true weight:

- **#14 is not a flip decision.** Its stop (75,281) had already been passed by price
  (74,136) when the flip fired; fix A (2026-11-08) now books it as `LOSS` first. Holding
  would not have saved it. Only **#15 and #16 (-3.13pp)** are genuine flip decisions, and
  nothing says holding them would have gone better.
- n = 2 genuine cases, one regime. A reason to look, not a result.

Live logic, for reference (`run_bot.py`): an opposite signal that clears the signal
threshold closes the open position **first** and unconditionally; the flip gates
(`flip_unprofitable`, confidence, fakeout, psychology, S/R, counter-trend regime, trend
confluence) then decide only whether the new position opens. So a blocked flip still
leaves the bot flat. There is no score margin and no minimum age.

## The prior, stated

An opposite signal is information, and closing on it is a risk exit as much as an entry.
Removing it also removes a protective close, and the three exit tests before this one all
preferred the *less* intervening setting. I expect FAIL or INCONCLUSIVE. It is registered
because the flip is untested, it is the only exit left that a measured loss points at,
and the candidate has **no parameter to tune**.

## The one candidate (fixed now)

**Flip off.** While a futures position is open, an opposite signal is skipped exactly as a
same-direction signal already is ("already open"); the position runs to its own exits
(stop, trail, TP1/TP2, 72h cap, volatility exit, funding exit). Everything else as live.

**One candidate only.** No score margin, no minimum age, no confidence requirement, no
"flip only if the new signal can open" variant, and no cooldown change may be run or
reported on these windows. A second, parameterised flip rule would need its own prereg
and its own data.

## What has to be built, and how it stays honest

The replay has no flip (`simulate_sequence` skips every signal while a position is open),
which is why H-V2's fidelity needed an amendment. The baseline must be the *live* rule, so
the simulator learns it, default-off, through the real exit simulator:

- **`exit_params["flip_at"]`** in `backtest._simulate_forward`, absent = off. When set to
  a bar index `f`, the position is closed at bar `f`'s **close**, outcome `FLIP`, priced
  by the same `_net_pnl` as every other exit, **after** that bar's stop, target and
  trailing checks (live checks stops first, fix A). A position that exits earlier than `f`
  is untouched.
- **Identity test before any scored run:** with the knob absent every output is
  byte-identical to the current simulator (the H-PLK identity script, 18,900 entries, BUY
  and SELL, reused). A test also asserts that `flip_at` strictly after the natural exit
  changes nothing.
- A new script, `scripts/flip_ic.py`, owns the sequencing. It does **not** edit
  `scripts/reentry_ic.simulate_sequence`. It scores each bar once per window with the
  real engine (`backtest._score_candle`) and replays the same signal stream under two
  rules: **baseline = live** (opposite signal closes at the bar close, then the new
  position opens only if `backtest._failing_gates` and the live `flip_unprofitable` test
  pass; no same-side cooldown after a FLIP, the existing cooldown after every other
  exit) and **candidate = flip off**.
- `backtest.py` is a trading-path file for the PAPER_RUN diff check, and this edit will
  show in it. It is default-off research plumbing. `trading/paper.py` and `run_bot.py`
  are **not** touched by the test.

## Contamination, disclosed

1. The 2026-11-08 to 11-10 live flips suggested the idea; run-3 positions are never an
   arm. The Claude exit opinions around them (below) were read before this was written.
2. Arm C primary (BTC 2022-01 to 2025-10) is the window that tuned the live geometry:
   a guard, not evidence. The flip itself has never been tested on any window.
3. The engine's conditions were examined across 8 assets over 7 years (STEP 1); no flip
   figure exists for any of them.
4. Arm C confirmation ends 2026-10-09, before run 3 began.

## Arms

All on `_simulate_forward`, futures mode, 1h, max hold 72 bars, live costs and geometry
(`_exit_signal`), the same signal stream replayed under both rules.

- **C (BTC):** primary 2022-01-01 to 2025-10-01 (contaminated), confirmation 2025-10-01
  to 2026-10-09.
- **D (fresh to flip, engine on four other assets):** ETH, BNB, XRP, LINK, 2022-01-01 to
  2026-10-09. The engine does not make money on alts (backlog, "not to re-propose"); the
  question here is path-dependence of one exit rule, not alpha.

A cell that fails to fetch is retried until it yields a row, never dropped. Positions
still open at a window's end are excluded from both sequences and counted.

## Criteria

The two sequences open different trades, so trade-level pairing is not possible. The unit
is the **calendar month**: for each rule, the sum of closed-trade P&L (pp) in that month;
`diff = candidate - baseline`; months where both are flat count as 0.

**Arm D:** pooled mean monthly `diff` > 0 with the **90% CI lower bound > 0** (resample
months jointly across the four assets, 5,000 draws), **and** the candidate ahead on the
window total in **at least 3 of 4** assets.

**Arm C:** total `diff` > 0 in **both** windows. It can veto, never carry a PASS.

**Verdict:** PASS if D and C hold. FAIL if D's pooled mean monthly `diff` <= 0.
INCONCLUSIVE otherwise.

Reported, never criteria: trades and win rate per rule; number of flips and their mean
P&L; the share of flips whose new position then opened; the realised P&L of flipped
positions against what the same positions earned when run to their own exit; BTC-only;
unresolved counts.

## Discard

- **Fidelity (new for this instrument):** the flip-aware **baseline** replay of the run-3
  futures window (`futures_restart_at` to the snapshot) must match at least **7 of the 10**
  live positions within +/-1 bar, and every unmatched one must be annotated individually
  as macro-close or breaker. The `flip artefact` category of the 2026-11-10 amendment is
  no longer allowed: the flip is modelled now. Fewer than 7, or an unmatched position that
  fits neither category, discards the run. The check is run **once**, before the scored
  arms, and the instrument is not edited after it.
- **Identity:** `flip_at` absent reproduces the pre-edit simulator exactly. Any
  difference discards the run.
- **Geometry once:** baseline stop distance equals `stop_distance_mult` x the engine's
  stop on a probe signal.
- **Inert:** if fewer than 30 flips occur in the baseline of Arm D pooled, Arm D is
  reported as underpowered and the verdict is INCONCLUSIVE.

## What each verdict licenses

- **PASS:** "no flip on futures" becomes a **tested change** in `run4-backlog.md`,
  implemented in `run_bot.py`'s futures block with a default-off switch. The owner may
  ship it mid-run, restarting every futures-scored hypothesis from the deploy. Not
  applied to spot.
- **FAIL or INCONCLUSIVE:** nothing changes. No other flip rule may be tried on these
  windows. A FAIL means the live close-and-flip stays, and it closes the exit side
  of the series with H-TPF, H-TRL and H-PLK.
- Whatever the verdict, no profit claim: absolute P&L of both rules is reported beside it.

## Claude arm (forward only; never a gate)

**Why it cannot be backtested.** Claude would be judging 2022-2025 candles that sit inside
its training data. The only fair test uses events that post-date it, i.e. live ones, and
live flips are rare (3 in 30 days, 2 of them genuine). So this arm is logging-only and
slow.

**Descriptive, now, no criterion (existing H-X data; written before it is tabulated
beyond this one look).** Claude's exit opinion in the last cycle before each live flip:

| position | cycle | P&L if closed then | Claude | final (flip) |
|---|---|---|---|---|
| #14 BUY | 11-08 21:01 | -0.44% | CLOSE (70) | -2.56% |
| #15 SELL | 11-09 15:01 | -0.76% | HOLD (60) | -1.61% |
| #16 BUY | 11-10 09:01 | -1.25% | CLOSE (62) | -1.52% |

n = 3. In all three, an hour-earlier close beat the flip's price, which says positions
drifting toward an opposite signal keep drifting, not that Claude can tell. H-X on these
data is INCONCLUSIVE (`2026-11-16-run3-futures-results.md`) and nothing here changes that.

**Forward instrument (to be built, committed before it is deployed).**
`agents/flip_shadow.py`, table `shadow_flip_opinions`. In the cycle where an opposite
signal meets an open futures position, **before** the flip executes, Claude gets numbers
only (the position as `agents/exit_shadow.build_exit_context` shows it, the opposite
signal, the market) and answers `FLIP` or `HOLD`, once per event, in a background thread
like the other shadows. No trade depends on the answer; a failing or slow provider costs
the cycle nothing. `agents/shadow.py` and `agents/exit_shadow.py` stay frozen for H-S and
H-X and are imported, never edited. Cost: a few API calls a month. Deploying it touches
`run_bot.py`, a trading-path file, and restarts the bot, so it waits for the owner's go.

**Scoring (H-FLP-C), pre-registered now.** For each flip event with an error-free opinion,
replay both branches from the event bar with `_simulate_forward`: *FLIP* = the flipped
position's realised P&L plus the new position's replayed P&L (zero if blocked), *HOLD* =
the held position run to its own exit. `value = pnl(branch Claude chose) - pnl(the other
branch)`. Unit = event. **PASS** if mean `value` > 0 and the 90% bootstrap CI lies above
0; **FAIL** if the mean is <= 0; **INCONCLUSIVE** otherwise, and **always INCONCLUSIVE
below 20 scored events**. At the observed rate that is roughly 6 months, so run 3 will
not score it; it carries into run 4 with these rules unchanged. A PASS would license a
pre-registered test of Claude as a flip veto, never a live gate by itself.

## Instrument and order of work

`scripts/flip_ic.py`, built on `scripts/lock_futures_ic.py` / `scripts/trail_futures_ic.py`
(fetch retries, window helpers, discard checks) with the candidate asserted in a test.
Committed **before** the scored run, with the simulator knob, the identity test and the
fidelity check. Smoke run: counts only. Arm C and D each replay multi-year windows (BTC
about 50 minutes per window; the four alts several hours in total, local, not the VPS);
the scored run starts once from the committed instrument. Raw output to
`2026-11-16-futures-flip-run/`.

Order: knob + identity test, then `flip_ic.py` + tests, then the fidelity check, then the
smoke, then the scored run. `agents/flip_shadow.py` is built in parallel on `develop`
and not deployed without the owner's go.

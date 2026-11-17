# Pre-registration: does a breakeven lock help futures' exit? (H-PLK)

**Written 2026-11-11, before the instrument or the simulator knob exists and before any
figure below has been computed.** Third in the series after H-TPF (TP1 distance, FAIL) and
H-TRL (trail width, FAIL): `2026-11-10-futures-tp1-results.md`,
`2026-11-10-futures-trail-results.md`. It reuses their arms, criteria and discard rules.

## Why

The run-3 observation that started the series (snapshot 2026-11-09, seven closed
futures positions): winners gave their move back before closing. #13 peaked +3.48% and
closed +0.18%; #4 peaked +1.67% and closed +0.19%; #15 was +0.80% in profit at its best
and closed -1.61%. No position reached TP1, so the live "snap to breakeven at TP1"
(`trading/paper.py`, `backtest._simulate_forward`) has never fired.

H-TPF moved TP1 closer: more TP1 hits (16% to 45%), lower P&L. H-TRL tightened the trail
everywhere: less give-back (3.84 to 3.29pp), lower P&L. Both cut winners that would have
run. A **lock** is different in structure: it leaves the trail at 3.5 x ATR and the
targets where they are, and only after the position has already earned a set amount does
it raise the floor to breakeven. It cannot shorten a winner that never came back to its
entry, which is what the two failed knobs did.

n = 7 live. A reason to look, not a result.

## The prior is against this too, and it is stated here

The trail grid (1.5 / 2.5 / 3.5) favours the widest setting every time, and the TP1 test
favoured the farthest target. A lock still stops out positions that would have retraced
to entry and then run. The most likely outcome is FAIL again. It is registered because
the structure differs from the two that failed, and a FAIL here, with H-TPF and H-TRL,
closes the exit-side question and moves the search to entry timing.

## The one candidate (fixed now)

**Breakeven lock at +1.0R.** `R` = the position's own stop distance from entry, taken at
open after the live geometry: `d = |entry - stop_loss|`. When a bar's **close** is
favourable by at least `1.0 x d` (BUY: `close >= entry + d`; SELL: `close <= entry - d`),
the trail is raised to **entry** (BUY: `trail = max(trail, entry)`; SELL:
`trail = min(trail, entry)`). Nothing else changes.

- **Armed on the close, effective from the next bar.** The lock set on bar `j`'s close is
  first tested against bar `j+1`'s low/high. Applying it to the same bar would let a bar
  that closes at +1R and wicked below entry earlier in the bar exit at entry using
  information from its own close.
- Trigger on the close, not the high, like the trail's own advance and unlike TP1's high
  test. This is the conservative choice: no credit for an intrabar touch.
- Everything else stays exactly as live: stop and target distances (x2), the trail
  (3.5 x ATR, 0.8 x after TP1, advance floor 0.5), TP1/TP2 and the 50% partial, the 72h
  cap, the volatility exit, the funding exit and costs. After TP1 the live code already
  snaps the trail to entry, so the lock changes only positions that have not reached TP1.
- The lock is **one-way**: it never lowers a trail already above entry.
- **No sweep.** +1.0R and the breakeven level are the only values run or reported. No
  other trigger (0.5R, 1.5R, 2R) and no other lock level may be tried on these grids or
  windows.

Why +1.0R: it is the smallest trigger at which a lock at breakeven protects a gain larger
than the round-trip cost, and the point where the stop's risk and the position's reward
are equal. It was not fitted to the observed excursions. For the five live positions that moved
in their favour, the peaks were 1.7 to 5.1 ATR, or 0.3 to 1.1R in stop distances: only
#4 and #13 reached +1R, so a +1R lock would have armed on two of the seven live positions.

## What has to be built, and how it stays honest

The simulator has no lock. Per the repository's rule that candidates run through the
real simulator, not a copy, the test needs one new knob:

- `exit_params["be_lock_r"]` in `backtest._simulate_forward`, **default absent = off**.
- **Identity test before any scored run:** with the knob absent, every output of the
  simulator is byte-identical to the current one over the Arm C signal set. With
  `be_lock_r` present, only the lock's own effect differs. A test asserts both.
- `backtest.py` is a trading-path file for the PAPER_RUN diff check, and this edit will
  show in it. It is default-off research plumbing and changes nothing the live bot runs.
  `trading/paper.py` is **not** touched by the test. A live implementation is written only
  after a PASS, as a separate change the owner decides on.

## Contamination, disclosed

1. Arm C primary (2022-01 to 2025-10) is the window that tuned the live geometry:
   a guard, not evidence.
2. Arm B's BTC 2022 and 2023 cells overlap it. Everything else is fresh to exit tests.
3. Arm C confirmation ends **2026-10-09**, before run 3 began. The seven live positions
   suggested the idea and are never an arm.

## Arms

Identical to H-TPF and H-TRL: `backtest._simulate_forward`, futures mode, 1h, max hold 72
bars, live costs and geometry (`_exit_signal`), each entry run with and without the lock,
paired `diff = pnl(lock) - pnl(baseline)`.

- **A** synthetic BUY entries (`exit_ic` method, stride 24): BTC, ETH, BNB, XRP, LINK,
  **2024 and 2025**, 10 cells.
- **B** same, **2020 to 2023**, 20 cells.
- **C** every signal `run_backtest(counterfactual=True)` simulates for BTC futures:
  primary 2022-01-01 to 2025-10-01 (contaminated), confirmation 2025-10-01 to
  2026-10-09.

Unresolved entries stay in at 0.0 and are counted. A failed fetch is retried until it
yields a row, never dropped.

## Criteria

Same as H-TPF and H-TRL.

**Arm A and Arm B, each:** the paired mean favours the lock in **>= 80% of cells**
(>= 8/10, >= 16/20), and the pooled paired mean (entry-weighted) is **>= +0.05pp**.

**Arm C:** paired mean **> 0 in both windows** and **> 0 for BUY and for SELL**
(pooled). No size bar, no interval. It can veto, never carry a PASS.

**Verdict:** PASS if A, B and C all hold. FAIL if A's pooled paired mean <= 0 **or** B's
<= 0. INCONCLUSIVE otherwise.

Reported, never criteria: baseline and candidate absolute mean P&L; win rate; the share
of entries where the lock armed; the share that then exited at the locked floor; the
TIME_EXIT share; give-back (best excursion minus final P&L); BTC-only and ex-BTC;
`n_eff`; unresolved counts.

## Discard

- **Fidelity:** Arm C's baseline replay equals `run_backtest`'s own P&L on every recorded
  signal (max |diff| <= 1e-9).
- **Identity:** with `be_lock_r` absent the simulator reproduces its pre-edit output on
  the Arm C signal set exactly. Any difference discards the run.
- **Geometry once:** the baseline stop distance equals `stop_distance_mult` x the
  engine's stop on a probe signal.
- **Lock inert:** if the lock arms on fewer than 10% of Arm C entries, C is reported as
  such. That does not discard A or B.

## What each verdict licenses

- **PASS:** a futures breakeven lock at +1.0R becomes a **tested change** in
  `run4-backlog.md`, implemented in `trading/paper.py` beside the existing post-TP1
  snap, with the same default-off switch. The owner may ship it mid-run, restarting every
  futures-scored hypothesis from the deploy. Not applied to spot.
- **FAIL or INCONCLUSIVE:** nothing changes in config or live code. No other lock trigger
  or level may be tried on these grids or windows. With H-TPF and H-TRL, this finishes
  the exit-side candidates the run-3 observation suggested.
- Whatever the verdict, no profit claim: the absolute mean is reported beside it.

## Instrument

`scripts/lock_futures_ic.py`, built on `scripts/trail_futures_ic.py` and
`scripts/tp1_futures_ic.py` (arms, fetch retries, recording spy, discard checks) with the
candidate set explicitly to `{"be_lock_r": 1.0}` and asserted in a test. Committed
**before** the scored run, with the simulator knob and its identity test. Smoke run:
counts only. Raw output to `2026-11-11-futures-profit-lock-run/`. Arm C replays two
multi-year backtests (about 50 minutes) and needs network to Binance; the scored run
starts once, from the committed instrument.

---

# Amendment, 2026-11-11 (same day): scope of the identity check

Written before any H-PLK figure exists: the knob and the instrument are built and tested,
and nothing has been scored.

The Discard section asks the simulator to reproduce its pre-edit output "on the Arm C
signal set" when `be_lock_r` is absent. That set is produced by a 50-minute backtest per
window, and checking it against the old simulator would need that backtest run twice. The
check is done instead where it can be exhaustive and cheap, and the in-run fidelity check
still covers Arm C:

- **Identity (done, before the scored run):** the simulator at `7b35144` (before the knob)
  against the simulator with the knob, over **18,900 simulated entries** on six seeded
  random-walk frames (4,000 hourly bars each), **BUY and SELL**, with `exit_params` set
  to `None`, `{}` and `{"trailing_atr_factor": 2.5}`: **0 differences**, and no `_lock_armed`
  flag set. Script and output: `2026-11-11-futures-profit-lock-run/identity_check.py`,
  `identity.txt`.
- **In the scored run:** Arm C's baseline replay must still equal `run_backtest`'s own
  P&L on every recorded signal (max |diff| <= 1e-9), as before.

This narrows where the identity is demonstrated and widens nothing else. The knob's own
diff is guarded by `if be_lock_r`; the only unguarded additions are one `abs()` and a
false-`if`.

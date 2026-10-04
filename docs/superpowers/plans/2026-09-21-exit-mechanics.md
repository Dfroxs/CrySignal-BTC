# Exit Mechanics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an instrument that can judge exit rules on tens of thousands of synthetic entries, fix the defect that makes the current backtest discard slow trades, and run three pre-registered hypotheses against it.

**Architecture:** `_simulate_forward()` in `backtest.py` gains an optional `exit_params` dict so alternative exit rules run through the *real* simulator rather than a copy. A new `scripts/exit_ic.py` generates hypothetical entries every N candles and re-runs that simulator over an identical entry set per candidate rule, reporting paired per-entry differences. Hypotheses and pass/fail criteria are committed before the confirmatory run.

**Tech Stack:** Python 3.14, pandas, ccxt (via `signals/ohlcv.py`), stdlib `argparse`/`statistics`. Tests are plain functions in `test_pipelines.py`, run by `python3 test_pipelines.py` — **there is no pytest in this repo.**

**Spec:** `docs/superpowers/specs/2026-09-21-exit-mechanics-design.md`

## Global Constraints

- **Do not touch the running bot.** `PAPER_RUN.md`: change nothing while the run is live. Everything here is offline tooling; findings apply to the *next* run.
- **Do not retune thresholds, the 1.2× confidence multiplier, or condition weights.** `CLAUDE.md` forbids it; STEP 1 closed the entry side.
- **Land on `develop`.** The server tracks `main`.
- **Update `CHANGELOG.md` after every behaviour change.** Project rule, no exceptions.
- **Tests are added to `test_pipelines.py`** as `def test_*()` functions plus a `run("label", fn)` registration line in `main()`. Run with `python3 test_pipelines.py`; it exits 0 on pass, 1 on failure.
- **Tests must not touch network, DB or exchange.** That is the stated contract at the top of `test_pipelines.py`.
- **Confirmatory grid is 40 cells:** 5 assets (BTC, ETH, BNB, XRP, LINK) × 4 years (2020, 2021, 2022, 2023) × 2 modes. BTC 2024–2025 is burned exploratory data and must not be used to judge a hypothesis.
- **Adoption criteria (verbatim from the spec):** sign consistency ≥ 80% of that hypothesis's cells (≥ 32/40 for H2 and H3, ≥ 16/20 for H1); pooled paired mean improvement ≥ **+0.05 percentage points per entry**; no mode reversal. Failure on any criterion ends that hypothesis.

## File Structure

| File | Responsibility |
|---|---|
| `backtest.py` (modify) | `_simulate_forward()` gains `exit_params`; the unreachable `TIME_EXIT` branch is fixed |
| `test_pipelines.py` (modify) | deterministic offline fixtures pinning every exit path |
| `scripts/exit_ic.py` (create) | synthetic-entry generation, paired execution, per-cell reporting |
| `docs/superpowers/specs/2026-09-21-exit-prereg.md` (create) | hypotheses + criteria, committed before the confirmatory run |
| `CHANGELOG.md` (modify) | records the `TIME_EXIT` fix and the confirmatory result |

---

### Task 1: Pin current exit behaviour with offline fixtures

No behaviour change. This task exists so that Task 2 can prove it changed nothing, and Task 3 can show exactly what it changed.

**Files:**
- Modify: `test_pipelines.py`

**Interfaces:**
- Consumes: `backtest._simulate_forward(df, entry_idx, signal, max_hold, timeframe, mode)`
- Produces: `_exit_fixture(closes, highs, lows, atr=100.0)` returning a `pandas.DataFrame` with columns `open/high/low/close/ATR_14`; `_exit_signal(stype="BUY", entry=1000.0, sl=850.0, tp1=1150.0, tp2=1300.0, atr=100.0)` returning the signal dict `_simulate_forward` reads.

- [ ] **Step 1: Write the failing tests**

Add to `test_pipelines.py`, above the `main()` runner:

```python
# ── 20. Exit simulator ───────────────────────────────────────────────────────

def _exit_fixture(closes, highs=None, lows=None, atr=100.0):
    """Deterministic OHLCV frame for _simulate_forward. No network, no exchange."""
    import pandas as pd
    n = len(closes)
    highs = highs if highs is not None else [c + 1 for c in closes]
    lows = lows if lows is not None else [c - 1 for c in closes]
    return pd.DataFrame(
        {"open": closes, "high": highs, "low": lows, "close": closes,
         "ATR_14": [atr] * n},
        index=pd.date_range("2026-01-01", periods=n, freq="4h"),
    )


def _exit_signal(stype="BUY", entry=1000.0, sl=850.0, tp1=1150.0, tp2=1300.0, atr=100.0):
    return {"type": stype, "entry_price": entry, "stop_loss": sl,
            "take_profit": tp1, "tp2": tp2, "atr": atr}


def test_exit_tp2_path_returns_win():
    """Price walks up through TP1 then TP2 — the trade closes WIN at tp2."""
    from backtest import _simulate_forward
    closes = [1000] + [1000 + 40 * k for k in range(1, 20)]
    df = _exit_fixture(closes, highs=[c + 30 for c in closes])
    t = _simulate_forward(df, 0, _exit_signal(), 18, "4h", "spot")
    assert t["outcome"] == "WIN", t["outcome"]
    assert t["exit_price"] == 1300.0, t["exit_price"]


def test_exit_stop_path_returns_loss():
    """Price gaps straight down through the stop — trailing stop hit, LOSS."""
    from backtest import _simulate_forward
    closes = [1000] + [800] * 19
    df = _exit_fixture(closes, lows=[c - 60 for c in closes])
    t = _simulate_forward(df, 0, _exit_signal(), 18, "4h", "spot")
    assert t["outcome"] == "LOSS", t["outcome"]


def test_exit_time_cap_is_unreachable_today():
    """A position that survives the cap is recorded OPEN at 0.00%, never
    TIME_EXIT — the loop ends one candle before `age * 4 > 72` can be true.

    This test pins the DEFECT so Task 3's fix is visible as a deliberate
    change rather than an accident. When that task lands, this test is
    replaced by test_exit_time_cap_fires_at_the_cap.
    """
    from backtest import _simulate_forward
    closes = [1000] * 25
    df = _exit_fixture(closes)
    t = _simulate_forward(df, 0, _exit_signal(), 18, "4h", "spot")
    assert t["outcome"] == "OPEN", t["outcome"]
    assert t["pnl_pct"] == 0, t["pnl_pct"]
```

- [ ] **Step 2: Register them in the runner**

In `test_pipelines.py`'s `main()`, immediately before the `print(f"\n{'══' * 20}")` results block, add:

```python
    print("\n── 20. Exit simulator ──")
    run("TP1 then TP2 closes WIN",                test_exit_tp2_path_returns_win)
    run("stop hit closes LOSS",                   test_exit_stop_path_returns_loss)
    run("time cap unreachable — records OPEN",    test_exit_time_cap_is_unreachable_today)
```

- [ ] **Step 3: Run the suite**

Run: `./venv/bin/python test_pipelines.py 2>&1 | tail -20`
Expected: all three new tests PASS, and the existing suite still reports `✓ ALL PASS`. If `test_exit_tp2_path_returns_win` or `test_exit_stop_path_returns_loss` fails, the fixture prices are wrong — adjust the fixture, not the simulator.

- [ ] **Step 4: Commit**

```bash
git add test_pipelines.py
git commit -m "test: pin _simulate_forward's exit paths with offline fixtures

Includes a test asserting the current, defective behaviour: a position
alive at the cap is recorded OPEN at 0.00% and never TIME_EXIT. Pinning
it makes the fix in the next commit a visible, deliberate change.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Add `exit_params` injection

**Files:**
- Modify: `backtest.py` (`_simulate_forward`, lines ~309–335)
- Modify: `test_pipelines.py`

**Interfaces:**
- Consumes: `_exit_fixture`, `_exit_signal` from Task 1
- Produces: `_simulate_forward(df, entry_idx, signal, max_hold, timeframe, mode, exit_params=None)`. `exit_params` accepts exactly these keys: `trailing_atr_factor`, `trailing_post_tp1_factor`, `trailing_advance_min_ratio`, `max_position_hours`, `vol_expansion_exit_mult`. An unknown key raises `ValueError`.

- [ ] **Step 1: Write the failing tests**

```python
def test_exit_params_none_matches_config():
    """exit_params=None must behave exactly as reading config directly."""
    from backtest import _simulate_forward
    closes = [1000] + [1000 + 40 * k for k in range(1, 20)]
    df = _exit_fixture(closes, highs=[c + 30 for c in closes])
    a = _simulate_forward(df, 0, _exit_signal(), 18, "4h", "spot")
    b = _simulate_forward(df, 0, _exit_signal(), 18, "4h", "spot", exit_params=None)
    assert a["outcome"] == b["outcome"] and a["pnl_pct"] == b["pnl_pct"], (a, b)


def test_exit_params_trailing_factor_changes_the_exit():
    """A much wider trail must not stop the trade out at the same candle."""
    from backtest import _simulate_forward
    closes = [1000, 1120, 1040] + [1050] * 17
    df = _exit_fixture(closes, highs=[c + 10 for c in closes],
                       lows=[c - 10 for c in closes])
    tight = _simulate_forward(df, 0, _exit_signal(), 18, "4h", "spot",
                              exit_params={"trailing_atr_factor": 0.2})
    wide = _simulate_forward(df, 0, _exit_signal(), 18, "4h", "spot",
                             exit_params={"trailing_atr_factor": 10.0})
    assert tight["candles_held"] != wide["candles_held"] \
        or tight["outcome"] != wide["outcome"], (tight, wide)


def test_exit_params_rejects_an_unknown_key():
    """A typo must fail loudly. Silently ignoring it would make a whole
    confirmatory run measure the baseline against itself."""
    from backtest import _simulate_forward
    df = _exit_fixture([1000] * 20)
    try:
        _simulate_forward(df, 0, _exit_signal(), 18, "4h", "spot",
                          exit_params={"trailing_atr_factorr": 1.0})
    except ValueError:
        return
    raise AssertionError("unknown exit_params key was accepted")
```

Register in `main()` under the same section 20 block:

```python
    run("exit_params=None matches config",        test_exit_params_none_matches_config)
    run("trailing factor override takes effect",  test_exit_params_trailing_factor_changes_the_exit)
    run("unknown exit_params key is rejected",    test_exit_params_rejects_an_unknown_key)
```

- [ ] **Step 2: Run to verify they fail**

Run: `./venv/bin/python test_pipelines.py 2>&1 | grep -A3 "exit_params"`
Expected: FAIL — `_simulate_forward() got an unexpected keyword argument 'exit_params'`

- [ ] **Step 3: Implement the injection**

In `backtest.py`, change the signature at line ~309:

```python
def _simulate_forward(df, entry_idx, signal, max_hold, timeframe, mode, exit_params=None):
    """Walk forward from entry_idx, managing trailing stop and partial TP.

    `exit_params` overrides individual exit knobs for one call. None reads
    config exactly as before. This mirrors generate_signals()'s
    `threshold_override` / `disabled`: alternative rules run through the REAL
    simulator, so a candidate can never drift from what the bot actually does —
    which a second, copied simulator would do within months, silently.
    """
    _ALLOWED = {"trailing_atr_factor", "trailing_post_tp1_factor",
                "trailing_advance_min_ratio", "max_position_hours",
                "vol_expansion_exit_mult"}
    ep = exit_params or {}
    unknown = set(ep) - _ALLOWED
    if unknown:
        raise ValueError(f"unknown exit_params key(s): {sorted(unknown)}")
```

Then replace each config read. The existing block at lines ~330–333 becomes:

```python
    _default_trail = (FUTURES_CONFIG.get("trailing_atr_factor", 0.9) if mode == "futures"
                      else RISK_CONFIG.get("trailing_atr_factor", 1.0))
    base_trail_factor = ep.get("trailing_atr_factor", _default_trail)
    post_tp1_factor   = ep.get("trailing_post_tp1_factor",
                               RISK_CONFIG.get("trailing_post_tp1_factor", 0.8))
    min_adv_ratio     = ep.get("trailing_advance_min_ratio",
                               RISK_CONFIG.get("trailing_advance_min_ratio", 0.5))
```

And inside the loop, the time-exit and vol-exit config reads become:

```python
        if mode == "spot":
            _default_hours = RISK_CONFIG.get("max_position_hours_spot", 48)
        else:
            _default_hours = RISK_CONFIG.get("max_position_hours", 72)
        max_hours = ep.get("max_position_hours", _default_hours)
```

```python
        _vol_mult = ep.get("vol_expansion_exit_mult",
                           RISK_CONFIG.get("vol_expansion_exit_mult", 2.0))
        if atr_entry > 0 and atr_now > atr_entry * _vol_mult:
```

Note: `max_position_hours` is one injectable key covering both modes; the
per-mode default is still chosen by `mode`.

- [ ] **Step 4: Run to verify they pass**

Run: `./venv/bin/python test_pipelines.py 2>&1 | tail -20`
Expected: `✓ ALL PASS`. **Task 1's three tests must still pass unchanged** — that is the proof this task altered no behaviour.

- [ ] **Step 5: Commit**

```bash
git add backtest.py test_pipelines.py
git commit -m "feat: exit_params injection for _simulate_forward

Alternative exit rules run through the real simulator rather than a copy,
mirroring generate_signals()'s threshold_override/disabled. A second exit
simulator would drift from this one within months with nothing reporting it.

exit_params=None reads config exactly as before; the fixtures from the
previous commit pass unchanged, which is the proof. Unknown keys raise
rather than being ignored — a silently-dropped typo would make a whole
confirmatory run compare the baseline against itself.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Fix the unreachable `TIME_EXIT` branch

Deliberate behaviour change, isolated in its own commit so the before/after is reviewable.

**Files:**
- Modify: `backtest.py` (`_simulate_forward` loop bound)
- Modify: `test_pipelines.py`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: `_simulate_forward(..., exit_params=None)` from Task 2
- Produces: no signature change. `TIME_EXIT` becomes reachable; positions alive at the cap are recorded with a real P&L instead of an `OPEN` row at 0.

- [ ] **Step 1: Replace the defect test with the fixed-behaviour test**

Delete `test_exit_time_cap_is_unreachable_today` and its `run(...)` line. Add:

```python
def test_exit_time_cap_fires_at_the_cap():
    """A position alive at the cap must close as TIME_EXIT with a real P&L.

    It used to fall through to an OPEN row at 0.00%, which RESOLVED excludes
    from every statistic — so the backtest discarded slow trades instead of
    measuring them. The loop ran `age` 1..max_hold while the test needed
    `age * mult > max_hours`, and `max_hold * mult` equals `max_hours` exactly.
    """
    from backtest import _simulate_forward
    closes = [1000] * 30
    df = _exit_fixture(closes)
    t = _simulate_forward(df, 0, _exit_signal(), 18, "4h", "spot")
    assert t["outcome"] == "TIME_EXIT", t["outcome"]
    assert t["pnl_pct"] != 0, t["pnl_pct"]


def test_exit_open_row_still_used_when_candles_run_out():
    """OPEN is still correct when the FRAME ends early — that is a data
    limit, not a hold limit, and must not be mislabelled TIME_EXIT."""
    from backtest import _simulate_forward
    df = _exit_fixture([1000] * 6)
    t = _simulate_forward(df, 0, _exit_signal(), 18, "4h", "spot")
    assert t["outcome"] == "OPEN", t["outcome"]
```

Register:

```python
    run("time cap closes as TIME_EXIT",           test_exit_time_cap_fires_at_the_cap)
    run("OPEN kept when the frame runs out",      test_exit_open_row_still_used_when_candles_run_out)
```

- [ ] **Step 2: Run to verify the new test fails**

Run: `./venv/bin/python test_pipelines.py 2>&1 | grep -A3 "time cap"`
Expected: FAIL — outcome is `OPEN`, not `TIME_EXIT`.

- [ ] **Step 3: Extend the loop by one candle so the cap can be reached**

In `backtest.py`, change the loop bound:

```python
    # +2 rather than +1: the time-exit test needs `age * mult > max_hours`, and
    # `max_hold * mult` equals `max_hours` exactly, so a loop ending at
    # `max_hold` could never satisfy it. The branch was dead in both modes and
    # every position alive at the cap fell through to an OPEN row at 0.00% —
    # which RESOLVED excludes, so slow trades were discarded, not measured.
    for j in range(entry_idx + 1, min(entry_idx + 2 + max_hold, len(df))):
```

- [ ] **Step 4: Run to verify all tests pass**

Run: `./venv/bin/python test_pipelines.py 2>&1 | tail -20`
Expected: `✓ ALL PASS`.

- [ ] **Step 5: Record the before/after on real data**

Run and save both numbers into the commit message:

```bash
./venv/bin/python backtest.py --mode spot --start 2025-01-01 --end 2025-12-31 2>&1 | grep -E "Closed Trades|Open \(max hold\)|Total P&L"
```

Expected direction: `Open (max hold)` drops toward 0 and `Closed Trades` rises. Before the fix this window reported 2 closed / 2 open.

- [ ] **Step 6: Update CHANGELOG and commit**

Add a dated entry to `CHANGELOG.md` describing the dead branch, the off-by-one, and the measured before/after. Then:

```bash
git add backtest.py test_pipelines.py CHANGELOG.md
git commit -m "fix: TIME_EXIT was unreachable — the backtest discarded slow trades

_simulate_forward iterated age 1..max_hold while the time-exit test needs
age*mult > max_hours, and max_hold*mult equals max_hours exactly. The branch
was dead in BOTH modes; every position alive at the cap fell through to an
OPEN row at pnl=0, which RESOLVED excludes from every statistic.

Spot 2025 reported 2 closed / 2 open before this fix — half the sample
silently dropped. Measured after: see CHANGELOG.

This moves the baseline that every exit hypothesis is judged against, which
is why it lands before the harness rather than after.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Build `scripts/exit_ic.py`

**Files:**
- Create: `scripts/exit_ic.py`
- Modify: `test_pipelines.py`

**Interfaces:**
- Consumes: `backtest._simulate_forward(..., exit_params=...)`, `backtest.MAX_HOLD_CANDLES`, `backtest.RESOLVED`, `signals.ohlcv.fetch_ohlcv_df`, `config.RISK_CONFIG`
- Produces:
  - `synth_entries(df, stride, warmup=200, tail=0) -> list[int]` — candle indices
  - `run_rule(df, entries, mode, timeframe, rule) -> list[float]` — net P&L per entry, index-aligned with `entries`. A rule is `{"max_hold": int|None, "exit_params": dict|None}`
  - `paired_stats(base, cand) -> dict` with keys `n`, `mean_diff`, `win_share`, `base_mean`, `cand_mean`
  - `run_cell(symbol, year, mode, rules, stride=6) -> dict` mapping rule name → `paired_stats` against `"baseline"`. `rules` maps name → rule dict; the key `"baseline"` is required

- [ ] **Step 1: Write the failing tests**

```python
def test_synth_entries_respects_stride_and_warmup():
    from scripts.exit_ic import synth_entries
    df = _exit_fixture([1000] * 260)
    e = synth_entries(df, stride=6, warmup=200)
    assert e[0] == 200, e[:3]
    assert e[1] - e[0] == 6, e[:3]
    assert max(e) < len(df), max(e)


def test_paired_stats_is_paired_not_two_samples():
    """mean_diff must be the mean of per-entry differences, which is only
    defined when both arms have the same length and order."""
    from scripts.exit_ic import paired_stats
    s = paired_stats([1.0, -2.0, 3.0], [1.5, -1.0, 3.0])
    assert s["n"] == 3, s
    assert abs(s["mean_diff"] - 0.5) < 1e-9, s
    assert abs(s["win_share"] - (2 / 3)) < 1e-9, s
    try:
        paired_stats([1.0, 2.0], [1.0])
    except ValueError:
        return
    raise AssertionError("unequal arms were accepted")
```

```python
def test_synth_entries_drop_the_untradeable_tail():
    """Entries with no room to complete are truncated by the FRAME, not closed
    by a rule — and rules hold for different lengths, so that truncation lands
    unevenly across arms and reads as a real effect."""
    from scripts.exit_ic import synth_entries
    df = _exit_fixture([1000] * 260)
    e = synth_entries(df, stride=6, warmup=200, tail=40)
    assert max(e) < 220, max(e)


def test_run_rule_honours_a_max_hold_override():
    """A rule asking for a longer hold must actually get one. Passing only
    max_position_hours cannot do it: the loop stops at max_hold+1 candles and
    the position becomes an OPEN row that RESOLVED discards."""
    from scripts.exit_ic import run_rule
    df = _exit_fixture([1000] * 120)
    short = run_rule(df, [10], "spot", "4h", {})
    long_ = run_rule(df, [10], "spot", "4h",
                     {"max_hold": 72, "exit_params": {"max_position_hours": 288}})
    assert short[0] != long_[0] or short[0] == 0.0, (short, long_)


def test_run_rule_is_deterministic():
    """Same frame, same entries, same params -> byte-identical output. A
    confirmatory run that cannot be reproduced cannot be checked."""
    from scripts.exit_ic import run_rule, synth_entries
    df = _exit_fixture([1000 + (k % 7) * 20 for k in range(260)])
    e = synth_entries(df, stride=20, warmup=200, tail=20)
    a = run_rule(df, e, "spot", "4h", {})
    b = run_rule(df, e, "spot", "4h", {})
    assert a == b, (a[:5], b[:5])
    assert len(a) == len(e), (len(a), len(e))
```

Register:

```python
    run("synth entries honour stride/warmup",     test_synth_entries_respects_stride_and_warmup)
    run("synth entries drop untradeable tail",    test_synth_entries_drop_the_untradeable_tail)
    run("paired stats are truly paired",          test_paired_stats_is_paired_not_two_samples)
    run("run_rule honours a max_hold override",   test_run_rule_honours_a_max_hold_override)
    run("run_rule is deterministic",              test_run_rule_is_deterministic)
```

- [ ] **Step 2: Run to verify they fail**

Run: `./venv/bin/python test_pipelines.py 2>&1 | grep -A3 "synth entries"`
Expected: FAIL — `No module named 'scripts.exit_ic'`

- [ ] **Step 3: Write the module**

```python
#!/usr/bin/env python3
"""Judge EXIT rules on synthetic entries.

The full system closes ~14 trades across two years of backtest, so no exit
hypothesis can be judged on its own trade count. `condition_ic.py` already
solved this for the entry side by evaluating components every candle; this is
the same move for the exit side.

"Does trailing to breakeven after TP1 beat a flat target?" is a property of
PRICE BEHAVIOUR, not of the entry signal. Measuring it needs many entry points,
not entries with an edge. Every Nth candle becomes a hypothetical BUY and each
candidate rule runs forward from that same point.

Comparisons are PAIRED: every rule sees the identical entry set, so a difference
cannot be confounded by which trades were taken — the confound that makes
threshold sweeps in this repository unreadable (see CLAUDE.md on `open_until`).

Synthetic entries have no edge, so absolute P&L here is meaningless. Only the
RELATIVE comparison between rules on the same price population is.
"""
import argparse
import logging
import statistics
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import MAX_HOLD_CANDLES, _simulate_forward  # noqa: E402
from config import RISK_CONFIG  # noqa: E402
from signals.ohlcv import fetch_ohlcv_df  # noqa: E402

logger = logging.getLogger(__name__)

TF_FOR = {"spot": "4h", "futures": "1h"}


def synth_entries(df, stride, warmup=200, tail=0):
    """Indices of hypothetical entries: every `stride`-th candle after warmup.

    Warmup exists because ATR_14 and the other indicator columns are NaN at the
    head of the frame; entering there would compare rules on undefined stops.

    `tail` drops entries too close to the end to complete. Without it the last
    entries are truncated by the frame rather than closed by a rule, and since
    rules differ in how long they hold, that truncation lands unevenly across
    arms — a difference the paired comparison would report as a real effect.
    """
    return list(range(warmup, max(warmup, len(df) - tail), stride))


def _signal_at(df, i):
    """Build the signal dict the live path would build at candle `i`.

    Uses the same formulas as the real entry path: SL at atr_multiplier x ATR,
    TP1 at take_profit_rr x risk, TP2 at twice the TP1 distance. No `mode`
    argument: none of these formulas differ by mode — `mode` is read by
    _simulate_forward, which run_rule passes it to directly.
    """
    row = df.iloc[i]
    atr = float(row["ATR_14"])
    entry = float(row["close"])
    risk = atr * RISK_CONFIG["atr_multiplier"]
    tp1 = entry + risk * RISK_CONFIG["take_profit_rr"]
    return {"type": "BUY", "entry_price": entry, "stop_loss": entry - risk,
            "take_profit": tp1, "tp2": entry + (tp1 - entry) * 2, "atr": atr}


def run_rule(df, entries, mode, timeframe, rule):
    """Net P&L per entry under one exit ruleset, index-aligned with `entries`.

    A rule is {"max_hold": int|None, "exit_params": dict|None}. `max_hold` MUST
    be part of a rule, not just `max_position_hours`: _simulate_forward's loop
    stops at `max_hold + 1` candles no matter what the hour cap says, so a rule
    asking for a longer hold through exit_params alone never reaches it — the
    frame runs out first and the position becomes an OPEN row that RESOLVED
    discards. That is the same silent-drop that made TIME_EXIT unreachable.
    """
    max_hold = rule.get("max_hold") or MAX_HOLD_CANDLES[timeframe]
    out = []
    for i in entries:
        sig = _signal_at(df, i)
        if not (sig["atr"] > 0):
            out.append(0.0)
            continue
        t = _simulate_forward(df, i, sig, max_hold, timeframe, mode,
                              exit_params=rule.get("exit_params"))
        out.append(float(t["pnl_pct"]))
    return out


def paired_stats(base, cand):
    """Per-entry differences. Unequal arms are a bug, not a warning."""
    if len(base) != len(cand):
        raise ValueError(f"unpaired arms: {len(base)} vs {len(cand)}")
    diffs = [c - b for b, c in zip(base, cand)]
    return {
        "n": len(diffs),
        "mean_diff": statistics.fmean(diffs) if diffs else 0.0,
        "win_share": (sum(1 for d in diffs if d > 0) / len(diffs)) if diffs else 0.0,
        "base_mean": statistics.fmean(base) if base else 0.0,
        "cand_mean": statistics.fmean(cand) if cand else 0.0,
    }


def run_cell(symbol, year, mode, rules, stride=6):
    """One (asset, year, mode) cell. `rules` maps name -> exit_params dict.

    The key "baseline" must be present and is the arm every other is paired
    against.
    """
    tf = TF_FOR[mode]
    # until is the NEXT year's Jan 1: "{year}-12-31" is midnight, which silently
    # drops the final day of every cell.
    since = int(pd.Timestamp(f"{year}-01-01", tz="UTC").timestamp() * 1000)
    until = int(pd.Timestamp(f"{year + 1}-01-01", tz="UTC").timestamp() * 1000)
    df = fetch_ohlcv_df(symbol, tf, since=since, until=until)
    # The tail margin must clear the LONGEST-held rule, not the baseline. Sized
    # to the baseline, a longer rule's late entries would be cut off by the
    # frame while the baseline's completed — truncation landing on one arm only,
    # which the paired comparison would report as a real effect.
    longest = max([r.get("max_hold") or MAX_HOLD_CANDLES[tf] for r in rules.values()])
    entries = synth_entries(df, stride, tail=longest + 2)
    base = run_rule(df, entries, mode, tf, rules["baseline"])
    return {name: paired_stats(base, run_rule(df, entries, mode, tf, rule))
            for name, rule in rules.items() if name != "baseline"}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["spot", "futures"], default="futures")
    ap.add_argument("--symbols", default="BTC/USDT,ETH/USDT,BNB/USDT,XRP/USDT,LINK/USDT")
    ap.add_argument("--years", default="2020,2021,2022,2023")
    ap.add_argument("--stride", type=int, default=6,
                    help="candles between synthetic entries (default 6)")
    ap.add_argument("--only", default=None, metavar="HYPOTHESIS",
                    help="judge ONE pre-registered hypothesis. Reading a single "
                         "committed row is what removes the multiple-comparison "
                         "problem.")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    # H3 needs the partial disabled, which no knob does yet. Task 5 either adds
    # `partial_enabled` and registers H3 here, or drops H3. It is deliberately
    # absent rather than aliased to H2 — a rule table where two names share one
    # params dict silently reports the same number twice.
    # A typo in --only must fail loudly. Silently yielding an empty rule table
    # would print nothing and read as "no cells qualified".
    KNOWN = {"H1", "H2"}
    if args.only is not None and args.only not in KNOWN:
        raise ValueError(f"--only must be one of {sorted(KNOWN)}, got {args.only!r}")
    rules = {"baseline": {}}
    if args.only in (None, "H1"):
        # 72 candles, matching what futures already gets, and the hour cap moved
        # with it: 72 x 4h = 288h. Both must move or the loop bound wins.
        rules["H1"] = {"max_hold": 72, "exit_params": {"max_position_hours": 288}}
    if args.only in (None, "H2"):
        rules["H2"] = {"exit_params": {"trailing_post_tp1_factor": 1.0}}

    for year in [int(y) for y in args.years.split(",")]:
        for symbol in args.symbols.split(","):
            try:
                res = run_cell(symbol, year, args.mode, rules, args.stride)
            except Exception as exc:
                logger.warning("cell %s %s %s failed: %s — skipped",
                               symbol, year, args.mode, exc)
                continue
            for name, s in res.items():
                print(f"{args.mode:8} {symbol:10} {year}  {name:4} "
                      f"n={s['n']:5}  mean_diff={s['mean_diff']:+.4f}pp  "
                      f"win_share={s['win_share']:.3f}")


if __name__ == "__main__":
    main()
```

Note on H3: `_simulate_forward` has no "disable the partial" switch, so H3 cannot be expressed through `exit_params` alone. It is therefore **absent** from the rule table above rather than faked. Task 5 decides whether to add a `partial_enabled` key or drop H3 from the pre-registration.

- [ ] **Step 4: Run to verify the tests pass**

Run: `./venv/bin/python test_pipelines.py 2>&1 | tail -20`
Expected: `✓ ALL PASS`

- [ ] **Step 5: Smoke-run one cell**

Run: `./venv/bin/python scripts/exit_ic.py --mode spot --symbols BTC/USDT --years 2024 --only H1`

This deliberately uses **burned** BTC 2024 — a smoke test proves the plumbing runs; it must not be read as evidence. Expected: one line with `n` in the hundreds and a finite `mean_diff`.

- [ ] **Step 6: Commit**

```bash
git add scripts/exit_ic.py test_pipelines.py
git commit -m "feat: exit_ic.py — judge exit rules on synthetic entries

Two years of backtest close ~14 trades, so no exit hypothesis can be
judged on its own trade count. condition_ic.py already solved this for
the entry side; this is the same move for the exit side.

Comparisons are paired over an identical entry set, so a difference
cannot be confounded by which trades were taken.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Resolve H3 and commit the pre-registration

**Files:**
- Modify: `backtest.py` and `test_pipelines.py` *only if* H3 is kept
- Create: `docs/superpowers/specs/2026-09-21-exit-prereg.md`

**Interfaces:**
- Consumes: everything from Tasks 2–4
- Produces: a committed pre-registration. Nothing downstream reads it programmatically; it is the record that fixes the criteria before results exist.

- [ ] **Step 1: Decide H3**

H3 ("the 50/50 partial beats a single exit at TP2") needs the partial disabled, which no current knob does. Two options — pick one and say which in the commit message:

- **Keep H3:** add `partial_enabled` to `_ALLOWED`, default `True`, and guard the TP1 block with `if partial_enabled and not partial_closed and high >= tp1:` on the BUY side and the mirrored `low <= tp1` on the SELL side. Add a test asserting `partial_enabled=False` produces a trade whose exit is TP2 or the trail, never a partial.
- **Drop H3:** remove it from the pre-registration and from `exit_ic.py`'s rule table. Two hypotheses is a smaller multiple-comparison surface, which is a real benefit, not a consolation.

- [ ] **Step 2: Write the pre-registration**

Create `docs/superpowers/specs/2026-09-21-exit-prereg.md` containing, with no hedging language: each hypothesis's exact `exit_params` dict, the 40-cell grid (or 20 for H1), the three adoption criteria copied verbatim from the Global Constraints above, and this sentence: *"Failure on any criterion ends that hypothesis. Criteria are not revised after results are seen."*

- [ ] **Step 3: Run the suite**

Run: `./venv/bin/python test_pipelines.py 2>&1 | tail -5`
Expected: `✓ ALL PASS`

- [ ] **Step 4: Commit — before any confirmatory cell is run**

```bash
git add docs/superpowers/specs/2026-09-21-exit-prereg.md backtest.py test_pipelines.py
git commit -m "docs: pre-register the exit-mechanics hypotheses

Committed before the confirmatory run. A pre-registration written after
results are seen is worth nothing, and this project has already moved
goalposts twice with htf.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Run the confirmatory grid and record the result

**Files:**
- Modify: `CHANGELOG.md`
- Create: `docs/superpowers/specs/2026-09-21-exit-results.md`

**Interfaces:**
- Consumes: `scripts/exit_ic.py`, the committed pre-registration
- Produces: a recorded verdict per hypothesis

- [ ] **Step 1: Run each hypothesis separately**

One `--only` invocation per hypothesis, so each is read as a single committed row:

```bash
for H in H1 H2; do
  for M in spot futures; do
    ./venv/bin/python scripts/exit_ic.py --mode $M --only $H \
      --symbols BTC/USDT,ETH/USDT,BNB/USDT,XRP/USDT,LINK/USDT \
      --years 2020,2021,2022,2023 | tee -a data/exit_ic_$H.log
  done
done
```

This takes hours. Run it on the Mac, never the VPS — `CLAUDE.md` is explicit that 1 GB cannot hold these frames. H1 is spot-only; skip its futures pass.

- [ ] **Step 2: Score each hypothesis against its committed criteria**

For each: count cells where `mean_diff > 0`, compute the pooled mean of `mean_diff`, and check both mode subtotals. Write the counts into `docs/superpowers/specs/2026-09-21-exit-results.md` as a table with a PASS/FAIL per criterion. **Read the criteria as written.** A hypothesis that misses by a little has failed.

- [ ] **Step 3: Update CHANGELOG**

Add a dated entry stating, per hypothesis, the criterion, what was required, and what was got — the same table shape `CHANGELOG.md` already uses for STEP 1. If all hypotheses fail, say so plainly and state that nothing ships.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md docs/superpowers/specs/2026-09-21-exit-results.md
git commit -m "docs: exit-mechanics confirmatory result

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Apply only what passed**

For each hypothesis that met **every** criterion, change the corresponding value in `config.py`, with a comment naming this results document. Do not apply a hypothesis that failed any criterion. Do not apply anything to the running bot — `PAPER_RUN.md`: findings go to the *next* run.

If nothing passed, this step is empty, and that is a complete and valid outcome of the plan.

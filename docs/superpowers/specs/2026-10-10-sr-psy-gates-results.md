# Results: do the spot S/R and psychology-SL gates earn their place? (H-SR, H-PSY)

Scored against `2026-10-10-sr-psy-gates-prereg.md` (`163be83`, corrected `9486385`).
Instrument `scripts/spot_gates_ic.py` committed at `571b024` before the scored run, with
a smoke run that produced counts only. Raw output: `2026-10-10-sr-psy-gates-run/run.txt`,
`result.json`. Discard checks passed: 0 BTC psychology-rule mismatches, `sr_first`
fired on 469 engine BUYs. 2,412 NORMAL+ BUYs in total (BNB and ICP: none after the
strict HTF warmup).

## Verdicts

| gate | window | only_G | kept | random (G's condition) | kept − only [90% CI] | H-x1 | H-x2 |
|---|---|---|---|---:|---|---|---|
| `sr_first` | primary | n=166, −0.426pp | n=347, −0.165pp | −0.454pp | +0.261 [−0.526, +1.083] | INCONCLUSIVE | INCONCLUSIVE |
| `sr_first` | confirmation | n=12, **+1.023pp** | n=43, −0.515pp | −0.755pp | −1.537 [−4.240, +1.123] | FAIL | INCONCLUSIVE |
| `psy_sl_first` | primary | n=35, −0.489pp | n=347, −0.165pp | −0.469pp | +0.324 [−0.687, +1.337] | INCONCLUSIVE | FAIL |
| `psy_sl_first` | confirmation | n=6, −1.824pp | n=43, −0.515pp | −0.418pp | — (n < 10) | INCONCLUSIVE | INCONCLUSIVE |

**Overall: `sr_first` INCONCLUSIVE, `psy_sl_first` INCONCLUSIVE.** Neither FAILS
overall: the rule needs a primary FAIL, and both primaries point the gates' way
without clearing zero. **Per the pre-registration, both gates stay by default.**

## What it says

- **`sr_first` is the one that matters for data yield, and its case is weak both ways.**
  In the primary window the BUYs it alone blocks did about as well as random entries
  under resistance (−0.43 vs −0.45pp), and somewhat worse than the kept trades (−0.17pp).
  That interval is 1.6pp wide around +0.26. In the confirmation year the sign flips:
  the 12 blocked BUYs made +1.02pp against −0.52pp kept. With n=12 the interval spans
  5.4pp, so this proves nothing. It is the familiar pattern in this repository: the sign
  of a component's effect changes with the period.
- **It is also the gate that blocks the most.** In the primary window `only_sr_first`
  is 166 BUYs against 347 that pass every gate. Removing it alone would add about half
  again as many spot entries. BTC alone: 53 vs a kept arm the same order of size.
- **`psy_sl_first` blocks little and leans the gate's way.** 35 BUYs in the primary
  (about a tenth of `kept`). They did no better than random (H-x2 FAIL: −0.49 vs
  −0.47pp), and the 6 in the confirmation year lost 1.82pp. Removing it would add few
  trades, against weak evidence that it removes poor ones.
- **The kept trades still lose** (−0.17pp primary, −0.52pp confirmation). No gate here
  turns the entry into a profitable one. That is H-B's question, not this one.

## What is licensed

Nothing changes automatically. Both gates stay in run 3.

Under the pre-registration, the owner may still remove either gate as a **data-yield
design decision**. That is recorded as such, never as a test result, and it restarts
run 3's scoring window like the 2026-10-09 restarts, since it is a Phase 3 /
`config.py` change. `sr_first` is the only candidate where that trade is material:
about +50% spot entries, at an estimated cost per added trade that this test cannot
distinguish from zero. `psy_sl_first` is not worth a restart.

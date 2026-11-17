# Results: does closing-and-flipping help futures? (H-FLP)

Scored once, 2026-11-17, from the instrument committed in `165d34b` (`scripts/flip_ic.py`,
nothing edited between the signal cache and the run). Pre-registration:
`2026-11-16-futures-flip-prereg.md`. Raw output: `2026-11-16-futures-flip-run/`
(`result.json`, `result.txt`, `identity.txt`, `fidelity.json`).

## Verdict: INCONCLUSIVE (the instrument printed FAIL; read why)

`flip_ic.py` printed **FAIL**. The prereg says otherwise. Its Discard section: *"Inert: if
fewer than 30 flips occur in the baseline of Arm D pooled, Arm D is reported as underpowered
and the verdict is INCONCLUSIVE."* Arm D had **2** flips. The instrument's `verdict()` tests
`d_mean <= 0` before the flip count, so a near-empty arm that happens to sum below zero is
printed as FAIL. That ordering is a defect in my instrument against the prereg's own text,
and I am reporting the prereg's reading: **INCONCLUSIVE, underpowered**. I have not edited
the instrument after the scored run. Both readings license the same thing: nothing changes.

## What the run found

Discards: identity 0 differences in 18,900 simulations, geometry ok, **fidelity 8 of 10**
live positions matched (7 needed) with the two unmatched annotated (#18, #19: both follow a
`MACRO_CLOSE`, as in the H-V2 amendment), and the replay reproduced 2 live flips. The
instrument is faithful on the one window where the live flip can be checked. It is not the
reason the arms are empty.

| arm | signals | baseline trades | baseline total | flips | candidate - baseline |
|---|---|---|---|---|---|
| C BTC primary 2022-01 to 2025-10 | 3,299 | 289 | -20.40pp | 0 | +0.00pp |
| C BTC confirmation to 2026-10-09 | 984 | 83 | -19.38pp | 0 | +0.00pp |
| D ETH | 4,256 | 361 | +20.69pp | 0 | +0.00pp |
| D BNB | 4,391 | 373 | -75.89pp | 1 | -1.62pp |
| D XRP | 3,992 | 371 | -218.75pp | 1 | -3.72pp |
| D LINK | 4,379 | 361 | -148.68pp | 0 | +0.00pp |

Arm D pooled: mean monthly `diff` -0.0230pp, 90% CI [-0.068, +0.015], candidate ahead in
**0 of 4** assets (three are exactly tied), 2 flips against the 30 required. Arm C: +0.00pp
in both windows (it can veto, never carry).

## Why almost nothing flips

Not a bug in the instrument: I checked the cached BTC stream directly (read-only).

- Both directions are present (2,012 BUY, 1,287 SELL on the primary window).
- Holds last a median of 30 bars (mean 37, max 73).
- The next opposite raw signal arrives a **median 1,729 bars** later; only **2.7%** of signals
  have an opposite one within 72 bars.
- Of the 372 BTC trades, **none** had an opposite raw signal during its hold.

Opposite signals come in long regimes, not in the back-and-forth that run 3 showed on
11-08 to 11-10. The flip is therefore a rare event on 4.5 years of five assets: 2 flips
in about 20,000 trade-opportunities, and 3 in the single chop month of run 3.

## What the two flips say (descriptive, n = 2)

| asset | flip P&L | the same position run to its own exit | flip opened a new position |
|---|---|---|---|
| BNB | -0.81pp | -2.95pp | no (blocked) |
| XRP | -1.12pp | -2.81pp | yes |

In both, the flip beat holding on the closed position itself. Two events carry no
statistical weight, and the candidate's negative total is those two events.

## What it means

- The question the prereg asked is **not answerable from history**: the flip is too rare in
  these windows to compare two rules by month. This is a property of the engine, not of the
  missing data, so a longer window of the same engine would not fix it.
- The 2 genuine run-3 flips (#15, #16) are the only flip-rich stretch seen, one regime. They
  stay a reason to look, not a result.
- Nothing changes in config or live code. No other flip rule may be tried on these windows
  (prereg). The "no flip" change is **not** licensed; the live close-and-flip stays.
- The exit-side series (H-TPF, H-TRL, H-PLK, H-FLP) ends with no passing exit change.
- No profit claim: absolute P&L of both rules is in the table; BTC primary and confirmation
  are negative under either rule.

## Follow-ups

- The forward Claude arm (H-FLP-C, `agents/flip_shadow.py`) is the only way to collect flip
  events at the rate they occur live. Built on `develop`, not deployed (needs the owner's go,
  because the hook touches `run_bot.py` and a restart restarts every futures-scored window).
- If `flip_ic.py` is reused, move the `flips_d < MIN_FLIPS_D` check ahead of `d_mean <= 0` in
  `verdict()` (and its test) so the printed verdict matches the prereg. Not done here, because
  editing the instrument after the scored run is exactly what the prereg forbids without a
  note; this is the note.

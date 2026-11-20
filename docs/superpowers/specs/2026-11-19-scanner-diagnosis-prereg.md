# Pre-registration: why do the scanner's picks win or lose? (H-SCD, diagnosis, forward only)

**Written 2026-11-19 ~19:30 UTC, before any H-SC window figure has been computed.** The H-SC
window (prompt v2) has run since 2026-11-17 19:00 UTC: about 2 days, 55 LONG/SHORT picks.
Nobody has computed an excess, a bin or a path on them, and this prereg is written so
nobody does before its date. The owner asked for it: H-SC's verdict answers *whether*
Claude selects. It cannot say *why* a pick loses, or whether the entry or the exit is at
fault. Ninety days is too long to spend and learn only a yes or no.

Companion to H-SC (`2026-11-17-claude-scanner-prereg.md`) and H-SC2
(`2026-11-17-claude-scanner-hybrid-prereg.md`). It changes nothing in either test: not the
scanner, the prompt, the books, a criterion or a date.

## The prior, stated

The stored reasons are almost all relative momentum: "kekuatan relatif tertinggi", "di
atas EMA50", "funding negatif" for LONGs, and "terlemah 7 hari", "di bawah EMA200" for
SHORTs. The picks are concentrated: SOL/XRP LONG and AVAX/ETH SHORT repeat hour after hour.
I expect:
- Claude to agree mostly with a mechanical cross-sectional momentum rule (Part C);
- the effective sample to be far smaller than the pick count (Part D);
- no condition to survive confirmation (Part B), as no component survived STEP 1.

It is registered because each of those answers decides what the next 90 days should test.

## Data (fixed now)

- **Window:** exactly H-SC's scored window. That means the pure arm (`data/scanner.db`),
  prompt v2 cycles only (`scanner_eval.load`), error-free, one model at a time, from the
  first v2 cycle to the H-SC scoring date.
- **Same window for the other arms:** the hybrid arm (`data/scanner_hybrid.db`) and the
  random arm (`data/scanner_random.db`) get the same analysis, as comparisons. The random
  arm is the **control**: a pattern that shows equally in random picks with Claude's sides
  belongs to the market, not to Claude.
- **Prices:** H-SC's own source, Binance spot 1h klines (the local mirror serves them),
  bars indexed by close time. Highs and lows come from the same bars. Perp 1h klines are
  used only in Part E. They are refetchable from the VPS; on 2026-11-19, AVAXUSDT perp 1h
  bars were served 400 days back.
- **Features:** read only from each cycle's stored `context`, which is exactly what the
  model saw. Nothing is recomputed from prices, so no feature can use a bar the model did
  not see.
- **Units:** `ATR24 = atr_pct(1h, from context) × √24`, in percent: the book's 24h scale.
  `net = d × r − 0.20pp` (H-SC's futures round trip). `excess = d × (r − m)` as in H-SC.
  Here `d` is +1 LONG / −1 SHORT, `r` the pick's 24h close-to-close return, `m` the universe
  mean.
- **Loss** means `net < 0` (what a book feels). **Below-market** means `excess < 0` (what
  H-SC scores). Both are reported, and the two are never merged.

## Split (fixed now)

Scored days, in date order, are split in two:
- the **discovery** half: the first ⌊n/2⌋ days;
- the **confirmation** half: the rest.

Every cut point, tercile edge and candidate comes from discovery only. The instrument runs
in two steps:
1. `--phase discovery` writes `discovery.json`. It is committed, and its SHA-256 is written
   into the results doc, **before** step 2 runs.
2. `--phase confirm` refuses to run unless `discovery.json` exists and its hash matches the
   one in the results doc.

Each phase runs once. Neither is ever re-run on a longer window.

## Part A: path anatomy (entry wrong, or exit wrong?)

For every LONG/SHORT pick, over the 24 closed 1h bars after `t`:
- **MFE**: the furthest the price went in the pick's favour;
- **MAE**: the furthest it went against it;

both in ATR24 units, plus the hour each was reached.

Each **losing** pick is put in exactly one class, checked in this order:
- **A1 never right:** MFE < 0.25 ATR24. The entry was wrong from the bar it was made.
- **A2 right, then reversed:** MFE ≥ 0.50 ATR24. The idea worked first and the exit gave
  it back.
- **A3 chop:** everything else.

**Reported for each arm:**
- the share of each class;
- the same three classes for winning picks;
- the median MAE of winners. That shows how wide a stop must be not to cut a winner.

**Horizon curve, reported only:** mean day excess at 1, 2, 4, 8, 12, 24, 48 and 72 h. It
shows whether any selection decays or builds.

**Interpretation, fixed now:**
- A1 at or above the random arm's A1 share means the **pick itself** is the problem.
- A2 above random's A2 means **holding to 24 h** throws away moves Claude had right.

Either finding licenses only a new prereg (an exit or horizon rule) scored on days after
this one's date. It never licenses a change to H-SC's horizon.

## Part B: conditions (when does it lose?)

**Fixed feature list.** Per picked coin at `t`, read from `context`:
1. Claude's `confidence`;
2. `ret_24h_vs_universe_pct`;
3. `ret_7d_pct`;
4. `range_pos_24h`;
5. `close_vs_ema200_pct`;
6. `funding_pct`;
7. `oi_change_24h_pct`;
8. `ls_ratio`;
9. `taker_ratio`;
10. `atr_pct`;
11. `vol_ratio`.

Per bar:

12. BTC's `ret_7d_pct` (the market's direction);
13. **dispersion**, the cross-sectional standard deviation of `ret_24h_pct` across the coins
    in that bar (how much there is to select from);
14. **UTC session**: 00–07, 08–15, 16–23.

Side (LONG/SHORT) splits every feature: each is analysed per side, as 2 × 14 = 28 cells.

**Bins.** Terciles, with edges taken from **all universe coin-hours** in the discovery half,
not from the picks. The bins describe the market, so Claude's choices cannot shape them.
The session's three buckets are fixed. A null value is its own bin: reported, never tested.

**Two statistics per cell:**
- **Condition effect,** a property of the market:
  `B = mean d × (r − m)` over all coin-hours in the bin, taking `d` as the pick's side.
- **Claude in the condition:** the mean day value of `excess − B` over Claude's picks in
  the bin. This measures selection beyond what the condition itself gives.

**Discovery.**
- For each of the 28 cells, compute `spread = top-tercile value − bottom-tercile value` of
  Claude's `excess`, with a moving-block bootstrap over days (block 7, 5,000 draws,
  seed 2026, 90% CI).
- A cell is a **candidate** if its CI excludes 0 **and** each of the two terciles holds at
  least 15 scored days.
- At most **5** candidates, ranked by `|spread| / CI half-width`. Each is written with its
  sign.

**Confirmation.**
- Each candidate is recomputed on the confirmation half, with the discovery edges.
- **CONFIRMED:** the same sign, and its one-sided p below the Holm-adjusted α. Family =
  the candidates, α = 0.05; p comes from the same bootstrap.
- **NOT CONFIRMED:** otherwise.
- **INCONCLUSIVE:** fewer than 15 scored days per tercile in the confirmation half.

**Reported, never tested:** per coin (9), and the random and hybrid arms on the same cells.

## Part C: is Claude a momentum rule?

**Two mechanical mimics, built from `context` alone.** At each pure-arm cycle with `kL` LONG
and `kS` SHORT picks:
- **M24:** LONG the `kL` coins with the highest `ret_24h_vs_universe_pct`; SHORT the `kS`
  coins with the lowest.
- **M7:** the same, ranked by `ret_7d_pct`.

Ties break by coin order in `UNIVERSE`. Coins missing that hour are skipped. A coin cannot
be both LONG and SHORT.

**For each mimic:**
- **Agreement:** the share of Claude's picks the mimic also makes, coin and side.
- **The mimic's own H-SC statistic** over the full window.
- **Paired difference:** `Claude − mimic` per day, block bootstrap 90% CI. Both mimics form
  one family, Holm-adjusted.

**Interpretation, fixed now:**
- Agreement ≥ 70% and the diff's CI including 0 means **"Claude ≈ momentum rule"**.
- A diff CI entirely below 0 means **"the rule beats Claude"**.
- A diff CI entirely above 0 means **"Claude adds to momentum"**.

**Licenses, either way:** every later scanner prereg must carry M24 and M7 as baselines next
to random. If Claude ≈ the rule, a scanner that costs ~$7/month in API calls is replaceable
by a rule that costs nothing. That would be a new test of the rule, on fresh days.

## Part D: how many independent bets?

- **Episodes:** consecutive picks of the same coin and side, each within 24 h of the
  previous, form one episode. This is the lesson of H-S, where the effect lived in a few
  long episodes.
- **Reported for each arm:**
  - the number of episodes and the median picks per episode;
  - the top-3 coins' share of picks;
  - H-SC's statistic recomputed with **one value per episode** (its first pick), with a
    bootstrap over episodes;
  - the leave-one-episode-out range.

This says how much H-SC's 300 picks are really worth. It changes no H-SC verdict.

## Part E: execution gaps (reported only)

- **The five-minute fill:** the book prices entry at the bar close (`t`), but the scanner
  answers at about `t + 5 min`. The gap is measured as `d ×` the return from `t` to the
  open of the 5m bar at `t + 5 min` (spot 5m klines), as a mean and a distribution.
- **Spot vs perp:** the gap between the perp close stored in `scan_picks.close` and H-SC's
  spot close at `t`.
- **Book vs picks:** each demo book's realised P&L against the pick-level `net`, split at
  the level change of 2026-11-19 (1h-scale levels before it, 24h-scale levels after).

## Part F: data health (reported; gates the parts)

- Reported: missing coin-hours per coin, the null share per feature, error and timeout
  cycles, and picks excluded for a missing `t + 24h` close.
- A feature with more than 20% nulls in either half is reported but not tested in Part B.
- If more than 20% of cycles in the window errored, the whole diagnosis reads
  INCONCLUSIVE, as H-SC does.

## Date and order of work

- **Runs on the H-SC scoring date.** That is the first date with 90 scored days and 300
  picks, from cron through `agents.scoring_day`, after H-SC itself has been scored. It is
  never run earlier, and nothing is looked at in between.
- **Instrument:** `scripts/scanner_diag.py`, with tests in `test_pipelines.py`, wired into
  `agents/scoring_day.py`. It is built on synthetic data and smoke-tested only on an empty
  or synthetic database, and committed **before 2027-01-01**. It is never run on the real
  window before the date. Changing it after the first real run voids that part.

## What it licenses

- **CONFIRMED conditions, Part A or Part C findings:** a new prereg only (a filter, an exit
  or horizon rule, or a rule in place of Claude), scored on days after this diagnosis.
- **No change to the running scanner** before H-SC is scored.
- **No profit claim:** this diagnoses selection and paths, not a strategy.
- **NOT CONFIRMED or INCONCLUSIVE:** leads only. They are written down so they are not
  rediscovered as new ideas.

## What this does not cover

The BTC bot's own losses are out of scope: they come from a different engine, exits and
data. Its trade-level post-mortem is `scripts/loss_forensics.py`. A pre-registered
diagnosis of the bot's run-4 positions is a separate document if wanted.

## Amendment 2026-11-19 (before any figure): instrument and hash record

The instrument is built: `scripts/scanner_diag.py`, test
`test_scanner_diag_parts_on_synthetic_data_follow_the_prereg` (synthetic data only, with a
planted effect that discovery must find and confirmation must confirm), wired into
`agents/scoring_day.py`. Because scoring day runs unattended, the hash record works like
this:
- **H-SCD-1 (discovery)** runs on the day H-SC is scored and sent. It writes
  `data/scoring/H-SCD-discovery.json` and its `.sha256`.
- **The external record** is the Telegram result message: it ends with the SHA-256. The
  same day's backup and Mac pull carry the file off-site.
- **H-SCD-2 (confirmation)** runs no sooner than 20 h later. It refuses unless the file's
  SHA-256 equals the recorded one and its split is the same.
- **Run once:** a second discovery run is refused while the file exists.

This replaces "committed, with its hash in the results doc": the Telegram message is the
tamper-evident record made before confirmation. The results doc quotes it afterwards.
Nothing else changes.


# Results: the four futures-gated run-3 hypotheses (H-V1, H-V2 futures, H-S, H-X)

Scored 2026-11-16 15:14 UTC, after the gate opened (`futures_restart_at` 2026-10-17
14:39:46 UTC + 30 days, 2026-11-16 00:00 UTC). Data: a fresh `sqlite3 .backup` of the VPS
database taken the same minute (cycles to 2026-11-16 15:00, 721 futures cycles in the
window) and the server's `paper_run_manifest.json`. Run once with
`scripts/score_run3.py --out`, instruments as committed, nothing edited. Health: no
discard (Anthropic 0 errors in 145 entry and 441 exit calls; DeepSeek 5/29 and 2/113,
below the 20% bar; DeepSeek is not scored anyway).

Raw output: `2026-11-16-run3-futures-results-run/`.

| hypothesis | verdict |
|---|---|
| H-V1 `rel_ic_dir` | **PASS** (read the caveat: it is "less wrong than base", not "predictive") |
| H-V1 `rel_engine_dir` | **FAIL** |
| H-V2 futures | descriptive only (7 trades per book); annotated fidelity below |
| H-S (Claude) | **PASS** by the registered rule; the 20 AGREE opinions are a few episodes (caveat) |
| H-X (Claude) | **INCONCLUSIVE** |

## H-V1: relative bands against absolute ones

Per-cycle Spearman IC of net score (`buy - sell`) against the forward 24h return, paired
difference to `base`, block bootstrap, n = 697 cycles (power guard 500 met).

| variant | IC(variant) - IC(base) | 90% CI | verdict |
|---|---|---|---|
| `rel_engine_dir` | -0.090 | [-0.140, -0.031] | **FAIL** |
| `rel_ic_dir` | +0.126 | [+0.046, +0.211] | **PASS** |

Only one variant passes, so `rel_ic_dir` is the candidate. What the PASS licenses
(prereg): promoting that variant's bias rule to the main engine **in run 4**, and nothing
more. Not the weights, not the threshold.

**Caveat, read before using it.** The absolute ICs, which the criterion does not look at:

| net score | IC vs fwd 24h | 90% CI |
|---|---|---|
| `base` | -0.103 | [-0.264, +0.052] |
| `rel_ic_dir` | +0.023 | [-0.120, +0.150] |
| `rel_engine_dir` | -0.193 | [-0.345, -0.034] |

`rel_ic_dir`'s own IC is indistinguishable from zero. It passes because the base book it
is paired against sits slightly below zero, so the result is "rel_ic_dir is less wrong than
base over this month", not "rel_ic_dir predicts returns". This is one month and one
regime. Descriptively, the whole-run window (from 2026-10-09, mixed configurations, so not
the scored window) gives the same sign: +0.097 [+0.022, +0.175].

## H-V2 futures: books and fidelity

Books on the replay (the replay's absolute P&L levels do not describe live P&L; see the
10-10 amendment's reading caveat):

| book | signals | trades | mean per trade | win |
|---|---|---|---|---|
| `base` | 65 | 7 | +0.571pp | 42.9% |
| `rel_engine_dir` | 61 | 7 | +0.029pp | 28.6% |
| `rel_ic_dir` | 71 | 7 | +0.491pp (1 still open) | 42.9% |
| random long (20 seeds x 7) | | 140 | -0.550pp | |

Seven trades per book, far below the 30 the prereg needs for a criterion: descriptive.
`rel_ic_dir` is **not** ahead of `base` here (+0.491 vs +0.571pp), so H-V2 does not
corroborate H-V1; by the prereg it could not overturn it either.

**Fidelity.** 5 of the 10 live futures positions in the window did not match the replay
within one bar. Annotated individually (the 11-10 amendment, tested against
`paper_positions`):

| live | opened (UTC) | category | test |
|---|---|---|---|
| #15 SELL | 2026-11-08 23:01 | flip artefact | (b): #14 closed `FLIP` |
| #16 BUY | 2026-11-09 16:01 | flip artefact | (a) and (b): same cycle as #15's `FLIP` close |
| #17 SELL | 2026-11-10 10:01 | flip artefact | (a) and (b): same cycle as #16's `FLIP` close |
| #18 SELL | 2026-11-10 14:01 | macro-close | #17 closed `MACRO_CLOSE` at 11:31; the replay has no macro-close, so its position state differs. Neither flip test applies. |
| #19 SELL | 2026-11-13 16:01 | macro-close | #18 closed `MACRO_CLOSE` at 2026-11-13 11:31, same reason |

Flip artefacts: 3 of 10 positions = 30%, under the one-third cap (3.33). #18 and #19
are filed under the original clause's **macro-close** category ("differences the replay
cannot see ... listed individually"). That is my reading: the amendment tests (a) and (b)
do not cover them, and the original wording names the category without saying whether the
macro-close is the unmatched position's own or the preceding one's. A stricter reading
(only the unmatched position's own `MACRO_CLOSE` counts) would leave #19 unannotated and
discard H-V2. It changes nothing that is acted on: H-V2 has no criterion at 7 trades and
cannot carry or overturn H-V1.

## H-S: do Claude's AGREE signals beat its DISAGREE signals?

Trade the bot would have run (amended primary), `shadow_eval.py`, Claude only.

| | n | mean P&L |
|---|---|---|
| AGREE | 20 | +2.835pp |
| DISAGREE | 93 | -0.371pp |
| difference | | **+3.206pp**, 90% CI [+2.015, +4.425] -> **PASS** |

Power guard (20 per group) met exactly at the floor. What a PASS licenses: pre-registering,
for run 4, a test of Claude as a **veto gate**. Not wiring it into trading, and not
a statement that vetoing beats the gates already in place.

**Caveats; the PASS is thinner than its interval looks.**

- The bootstrap resamples opinions, but the 20 AGREE opinions are not 20 independent
  trades. Ten are hourly opinions inside one SELL episode, 2026-11-10 14:01 to 11-11 23:01
  (position #18, closed +5.32%), one more is the entry of #19 (+6.39%), five are
  consecutive BUY signals of 2026-10-20/21, and four are spot 4h BUYs. That is roughly
  four to six episodes, two of them the window's biggest winners. The
  registered statistic does not cluster; the cluster-robust interval would be wider and may
  include zero.
- The pre-registered statistic also mixes spot and futures opinions (`shadow_eval.py`
  scores both modes together, as the prereg's table already notes).
- The original 24h-return statistic, reported as descriptive and never a criterion, does
  not show it: AGREE +0.101 vs DISAGREE -0.210, difference +0.311, CI [-0.513, +1.152].

### H-S, descriptive follow-up: episodes instead of opinions (added 2026-11-16, after the verdict)

`scripts/shadow_cluster.py`, output in `2026-11-16-run3-futures-results-run/hs_cluster/`.
Same 113 scored opinions and the same per-opinion trade P&L as `shadow_eval.py` (it
reproduces +3.206pp). **None of this changes the verdict above**, which is the registered
statistic. The three groupings were fixed before their numbers were seen; the
stratified and within-episode cuts at the bottom were **added after** the pooled result
showed a direction mix, so they are post-hoc and should be read as such.

**1. The registered difference with episodes as the resampling unit** (episode = run of
the same mode and direction with gaps under 12h / 24h, or a calendar day per mode):

| grouping | episodes (with AGREE) | diff | 90% CI | drop the most influential episode |
|---|---|---|---|---|
| runs, gap < 12h | 42 (9) | +3.206 | [+0.025, +4.804] | +0.522 |
| runs, gap < 24h | 38 (8) | +3.206 | [-0.004, +4.429] | +0.489 |
| calendar day x mode | 35 (9) | +3.206 | [+0.359, +4.853] | +1.222 |
| futures only, gap < 12h | 14 | +3.370 | [+0.265, +5.166] | |

The PASS (lower bound +2.015) does not survive the change of unit: the lower bound moves
to **-0.004 to +0.359** depending on the grouping, i.e. on or just above zero. Under the
24h grouping it would not be a PASS. Dropping the one largest episode cuts the difference
from +3.2 to +0.5 (gap groupings).

**2. Where the difference comes from.** Mean per-opinion trade P&L by mode, direction and
verdict:

| | AGREE n / mean | DISAGREE n / mean |
|---|---|---|
| futures SELL | 11 / **+5.53** | 19 / **+2.47** |
| futures BUY | 5 / -0.19 | 29 / -1.00 |
| spot BUY | 4 / -0.80 | 45 / -1.16 |

Eleven of the 20 AGREE opinions are shorts taken in the 2026-11-10 to 11-13 drop (BTC
73.7k to 68.9k), a stretch in which even the shorts Claude DISAGREED with averaged +2.47pp.
Most DISAGREE opinions are BUYs, which lost. So a large part of the pooled gap is
direction composition: shorts in a falling month against longs.

**3. Post-hoc controls for that** (1,000 resamples; the unit is the episode, gap < 12h,
then calendar day):

| control | estimate | 90% CI |
|---|---|---|
| within mode x direction (AGREE-weighted), gap < 12h | +1.959 | [+0.212, +3.036] |
| within mode x direction, calendar day | +1.959 | [+0.265, +2.977] |
| within the same episode (episode mean removed), gap < 12h | **+0.109** | [-0.459, +0.457], permutation p = 0.36 |
| within the same episode, calendar day | **-0.048** | [-0.888, +0.655], permutation p = 0.62 |

**Reading.** Every stratum has AGREE above DISAGREE (+3.06 SELL, +0.81 futures BUY,
+0.36 spot), so the sign is consistent. Once direction is held fixed the difference is
about +2pp and the clustered interval still sits above zero. But **inside one episode,
the hours Claude agreed with did not do better than the hours it disagreed with**
(+0.11 and -0.05pp, both indistinguishable from zero). What the evidence supports is
that Claude's AGREE separated *which stretches* of the month were good for the signal's
direction, not *which hours within a stretch*. With 8 or 9 episodes that contain an AGREE,
and two of them (the 11-10 and 11-12/13 SELL runs) carrying the effect, that is a thin
basis for a veto gate. It is a reason to pre-register a veto-gate test with episode-level
counting and a longer window, not evidence that the gate works. The PASS stands as
registered and licenses exactly that and nothing more.

## H-X: does following CLOSE/HOLD beat the bot's own exits?

Claude, 12 closed positions, 328 opinions (guards: 8 positions, 30 opinions, met). CLOSE
share 10.7%. Mean value +0.761pp, 90% cluster CI [-0.577, +1.916]: **INCONCLUSIVE**.
Nothing licensed.

## Also printed (not the four scored here)

- H-L, read from `H-L.txt`: H-L1 `basis_pct` IC +0.115 [+0.003, +0.233] (n=891) **PASS**;
  H-L2 `ls_ratio` IC -0.050 [-0.263, +0.194] **INCONCLUSIVE**. H-L1's interval clears zero
  by 0.003.
- H-V2 spot: base, `rel_engine_dir` and `rel_ic_dir` are identical (4 trades, all
  losing, -1.279pp each); fidelity 4/4. Nothing to compare.
- H-B: INCONCLUSIVE (SpotSignal -0.340pp vs random -0.437pp).

## What changes

Nothing in config or live code. Candidates for the run-4 backlog, each needing its own
pre-registration before anything ships: the `rel_ic_dir` bias rule (H-V1) and a Claude
veto-gate test (H-S). For the second, the first design question is how to count
independent episodes rather than hourly opinions.

No profit claim. The `base` book's +0.571pp per trade rests on seven replayed trades with
no interval, so it does not show an edge over the random book's -0.550pp. Live futures
closed 10 positions in the window, from -2.56% to +6.39%, and the two biggest winners
(#18, #19) also carry most of H-S's AGREE opinions.

# Results — does the assembled entry beat a dart?

Scored against `2026-09-24-entry-prereg.md`, criteria committed at `7febaa1` before any
figure existed. Instrument `078ec90`, ablation fix `4ba7d6e`. Raw output:
`2026-09-24-entry-run/tuning.json`.

**Universe:** Nakhoda's ten OKX `tuning` symbols, 4h, every candle before 2025-08-30, from
the point each symbol has 200 bars on both the 1D and 1W EMA200. 39,762 candles evaluated
across 8 symbols — BNB never warms up (141 weekly bars), ICP produced no engine entry.

## Pooled

| arm | n | mean | 90% CI | win% | PF | vs random |
|---|---:|---:|---|---:|---:|---:|
| spotsignal | 1,872 | **−0.646pp** | [−0.805, −0.492] | 31.8% | 0.66 | **−0.203** |
| no_antichase | 4,060 | −0.577pp | [−0.696, −0.459] | 34.6% | 0.71 | −0.134 |
| donchian | 133 | −0.058pp | [−0.675, +0.606] | 37.6% | 0.97 | +0.384 |
| random | 37,495 | −0.443pp | [−0.481, −0.406] | 34.2% | 0.76 | — |

## Verdicts

**H-A — the assembled entry beats random entry: FAILED.**
`spotsignal.mean − random.mean = −0.203pp`. The criterion requires this to be positive; it
is negative, so the hypothesis fails on its first clause and the interval test is moot.
n = 1,872, so the `n < 100` power guard does not apply and the verdict is a FAIL, not
INCONCLUSIVE.

It fails in the worse of the two available directions. The criterion was written to detect
"no better than random". The interval [−0.805, −0.492] lies **entirely below** random's mean
of −0.443, so the finding is not that the entry adds nothing — it is that the entry is
**worse than chance**, by 0.203pp per trade after identical costs and exits.

Per symbol, `spotsignal` beat random on **1 of 8** (BTC). Post-hoc and not a criterion, but
it rules out the reading that one bad symbol dragged a pooled figure down.

**H-B — the three anti-chase gates earn their place: FAILED.**
`spotsignal.mean (−0.646) > no_antichase.mean (−0.577)` is false. Removing no_chase,
anti_fomo and entry_wick makes the system **less bad by 0.069pp per entry** and more than
doubles its entry count (1,872 → 4,060). The gates helped on 3 of 8 symbols.

*Against my own criterion:* the two intervals overlap heavily, so 0.069pp is not a strong
effect. The criterion I pre-registered was a bare comparison of means and it returns FAIL;
I am recording that the criterion was weaker than it should have been rather than
retrofitting a stronger one now. The defensible claim is the negative one: **there is no
evidence the anti-chase gates pay for themselves**, and the direction of the point estimate
is against them.

**H-C — Donchian 20/10: descriptive only, as pre-registered.**

> **Update, 2026-09-24 (later the same day).** The sibling project's out-of-domain test of
> this exact rule was finished and scored: it **FAILED all three of its pre-registered
> criteria** on 22 non-crypto instruments — annualised edge −2.2%/yr against a +3.0%
> bar, breadth 2 of 22 against 60%, and, decisively, an edge over *matched* random entry
> of −0.007pp/yr. Its timing outside crypto is indistinguishable from noise. See
> `../../../../Nakhoda/docs/OOS-FINDING-2026-09-08.md`. Whatever the figures below
> suggest, Donchian is now measured as a crypto-and-period effect, not a general
> mechanism — so treat this row as a lead about THIS dataset only, and a weak one.
Best of the four arms on every statistic — mean −0.058pp, PF 0.97, +0.384pp over random —
and it is two parameters against 22 conditions, five gates and an adaptive controller. But
n = 133, the interval [−0.675, +0.606] contains both zero and random's mean, and these are
Donchian's own tuning symbols. **This confirms nothing.** It is a lead.

## Discard conditions

All three pre-registered conditions checked before scoring; none fired.

1. Every arm resolved ≥ 20 entries (smallest: donchian, 133).
2. Random baseline stable across seeds: seeds 0–9 mean −0.4465pp, seeds 10–19 −0.4356pp,
   drift 0.0109pp against a 0.5pp limit.
3. Engine entry rate 4.71% pooled, under the 5% bar.

*On condition 3:* my wording did not say whether 5% was pooled or per symbol. Pooled passes.
Per symbol, BTC (5.54%), SOL (5.29%) and OKB (7.17%) exceed it. The condition's stated
purpose was to catch a threshold that was never applied, and that is clearly not the case:
the live spot run fires at 14.6% of cycles, well above any figure here. Recording the
ambiguity rather than picking the convenient reading.

## What this does and does not license

**Does:** it closes the question the experiment was run to answer. The premise behind
"should the bot also trade breakouts" was that pullback-only was costing opportunities. The
entry apparatus does not select good entry points of any kind — adding a second archetype
to it would add a second way to lose. Subtraction is the indicated direction, not addition.

**Does not:** this is one asset class, spot only, 4h, long-only, at a fixed threshold of 4.3
while the live controller moves 3.0–7.0. Entries are simulated independently, so this
measures the quality of an entry POINT and says nothing about portfolio sequencing. The
`_net_pnl` cost asymmetry recorded in `2026-09-21-exit-prereg.md` §7.1 is present in every
arm and cancels in the comparisons, but shifts all four means slightly optimistic.

**Nothing in `config.py`, `signals/`, `trading/` or `run_bot.py` changed as a result of this
run,** and nothing should on this evidence alone. The one change it argues for — dropping
three gates — would need its own pre-registration on data this run has now spent.

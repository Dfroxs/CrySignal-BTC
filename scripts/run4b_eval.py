#!/usr/bin/env python3
"""Score H-VR and H-BS exactly as pre-registered — `docs/superpowers/specs/2026-11-18-run4b-prereg.md`.

Two run-4 part-B candidates, both on the first 60 days of run 4's own futures cycles
(`cycle_log`), with `live_ic`'s statistics (Spearman IC vs forward 24h, 24h blocks, 90%):

- H-VR: does the `rel_ic_dir` bias rule replicate H-V1, and does it now predict on its own?
- H-BS: which way does the futures basis point? Two-sided, because H-F (2020-2026 history)
  found it bearish and H-L1 (run 3 live) found it bullish.

    # on the VPS (or a pulled DB copy), on or after 2027-01-19 17:05:03 UTC:
    ./venv/bin/python scripts/run4b_eval.py --db data/signal_history.db --out data/run4b_result.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.live_ic import HORIZON, block_ic, health, load_cycles, paired_ic_diff  # noqa: E402

FROM = pd.Timestamp("2026-11-20 17:05:03")          # S/R-fix restart (amendment 3); was 11-20 15:19:44
END = FROM + pd.Timedelta(days=60)                   # window end and earliest scoring date
MIN_N = 1000                                          # cycles with every input present


def window(df: pd.DataFrame) -> pd.DataFrame:
    """Cycles from the first full hour of run 4 whose 24h return completes by END."""
    out = df[(df.index >= FROM.ceil("h")) & (df.index + pd.Timedelta(hours=HORIZON) <= END)]
    assert out.empty or out.index.min() >= FROM, "row before run 4 in a scored set — discard"
    return out


def verdict_vr(diff, abs_ic, n):
    """PASS needs both: rel_ic_dir beats base AND predicts on its own."""
    d, dlo, _ = diff
    a, alo, _ = abs_ic
    if n < MIN_N or any(v != v for v in (d, dlo, a, alo)):
        return "INCONCLUSIVE"
    if d <= 0:
        return "FAIL"
    return "PASS" if dlo > 0 and alo > 0 and a > 0 else "INCONCLUSIVE"


def verdict_bs(ic, lo, hi, n):
    """Two-sided: a direction only when the whole interval sits on one side of zero."""
    if n < MIN_N or any(v != v for v in (ic, lo, hi)):
        return "INCONCLUSIVE"
    if lo > 0:
        return "BULLISH"
    if hi < 0:
        return "BEARISH"
    return "INCONCLUSIVE"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    if pd.Timestamp.now("UTC").tz_localize(None) < END:
        print(f"refused: H-VR / H-BS may not be scored before {END} UTC (prereg § Discipline)")
        return 2
    df = window(load_cycles(args.db, str(FROM)))
    hl = health(df)
    y = df["fwd_24h"].values
    res = {"window": [str(FROM), str(END)], "health": hl}
    diff = paired_ic_diff(df["net_rel_ic_dir"].values, df["net_base"].values, y)
    abs_vr = block_ic(df["net_rel_ic_dir"].values, y)
    v = "INCONCLUSIVE" if hl["discard"] else verdict_vr(diff[:3], abs_vr[:3], diff[3])
    res["H-VR"] = {"diff": diff[:3], "abs_ic": abs_vr[:3], "n": diff[3], "verdict": v,
                   "base_ic": block_ic(df["net_base"].values, y)[:3]}
    bs = block_ic(df["basis_pct"].values, y)
    vb = "INCONCLUSIVE" if hl["discard"] else verdict_bs(*bs)
    res["H-BS"] = {"ic": bs[0], "ci": bs[1:3], "n": bs[3], "verdict": vb}
    print(f"run 4 part B  {FROM} → {END}  cycles={hl['cycles']}  discard={hl['discard']}")
    print(f"H-VR rel_ic_dir − base  Δ={diff[0]:+.3f} [{diff[1]:+.3f}, {diff[2]:+.3f}]  "
          f"own IC {abs_vr[0]:+.3f} [{abs_vr[1]:+.3f}, {abs_vr[2]:+.3f}]  n={diff[3]}  → {v}")
    print(f"H-BS basis_pct IC={bs[0]:+.3f} [{bs[1]:+.3f}, {bs[2]:+.3f}]  n={bs[3]}  → {vb}")
    if args.out:
        args.out.write_text(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

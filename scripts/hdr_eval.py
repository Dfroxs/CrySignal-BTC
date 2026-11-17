#!/usr/bin/env python3
"""Score H-DR exactly as pre-registered — `docs/superpowers/specs/2026-11-17-derivs-replication-prereg.md`.

H-D (scored 2026-11-15) left global L/S as a momentum signal INCONCLUSIVE: pooled alt IC
+0.047 [-0.060, +0.165], BTC alone +0.282. H-DR asks the same question on hours H-D never
saw, with the same statistics (`hd_eval`), over eight alts instead of five.

    # on the VPS, on or after 2027-02-06:
    ./venv/bin/python scripts/hdr_eval.py --derivs data/derivs --fetch-klines
    ./venv/bin/python scripts/hdr_eval.py --derivs data/derivs --out data/derivs/hdr_result.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hd_eval import BTC, FIELDS, H, _ic, fetch_klines, gaps, load_symbol, pooled_ic  # noqa: E402

ALTS = ("ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT")
FROM = pd.Timestamp("2026-11-08")                        # first stat stamp H-D never scored
RETURN_BY = pd.Timestamp("2027-02-06")                   # 90 days; also the earliest scoring date
PRIMARY = "ls_global"
MIN_ALTS, MIN_POSITIVE = 6, 6                            # of 8


def scored_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Stamps from FROM whose 24h forward return completes by RETURN_BY — every symbol."""
    out = df[(df.index >= FROM) & ((df["_last_bar"] + H) <= RETURN_BY)]
    assert out.empty or out.index.min() >= FROM, "row before H-DR's window in a scored set — discard"
    return out


def verdict_alts(r: dict) -> str:
    if len(r["counted"]) < MIN_ALTS:
        return "INCONCLUSIVE"
    lo, hi = r["ci"]
    if r["pooled"] <= 0 or hi < 0:
        return "FAIL"
    positive = sum(1 for s in r["counted"] if r["per_symbol"][s]["ic"] > 0)
    return "PASS" if lo > 0 and positive >= MIN_POSITIVE else "INCONCLUSIVE"


def verdict_btc(r: dict) -> str:
    if not r["counted"]:
        return "INCONCLUSIVE"
    if r["pooled"] <= 0:
        return "FAIL"
    return "PASS" if r["ci"][0] > 0 else "INCONCLUSIVE"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--derivs", type=Path, required=True)
    ap.add_argument("--fetch-klines", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    if args.fetch_klines:
        fetch_klines(args.derivs, ALTS + (BTC,))
        return 0
    if pd.Timestamp.now("UTC").tz_localize(None) < RETURN_BY:
        print(f"refused: H-DR may not be scored before {RETURN_BY.date()} (prereg § Discipline)")
        return 2

    alts = {s: scored_rows(load_symbol(args.derivs, s)) for s in ALTS}
    btc = {BTC: scored_rows(load_symbol(args.derivs, BTC))}
    res = {"rows": {s: len(d) for s, d in {**alts, **btc}.items()},
           "gaps": {s: gaps(args.derivs, s) for s in ALTS + (BTC,)}, "fields": {}}
    print(f"H-DR  rows {res['rows']}  gaps {res['gaps']}\n")
    for f in FIELDS:
        r, rb = pooled_ic(alts, f), pooled_ic(btc, f)
        primary = f == PRIMARY
        v = (verdict_alts(r), verdict_btc(rb)) if primary else ("—", "—")
        desc = {f"pooled_{h}h": pooled_ic(alts, f, h, n_boot=200)["pooled"] for h in (4, 12)}
        res["fields"][f] = {"role": "H-DR1/H-DR2" if primary else "descriptive",
                            "verdict_alts": v[0], "verdict_btc": v[1], **r, **desc,
                            "btc": {"ic": rb["pooled"], "ci": rb["ci"]}}
        per = "  ".join(f"{s[:-4]} {r['per_symbol'][s]['ic']:+.3f}" for s in ALTS)
        print(f"{f:16s} alts {r['pooled']:+.3f} [{r['ci'][0]:+.3f}, {r['ci'][1]:+.3f}] → {v[0]}   "
              f"BTC {rb['pooled']:+.3f} [{rb['ci'][0]:+.3f}, {rb['ci'][1]:+.3f}] → {v[1]}\n  {per}"
              f"   4h {desc['pooled_4h']:+.3f} 12h {desc['pooled_12h']:+.3f}")
    if args.out:
        args.out.write_text(json.dumps(res, indent=1, default=str))
        print(f"\nwritten → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

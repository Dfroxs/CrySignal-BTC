#!/usr/bin/env python3
"""Score H-F exactly as pre-registered — `docs/superpowers/specs/2026-10-09-hist-futures-ic-prereg.md`.

Do basis, funding and taker flow (each as a trailing 168h z-score) predict BTC's forward
24h return on 2020-01-01 → 2026-08-29? Input is the four CSVs written by
`scripts/fetch_futures_history.py`.

    ./venv/bin/python scripts/hist_futures_ic.py \\
        --data docs/superpowers/specs/2026-10-09-hist-futures-ic-run \\
        --out  docs/superpowers/specs/2026-10-09-hist-futures-ic-run/result.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HORIZON = 24
Z_WINDOW, Z_MIN = 168, 120
BLOCK = 168
N_BOOT = 2000
SEED = 2026
MIN_OBS = 20_000
FIELDS = ("basis_pct", "funding_rate", "taker_ratio")
# How the engine reads each field today: +1 = high is bullish, −1 = high is bearish.
ENGINE_SIGN = {"basis_pct": +1, "funding_rate": -1, "taker_ratio": +1}
WINDOW_START, WINDOW_END = pd.Timestamp("2020-01-01"), pd.Timestamp("2026-08-29 23:00")


def load(data: Path) -> tuple[pd.DataFrame, dict]:
    """Hourly frame indexed by kline OPEN time. Every field is read at that kline's close,
    and the forward return runs from that close to the close 24 klines later."""
    rd = lambda n: pd.read_csv(data / n)
    idx = pd.date_range(WINDOW_START, WINDOW_END, freq="h")
    def kl(df, cols):
        df.index = pd.to_datetime(df["open_time"], unit="ms")
        return df[cols].astype(float).reindex(idx)
    mark = kl(rd("mark_1h.csv"), ["close"])["close"]
    index = kl(rd("index_1h.csv"), ["close"])["close"]
    perp = kl(rd("perp_1h.csv"), ["close", "volume", "taker_buy_volume"])
    fund = rd("funding.csv")
    fund_t = pd.to_datetime(fund["funding_time"], unit="ms")
    fund = pd.Series(fund["funding_rate"].astype(float).values, index=fund_t).sort_index()

    df = pd.DataFrame(index=idx)
    df["basis_pct"] = (mark - index) / index * 100
    sell = perp["volume"] - perp["taker_buy_volume"]
    df["taker_ratio"] = (perp["taker_buy_volume"] / sell).where(sell > 0)
    # Latest settled funding at the kline's close (open + 1h): known when the bot acts.
    close_t = pd.Series(idx + pd.Timedelta(hours=1), index=idx)
    pos = fund.index.searchsorted(close_t.values, side="right") - 1
    df["funding_rate"] = np.where(pos >= 0, fund.values[np.clip(pos, 0, None)], np.nan)
    df["close"] = perp["close"]
    df["fwd_24h"] = np.log(df["close"].shift(-HORIZON) / df["close"])
    for f in FIELDS:
        r = df[f].rolling(Z_WINDOW, min_periods=Z_MIN)
        df[f"z_{f}"] = (df[f] - r.mean()) / r.std()
    health = {"hours": len(idx),
              "mark_missing": float(mark.isna().mean()),
              "index_missing": float(index.isna().mean()),
              "perp_missing": float(perp["close"].isna().mean())}
    health["discard"] = health["mark_missing"] > 0.05 or health["index_missing"] > 0.05
    return df, health


def _rank(a):
    return pd.Series(a).rank().values


def _ic(x, y):
    return float(np.corrcoef(_rank(x), _rank(y))[0, 1])


def _blocks(n, rng):
    starts = rng.integers(0, max(n - BLOCK, 1), max(n // BLOCK, 1))
    return np.concatenate([np.arange(s, min(s + BLOCK, n)) for s in starts])


def block_ic(x, y, n_boot=N_BOOT, seed=SEED):
    """(IC, 2.5th pct, 97.5th pct, n) over rows where both are present (95% interval)."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    m = ~(np.isnan(x) | np.isnan(y))
    x, y = x[m], y[m]
    rng = np.random.default_rng(seed)
    boot = [_ic(x[s], y[s]) for s in (_blocks(len(x), rng) for _ in range(n_boot))]
    return _ic(x, y), float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5)), len(x)


def plain_ic(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    m = ~(np.isnan(x) | np.isnan(y))
    return (_ic(x[m], y[m]) if m.sum() > 2 else float("nan")), int(m.sum())


def verdict(ic, lo, hi, n, agree, years):
    if n < MIN_OBS:
        return "INCONCLUSIVE"
    if lo <= 0 <= hi or agree < 4:
        return "FAIL"
    return "PASS" if agree >= 5 else "INCONCLUSIVE"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    df, health = load(args.data)
    y = df["fwd_24h"].values
    print(f"H-F  BTCUSDT perp 1h  {df.index[0]} → {df.index[-1]}  hours={health['hours']}")
    print(f"missing: mark {health['mark_missing']:.2%}  index {health['index_missing']:.2%}  "
          f"perp {health['perp_missing']:.2%}"
          f"{'   ⚠️ DISCARD per prereg' if health['discard'] else ''}\n")
    result = {"health": health, "H-F": {}, "descriptive": {}}
    years = sorted(set(df.index.year))

    print("H-F  Spearman IC of z168(field) vs forward 24h log return, 95% block CI (168h)")
    for f in FIELDS:
        z = df[f"z_{f}"].values
        ic, lo, hi, n = block_ic(z, y)
        per_year = {}
        for yr in years:
            m = df.index.year == yr
            per_year[yr] = plain_ic(z[m], y[m])[0]
        agree = sum(1 for v in per_year.values() if v == v and np.sign(v) == np.sign(ic))
        v = verdict(ic, lo, hi, n, agree, len(years))
        engine = "agrees with" if np.sign(ic) == ENGINE_SIGN[f] else "OPPOSES"
        result["H-F"][f] = {"ic": ic, "ci95": [lo, hi], "n": n, "per_year": per_year,
                            "years_agree": agree, "verdict": v,
                            "engine_sign": ENGINE_SIGN[f], "sign_vs_engine": engine}
        print(f"  {f:13s} IC={ic:+.4f}  [{lo:+.4f}, {hi:+.4f}]  n={n}  "
              f"years agree {agree}/{len(years)}  → {v}  ({engine} the engine)")
        print("      per year: " + "  ".join(f"{yr}:{iv:+.3f}" for yr, iv in per_year.items()))

    print("\ndescriptive (never a criterion)")
    desc = result["descriptive"]
    for f in FIELDS:
        row = {}
        for h in (4, 12):
            fwd = np.log(df["close"].shift(-h) / df["close"]).values
            row[f"z_ic_{h}h"] = plain_ic(df[f"z_{f}"].values, fwd)[0]
        row["raw_ic_24h"] = plain_ic(df[f].values, y)[0]
        q = pd.qcut(df[f"z_{f}"], 5, labels=False, duplicates="drop")
        row["quintile_mean_fwd24_pct"] = [
            round(float(df.loc[q == i, "fwd_24h"].mean() * 100), 4) for i in range(5)]
        desc[f] = row
        print(f"  {f:13s} z-IC 4h {row['z_ic_4h']:+.4f}  12h {row['z_ic_12h']:+.4f}  "
              f"raw-IC 24h {row['raw_ic_24h']:+.4f}")
        print(f"      fwd 24h mean % by z quintile (low→high): {row['quintile_mean_fwd24_pct']}")
    corr = df[[f"z_{f}" for f in FIELDS]].corr(method="spearman").round(3)
    desc["z_corr_spearman"] = corr.to_dict()
    print("\n  z-score correlation (Spearman):\n" + corr.to_string())

    if args.out:
        args.out.write_text(json.dumps(result, indent=1, default=str))
        print(f"\nwritten → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

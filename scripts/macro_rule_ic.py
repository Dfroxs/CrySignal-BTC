#!/usr/bin/env python3
"""Score H-MC exactly as pre-registered — `docs/superpowers/specs/2026-11-19-macro-fng-prereg.md`.

Does the live macro rule help? No new entry, and every open position closed, when a
High-impact USD event is 0-2 h after a cycle's bar close. Every BTC futures signal the backtest
simulates (H-TRL's Arm C recording, the engine as live runs it) is replayed twice with
`_simulate_forward`: as is, and under the rule (suppressed, or closed at the first bar inside a
window via `flip_at`). The effect per signal is pnl_rule - pnl_base; the bootstrap resamples
episodes.

    ./venv/bin/python scripts/macro_rule_ic.py --smoke     # event table and counts, no P&L
    ./venv/bin/python scripts/macro_rule_ic.py --out docs/superpowers/specs/2026-11-19-macro-fng-run/hmc.json
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
CAL = ROOT / "data" / "macro_hist" / "forex_factory_cache.csv"
START, ENTRY_END = pd.Timestamp("2022-01-01"), pd.Timestamp("2025-03-28")
RECORD = ("2022-01-01", "2025-10-01")            # H-TRL's primary Arm C window (cached recording)
WINDOW_H = 2
GAP_H = 12
MIN_AFFECTED, MIN_EPISODES = 60, 20
N_BOOT, SEED = 5000, 2026
NY = ZoneInfo("America/New_York")

# Official release times, New York clock (prereg § Event times). Anything else is excluded.
_T = {"08:15": ["ADP Non-Farm Employment Change"],
      "08:30": ["Unemployment Claims", "Unemployment Rate", "Non-Farm Employment Change",
                "Average Hourly Earnings m/m", "CPI m/m", "CPI y/y", "Core CPI m/m", "PPI m/m",
                "Core PPI m/m", "Core PCE Price Index m/m", "Retail Sales m/m", "Core Retail Sales m/m",
                "Empire State Manufacturing Index", "Philly Fed Manufacturing Index", "Advance GDP q/q",
                "Prelim GDP q/q", "Final GDP q/q", "Employment Cost Index q/q", "Durable Goods Orders m/m",
                "Housing Starts"],
      "09:00": ["S&P/CS Composite-20 HPI y/y"],
      "09:45": ["Flash Manufacturing PMI", "Flash Services PMI", "Final Manufacturing PMI"],
      "10:00": ["ISM Manufacturing PMI", "ISM Services PMI", "JOLTS Job Openings", "CB Consumer Confidence",
                "Prelim UoM Consumer Sentiment", "Revised UoM Consumer Sentiment",
                "Prelim UoM Inflation Expectations", "New Home Sales", "Pending Home Sales m/m"],
      "13:00": ["10-y Bond Auction", "30-y Bond Auction"],
      "14:00": ["FOMC Statement", "Federal Funds Rate", "FOMC Economic Projections", "FOMC Meeting Minutes"],
      "14:30": ["FOMC Press Conference"]}
EVENT_TIME = {ev: dtime(int(t[:2]), int(t[3:])) for t, evs in _T.items() for ev in evs}
FAMILY = {"jobs": ("Unemployment", "Non-Farm", "Hourly Earnings", "JOLTS", "Employment Cost"),
          "inflation": ("CPI", "PPI", "PCE"), "fomc": ("FOMC", "Federal Funds")}


def family(event):
    return next((f for f, keys in FAMILY.items() if any(k in event for k in keys)), "other")


def load_events(path=CAL):
    """(sorted UTC-naive event times, [(time, name)], excluded count) of USD High events.
    The archive's date, the official New York release time, DST applied."""
    df = pd.read_csv(path, usecols=["DateTime", "Currency", "Impact", "Event"])
    df = df[(df.Currency == "USD") & (df.Impact == "High Impact Expected")]
    rows, excluded = [], 0
    for raw, name in zip(df.DateTime, df.Event):
        t = EVENT_TIME.get(name)
        if t is None:
            excluded += 1
            continue
        d = pd.Timestamp(raw[:10]).date()
        local = pd.Timestamp.combine(d, t).tz_localize(NY)
        rows.append((local.tz_convert("UTC").tz_localize(None), name))
    rows.sort()
    return [r[0] for r in rows], rows, excluded


def event_ahead(times, t, hours=WINDOW_H):
    """The first event E with t <= E <= t + hours, or None."""
    k = bisect.bisect_left(times, t)
    return times[k] if k < len(times) and times[k] <= t + pd.Timedelta(hours=hours) else None


def episodes(entries):
    """Episode id per signal: runs of the same direction with gaps under GAP_H, in time order."""
    order = sorted(range(len(entries)), key=lambda k: entries[k][0])
    ids, cur, last = [0] * len(entries), -1, None
    for k in order:
        t, side = entries[k]
        if last is None or side != last[1] or (t - last[0]) >= pd.Timedelta(hours=GAP_H):
            cur += 1
        ids[k], last = cur, (t, side)
    return ids


def replay(df, calls, times, names, sim, hold):
    """One row per signal in the window: base and rule P&L, how the rule acted, the family."""
    from backtest import RESOLVED
    name_at = dict(names)
    rows = []
    for i, sig, _ in calls:
        bar_close = df.index[i] + pd.Timedelta(hours=1)
        if not (START <= df.index[i] <= ENTRY_END):
            continue
        base = sim(df, i, dict(sig), hold)
        if base is None or base["outcome"] not in RESOLVED:
            rows.append({"t": bar_close, "side": sig["type"], "unresolved": True})
            continue
        pb = float(base["pnl_pct"])
        row = {"t": bar_close, "side": sig["type"], "unresolved": False, "base": pb, "rule": pb,
               "acted": None, "family": None}
        ev = event_ahead(times, bar_close)
        if ev is not None:
            row.update(rule=0.0, acted="suppressed", family=family(name_at[ev]))
        else:
            exit_idx = i + int(base["candles_held"])
            for j in range(i + 1, min(exit_idx, len(df) - 1)):
                ev = event_ahead(times, df.index[j] + pd.Timedelta(hours=1))
                if ev is not None:
                    r = sim(df, i, dict(sig), hold, {"flip_at": j})
                    if r is not None and r["outcome"] in (*RESOLVED, "FLIP"):
                        row.update(rule=float(r["pnl_pct"]), acted="closed", family=family(name_at[ev]))
                    else:
                        row["unresolved"] = True
                    break
        rows.append(row)
    return rows


def verdict(rows, n_boot=N_BOOT, seed=SEED):
    aff = [r for r in rows if not r["unresolved"] and r["acted"]]
    out = {"affected": len(aff), "episodes": 0, "mean": None, "ci": None}
    if not aff:
        return dict(out, verdict="INCONCLUSIVE")
    ep = episodes([(r["t"], r["side"]) for r in aff])
    d = np.array([r["rule"] - r["base"] for r in aff])
    groups = {}
    for k, e in enumerate(ep):
        groups.setdefault(e, []).append(d[k])
    keys = list(groups)
    rng = np.random.default_rng(seed)
    boot = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(keys), len(keys))
        boot.append(np.mean(np.concatenate([groups[keys[p]] for p in pick])))
    lo, hi = float(np.percentile(boot, 5)), float(np.percentile(boot, 95))
    out.update(episodes=len(keys), mean=float(d.mean()), ci=[lo, hi])
    if len(aff) < MIN_AFFECTED or len(keys) < MIN_EPISODES:
        return dict(out, verdict="INCONCLUSIVE")
    if out["mean"] > 0 and lo > 0:
        return dict(out, verdict="HELPS")
    if out["mean"] < 0 and hi < 0:
        return dict(out, verdict="HURTS")
    return dict(out, verdict="INCONCLUSIVE")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--smoke", action="store_true", help="event table and counts only, no P&L")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    times, named, excluded = load_events()
    in_win = [t for t in times if START <= t <= ENTRY_END + pd.Timedelta(days=4)]
    print(f"H-MC events: {len(times)} timed USD High ({excluded} excluded, time varies); "
          f"{len(in_win)} in the window")
    if args.smoke:
        return 0
    from backtest import _simulate_forward
    from scripts.tp1_futures_ic import HOLD, MODE, TF
    from scripts.trail_futures_ic import record_window
    df, calls = record_window(*RECORD)
    sim = lambda d, i, s, h, ep=None: _simulate_forward(d, i, s, h, TF, MODE, exit_params=ep)
    rows = replay(df, calls, times, named, sim, HOLD)
    res = verdict(rows)
    done = [r for r in rows if not r["unresolved"]]
    res.update(signals=len(rows), unresolved=sum(r["unresolved"] for r in rows), excluded_events=excluded,
               share_affected=res["affected"] / len(done) if done else None)
    for how in ("suppressed", "closed"):
        part = [r["rule"] - r["base"] for r in done if r["acted"] == how]
        res[how] = {"n": len(part), "mean": float(np.mean(part)) if part else None}
    res["by_family"] = {f: {"n": len(v), "mean": float(np.mean(v))} for f in ("jobs", "inflation", "fomc", "other")
                        if (v := [r["rule"] - r["base"] for r in done if r["family"] == f])}
    print(json.dumps(res, indent=1, default=str))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

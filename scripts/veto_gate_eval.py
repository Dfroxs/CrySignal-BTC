#!/usr/bin/env python3
"""Score H-VG exactly as pre-registered — `docs/superpowers/specs/2026-11-18-claude-veto-gate-prereg.md`.

Would a Claude veto gate (open only when Claude's opinion on the signal is not DISAGREE)
have improved the BTC bot's trades? H-S (run 3) compared AGREE with DISAGREE opinions as if
every hourly opinion were independent; here the unit is the EPISODE, and the statistic is
what the gate does to the trade the episode would have produced.

Per episode (a run of signals with the same mode and direction, consecutive gap < 12h, as
`shadow_cluster`): the ungated trade is the first signal's; the gated trade is the first
signal whose opinion is not DISAGREE (errors fail open), or no trade (0.0) when Claude
disagreed with all of them. Effect = gated − ungated, net P&L in pp under the bot's own
exits (`shadow_eval.trade_outcomes`). Mean effect, 90% episode bootstrap.

    # on the VPS (or a pulled DB copy), on or after 2027-02-15:
    ./venv/bin/python scripts/veto_gate_eval.py --db data/signal_history.db --out data/hvg_result.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.live_ic import norm_ts, ts_sql  # noqa: E402
from scripts.shadow_cluster import episodes  # noqa: E402
from scripts.shadow_eval import TF, _hourly_prices, signed_forward, trade_outcomes  # noqa: E402

PROVIDER = "anthropic"
FROM = pd.Timestamp("2026-11-20 17:57:15")      # shadow-context + S/R-off restart (7th amendment); was 11-20 17:05:03
SCORE_BY = FROM + pd.Timedelta(days=90)         # window end and earliest scoring date
GAP_H = 12
MIN_EPISODES, MIN_ACTED = 30, 10                # power guard
N_BOOT, SEED = 5000, 2026


def episode_effects(ops: pd.DataFrame) -> pd.DataFrame:
    """One row per episode: ungated and gated P&L, whether the gate acted, the effect.
    `ops` needs ts, mode, signal_type, verdict, error, pnl. An episode whose trade under
    either rule cannot be replayed (pnl NaN) is dropped, never filled."""
    ops = ops.sort_values("ts").reset_index(drop=True)
    ops["episode"] = episodes(ops, GAP_H)
    rows = []
    for eid, g in ops.groupby("episode", sort=True):
        ungated = g["pnl"].iloc[0]
        keep = g[(g["verdict"] != "DISAGREE") | g["error"].notna()]
        gated = keep["pnl"].iloc[0] if len(keep) else 0.0
        if ungated != ungated or gated != gated:
            continue
        rows.append({"episode": eid, "mode": g["mode"].iloc[0], "start": g["ts"].iloc[0],
                     "n": len(g), "first_verdict": g["verdict"].iloc[0],
                     "ungated": float(ungated), "gated": float(gated),
                     "acted": bool(g["verdict"].iloc[0] == "DISAGREE" and g["error"].isna().iloc[0]),
                     "effect": float(gated - ungated)})
    return pd.DataFrame(rows)


def verdict(eff: pd.DataFrame, n_boot: int = N_BOOT, seed: int = SEED) -> dict:
    n, acted = len(eff), int(eff["acted"].sum()) if len(eff) else 0
    out = {"episodes": n, "acted": acted, "mean_effect": None, "ci": None}
    if n == 0:
        return dict(out, verdict="INCONCLUSIVE")
    x = eff["effect"].to_numpy(float)
    rng = np.random.default_rng(seed)
    boot = rng.choice(x, (n_boot, n)).mean(1)
    out.update(mean_effect=float(x.mean()),
               ci=[float(np.percentile(boot, 5)), float(np.percentile(boot, 95))])
    if n < MIN_EPISODES or acted < MIN_ACTED:
        return dict(out, verdict="INCONCLUSIVE")
    if out["mean_effect"] <= 0:
        return dict(out, verdict="FAIL")
    return dict(out, verdict="PASS" if out["ci"][0] > 0 else "INCONCLUSIVE")


def load(db: Path) -> tuple[pd.DataFrame, sqlite3.Connection]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    q = (f"SELECT timestamp, provider, verdict, mode, signal_type, entry_price, stop_loss, "
         f"take_profit, atr, error FROM shadow_opinions WHERE provider = ? AND "
         f"{ts_sql('timestamp')} >= ? AND {ts_sql('timestamp')} < ?")
    ops = pd.read_sql(q, con, params=[PROVIDER, norm_ts(str(FROM)), norm_ts(str(SCORE_BY))])
    ops["ts"] = pd.to_datetime(ops["timestamp"].str.replace("T", " ").str[:19])
    return ops, con


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    if pd.Timestamp.now("UTC").tz_localize(None) < SCORE_BY:
        print(f"refused: H-VG may not be scored before {SCORE_BY.date()} (prereg § Discipline)")
        return 2
    ops, con = load(args.db)
    err_share = float(ops["error"].notna().mean()) if len(ops) else 0.0
    from backtest import _simulate_forward
    from signals.ohlcv import fetch_ohlcv_df
    since = int((FROM - pd.Timedelta(days=12)).timestamp() * 1000)
    frames = {m: fetch_ohlcv_df("BTC/USDT", tf, since=since, vwap_period=24 if tf == "1h" else 6)
              for m, tf in TF.items() if (ops["mode"] == m).any()}
    ops["pnl"] = trade_outcomes(ops, frames, _simulate_forward)
    ops["fwd"] = signed_forward(ops, _hourly_prices(con))
    eff = episode_effects(ops)
    res = verdict(eff)
    if err_share > 0.20:
        res["verdict"] = "INCONCLUSIVE"
    res["error_share"] = err_share
    res["by_mode"] = {m: {"episodes": len(g), "mean_effect": float(g["effect"].mean())}
                      for m, g in eff.groupby("mode")} if len(eff) else {}
    first = eff.groupby("first_verdict")["ungated"].agg(["count", "mean"]) if len(eff) else None
    res["descriptive_first_opinion"] = first.to_dict("index") if first is not None else {}
    print(f"H-VG  opinions={len(ops)}  episodes={res['episodes']}  gate acted={res['acted']}  "
          f"errors={err_share:.1%}")
    ci = f"[{res['ci'][0]:+.3f}, {res['ci'][1]:+.3f}]" if res["ci"] else "—"
    me = f"{res['mean_effect']:+.3f}" if res["mean_effect"] is not None else "—"
    print(f"mean effect per episode {me} pp {ci}  → {res['verdict']}")
    print(f"by mode {res['by_mode']}\nfirst opinion (descriptive) {res['descriptive_first_opinion']}")
    if args.out:
        args.out.write_text(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

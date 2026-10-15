#!/usr/bin/env python3
"""Run 3's scoring day in one command: health always, every verdict only from day 30.

Reads a PULLED copy of the run's database and manifest, never the live files:

    scp dmonk@45.151.155.178:~/playground/CrySignal-BTC/data/signal_history.db data/server.db
    scp dmonk@45.151.155.178:~/playground/CrySignal-BTC/data/paper_run_manifest.json data/server_manifest.json
    ./venv/bin/python scripts/score_run3.py --db data/server.db --manifest data/server_manifest.json
    ./venv/bin/python scripts/score_run3.py ... --out docs/superpowers/specs/<date>-run3-results-run

Before day 30 it prints HEALTH ONLY: counts, the prereg's discard conditions, shadow error
rates. No IC, P&L or verdict is computed. From day 30 it runs each pre-registered scorer
(`2026-10-09-run3-prereg.md`) and writes every output into `--out`:

    H-L, H-V1  live_ic.py          H-V2  variant_books.py (futures, spot; with fidelity)
    H-S        shadow_eval.py      H-X   exit_shadow_eval.py
    H-B        hb_eval.py          H-D   hd_eval.py (separately, on the VPS: see its docstring)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.live_ic import norm_ts, ts_sql  # noqa: E402

DAY_30 = pd.Timestamp("2026-11-08")
SHADOW_ERROR_MAX = 0.20
# Amendment 2026-10-15 (futures basis/taker pruned, FLIP/BREAKER costs): everything the
# futures scoring path feeds counts from the manifest's `futures_restart_at`, and is
# scored 30 days after it. H-L reads raw market fields and keeps the run's start.
FUTURES_SCORED = ("H-V1", "H-V2_futures", "H-S", "H-X")


def health(db: Path, start: str) -> dict:
    """Counts and the prereg's discard conditions. Nothing here is an outcome."""
    from scripts.live_ic import health as cycle_health, load_cycles
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    q = lambda sql, *p: con.execute(sql, p).fetchall()
    out = {"start": start, "cycle_health": cycle_health(load_cycles(db, start))}
    s = norm_ts(start)
    out["positions_opened"] = dict(q("SELECT mode, COUNT(*) FROM paper_positions "
                                     f"WHERE {ts_sql('opened_at')} >= ? GROUP BY mode", s))
    out["signal_blocks"] = dict(q("SELECT gate, COUNT(*) FROM signal_blocks "
                                  f"WHERE {ts_sql('timestamp')} >= ? GROUP BY gate", s))
    out["shadow"] = {}
    for table in ("shadow_opinions", "shadow_exit_opinions"):
        rows = q(f"SELECT provider, COUNT(*), SUM(error IS NOT NULL) FROM {table} "
                 f"WHERE {ts_sql('timestamp')} >= ? GROUP BY provider", s)
        out["shadow"][table] = {p: {"calls": n, "errors": e or 0,
                                    "discard": (e or 0) / n > SHADOW_ERROR_MAX if n else False}
                                for p, n, e in rows}
    out["discard"] = out["cycle_health"]["discard"]
    return out


def futures_day_30(futures_start: str | None) -> pd.Timestamp:
    """Day 30 of the futures window: the run's own unless futures restarted later."""
    if not futures_start:
        return DAY_30
    return max(DAY_30, pd.Timestamp(futures_start).tz_localize(None).normalize() + pd.Timedelta(days=30))


def commands(db: Path, start: str, out: Path,
             futures_start: str | None = None) -> list[tuple[str, list[str]]]:
    """Every scorer with its own window. H-S and H-X mix both modes, so they take the
    later start whole: spot opinions before it are dropped, which costs only data."""
    py = [sys.executable]
    s = ["--db", str(db), "--start", start]
    f = ["--db", str(db), "--start", futures_start or start]
    return [
        ("H-L", py + ["scripts/live_ic.py", *s, "--out", str(out / "live_ic.json")]),
        ("H-V1", py + ["scripts/live_ic.py", *f, "--out", str(out / "live_ic_hv1.json")]),
        ("H-V2_futures", py + ["scripts/variant_books.py", *f, "--mode", "futures",
                               "--out", str(out / "books_futures.json")]),
        ("H-V2_spot", py + ["scripts/variant_books.py", *s, "--mode", "spot",
                            "--out", str(out / "books_spot.json")]),
        ("H-S", py + ["scripts/shadow_eval.py", *f, "--out", str(out / "shadow.json")]),
        ("H-X", py + ["scripts/exit_shadow_eval.py", *f, "--out", str(out / "exit_shadow.json")]),
        ("H-B", py + ["scripts/hb_eval.py", "--end", str(DAY_30.date()), "--out-dir", str(out / "hb")]),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    manifest = json.loads(args.manifest.read_text())
    start = manifest["started_at"]
    start_db = norm_ts(start)                    # every scorer normalises again; this is for display
    fut_start = manifest.get("futures_restart_at")
    fut_start_db = norm_ts(fut_start) if fut_start else None
    fut_day_30 = futures_day_30(fut_start)

    h = health(args.db, start_db)
    print(f"run 3 from {start}  db {args.db}")
    if fut_start:
        print(f"futures-scored hypotheses {FUTURES_SCORED} from {fut_start}, day 30 {fut_day_30.date()}")
    print(json.dumps(h, indent=1, default=str))
    if pd.Timestamp.now("UTC").tz_localize(None) < DAY_30:
        print(f"\nHEALTH ONLY: no hypothesis may be scored before {DAY_30.date()}.")
        return 0
    if args.out is None:
        print("day 30 reached: pass --out <folder> to score")
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "health.json").write_text(json.dumps(h, indent=1, default=str))
    failed = []
    now = pd.Timestamp.now("UTC").tz_localize(None)
    for name, cmd in commands(args.db, start_db, args.out, fut_start_db):
        if name in FUTURES_SCORED and now < fut_day_30:
            print(f"\n=== {name}: not before {fut_day_30.date()} (futures window restarted {fut_start})")
            continue
        print(f"\n=== {name}: {' '.join(cmd[1:])}", flush=True)
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        (args.out / f"{name}.txt").write_text(r.stdout + r.stderr)
        print(r.stdout[-3000:])
        if r.returncode:
            failed.append(name)
            print(f"  ✗ {name} exited {r.returncode}: {r.stderr[-500:]}")
    print(f"\nwritten → {args.out}   failed: {failed or 'none'}")
    if fut_start:
        print("H-L is read from H-L.txt / live_ic.json only. H-V1.txt prints H-L lines too, "
              "but on the futures window: they are not H-L.")
    print("H-D runs on the VPS: scripts/hd_eval.py --derivs data/derivs --fetch-klines, then --out")
    print("H-RGL (2026-10-10-regime-live-prereg.md) scores from 2026-11-11: "
          "scripts/regime_live_ic.py --db <db> --out <folder>/regime_live")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

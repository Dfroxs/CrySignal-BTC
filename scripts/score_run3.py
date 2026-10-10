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


def commands(db: Path, start: str, out: Path) -> list[tuple[str, list[str]]]:
    py = [sys.executable]
    s = ["--db", str(db), "--start", start]
    return [
        ("H-L_H-V1", py + ["scripts/live_ic.py", *s, "--out", str(out / "live_ic.json")]),
        ("H-V2_futures", py + ["scripts/variant_books.py", *s, "--mode", "futures",
                               "--out", str(out / "books_futures.json")]),
        ("H-V2_spot", py + ["scripts/variant_books.py", *s, "--mode", "spot",
                            "--out", str(out / "books_spot.json")]),
        ("H-S", py + ["scripts/shadow_eval.py", *s, "--out", str(out / "shadow.json")]),
        ("H-X", py + ["scripts/exit_shadow_eval.py", *s, "--out", str(out / "exit_shadow.json")]),
        ("H-B", py + ["scripts/hb_eval.py", "--end", str(DAY_30.date()), "--out-dir", str(out / "hb")]),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    start = json.loads(args.manifest.read_text())["started_at"]
    start_db = norm_ts(start)                    # every scorer normalises again; this is for display

    h = health(args.db, start_db)
    print(f"run 3 from {start}  db {args.db}")
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
    for name, cmd in commands(args.db, start_db, args.out):
        print(f"\n=== {name}: {' '.join(cmd[1:])}", flush=True)
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        (args.out / f"{name}.txt").write_text(r.stdout + r.stderr)
        print(r.stdout[-3000:])
        if r.returncode:
            failed.append(name)
            print(f"  ✗ {name} exited {r.returncode}: {r.stderr[-500:]}")
    print(f"\nwritten → {args.out}   failed: {failed or 'none'}")
    print("H-D runs on the VPS: scripts/hd_eval.py --derivs data/derivs --fetch-klines, then --out")
    print("H-RGL (2026-10-10-regime-live-prereg.md) scores from 2026-11-11: "
          "scripts/regime_live_ic.py --db <db> --out <folder>/regime_live")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

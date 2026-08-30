#!/usr/bin/env python3
"""Record what a paper validation run is testing, on the host that runs it.

A manifest describes ONE run on ONE machine: when it started, at which commit,
against which parameters, and what was already in the database beforehand. It
therefore cannot be a committed file — a manifest generated on a laptop and then
cloned to a server describes a run that never happened there, which is worse
than having none at all.

Run this once, on the machine that will host the run, before enabling the
service. deploy/setup.sh does it automatically.

    python3 scripts/start_paper_run.py
"""

import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as c
from config import DATA_DIR, SIGNAL_HISTORY_DB

MANIFEST = os.path.join(DATA_DIR, "paper_run_manifest.json")


def _git(*args):
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:
        return ""


def build():
    pre = {}
    if os.path.exists(SIGNAL_HISTORY_DB):
        conn = sqlite3.connect(SIGNAL_HISTORY_DB)
        for t in ("signals", "paper_positions", "cycle_log", "signal_blocks"):
            try:
                pre[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                pre[t] = None       # table not created yet — first ever run
        conn.close()

    return {
        "started_at": datetime.now(UTC).isoformat(),
        "host": os.uname().nodename,
        "git_sha": _git("rev-parse", "HEAD"),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "working_tree_clean": not _git("status", "--porcelain", "--untracked-files=no"),
        "purpose": "Frozen-parameter paper validation run. No parameter may be "
                   "changed while this runs; doing so voids the sample.",
        "rows_before_start": pre,
        "note": "Rows above predate this run. Filter analysis on "
                "timestamp >= started_at. Only paper_positions affects P&L, "
                "win rate, drawdown and the adaptive threshold — if it is 0, "
                "those all start from zero regardless of the other counts.",
        "frozen_parameters": {
            "SIGNAL_THRESHOLD": c.SIGNAL_THRESHOLD,
            "SIGNAL_MAX_SCORE": c.SIGNAL_MAX_SCORE,
            "THRESHOLD_MIN": c.THRESHOLD_MIN,
            "THRESHOLD_MAX": c.THRESHOLD_MAX,
            "SPOT_THRESHOLD": c.SPOT_THRESHOLD,
            "SPOT_MAX_SCORE": c.SPOT_MAX_SCORE,
            "SPOT_THRESHOLD_MIN": c.SPOT_THRESHOLD_MIN,
            "SPOT_THRESHOLD_MAX": c.SPOT_THRESHOLD_MAX,
            "DISABLED_CONDITIONS": sorted(c.DISABLED_CONDITIONS),
            "threshold_env_override_active": bool(
                os.getenv("SIGNAL_THRESHOLD") or os.getenv("SPOT_THRESHOLD")
            ),
            "ADAPTIVE_WINDOW_HOURS": c.ADAPTIVE_WINDOW_HOURS,
            "ADAPTIVE_MAX_SIGNALS": c.ADAPTIVE_MAX_SIGNALS,
            "account_balance": c.RISK_CONFIG["account_balance"],
            "futures_balance": c.FUTURES_CONFIG["futures_balance"],
            "risk_per_trade_spot": c.RISK_CONFIG["risk_per_trade"],
            "risk_per_trade_futures": c.FUTURES_CONFIG["risk_per_trade"],
            "execution": dict(c.EXECUTION_CONFIG),
            "risk_limits": dict(c.RISK_LIMITS),
        },
    }


def main():
    if os.path.exists(MANIFEST) and "--force" not in sys.argv:
        with open(MANIFEST) as f:
            existing = json.load(f)
        print(f"A run is already recorded on this host:\n"
              f"  started {existing.get('started_at')}  at {existing.get('git_sha', '')[:8]}\n"
              f"Re-recording resets the run's start time and orphans the data "
              f"collected so far.\nPass --force if that is what you want.")
        return 1

    m = build()
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(MANIFEST, "w") as f:
        json.dump(m, f, indent=2)

    print(f"Paper run recorded → {MANIFEST}")
    print(f"  host       {m['host']}")
    print(f"  commit     {m['git_sha'][:8]} on {m['git_branch']}"
          f"{'' if m['working_tree_clean'] else '  (WORKING TREE DIRTY)'}")
    print(f"  started    {m['started_at']}")
    print(f"  thresholds spot {m['frozen_parameters']['SPOT_THRESHOLD']} / "
          f"futures {m['frozen_parameters']['SIGNAL_THRESHOLD']}")
    if m["frozen_parameters"]["threshold_env_override_active"]:
        print("  ⚠️  A threshold env override is set — the adaptive controller "
              "is OFF for this run.")
    pos = (m["rows_before_start"] or {}).get("paper_positions")
    if pos:
        print(f"  ⚠️  {pos} paper positions already exist; P&L and drawdown do "
              f"NOT start from zero.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

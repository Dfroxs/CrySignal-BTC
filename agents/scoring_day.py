#!/usr/bin/env python3
"""Scoring day: run each pre-registered test's own instrument once, when it is due, and send
the result to Telegram. No LLM; nothing here computes a statistic itself.

Every test here is scored once, on a date or a count fixed in its prereg, by a script
committed before the data existed. A chat session does not survive the months between, so
this runs daily from cron. It never runs an instrument early: due-ness is a count or a date,
and the date-gated instruments refuse to score before their date anyway. A test is marked
scored only after its instrument exited 0 and the message was sent. Its full output is
kept in `data/scoring/`.

    python3 -m agents.scoring_day --dry-run     # list what is due; run nothing
    python3 -m agents.scoring_day               # run what is due, send, record

Cron (user crontab on the VPS, after the backup, ops report and archive):
    20 4 * * * cd ~/playground/CrySignal-BTC && ./venv/bin/python -m agents.scoring_day \
               >> data/scoring/scoring_day.log 2>&1
"""
from __future__ import annotations

import argparse
import html
import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = "./venv/bin/python"
TELEGRAM_CHARS = 3500
TIMEOUT_S = 3600


def _utc(ts):
    """A naive instrument timestamp (UTC by convention) as an aware datetime."""
    return ts.to_pydatetime().replace(tzinfo=UTC)


def tests(facts):
    """(key, plain name, due(facts, now) -> (bool, why), commands). Dates come from the
    instruments themselves, so the two can never disagree."""
    from scripts.hdr_eval import RETURN_BY as HDR_DATE
    from scripts.run4b_eval import END as RUN4B_DATE
    from scripts.veto_gate_eval import SCORE_BY as HVG_DATE
    hdr, run4b, hvg = _utc(HDR_DATE), _utc(RUN4B_DATE), _utc(HVG_DATE)
    on = lambda d: (lambda f, now: (now >= d, f"tanggal {d:%d %b %Y %H:%M} UTC"))
    out = "data/scoring"
    return [
        ("H-SC2", "Claude + engine vs Claude murni",
         lambda f, now: (f["paired_days"] >= 60, f'{f["paired_days"]} dari 60 hari berpasangan'),
         [[PY, "scripts/scanner_eval.py", "--db", "data/scanner.db", "--hybrid-db",
           "data/scanner_hybrid.db", "--only", "hsc2", "--out", f"{out}/H-SC2.json"]]),
        ("H-SC", "Claude murni vs rata-rata 9 koin",
         lambda f, now: (f["pure_days"] >= 90 and f["pure_picks"] >= 300,
                         f'{f["pure_days"]} dari 90 hari, {f["pure_picks"]} dari 300 pilihan'),
         [[PY, "scripts/scanner_eval.py", "--db", "data/scanner.db", "--only", "hsc",
           "--out", f"{out}/H-SC.json"]]),
        ("H-VR/H-BS", "Perbaikan engine: aturan varian dan arah basis", on(run4b),
         [[PY, "scripts/run4b_eval.py", "--db", "data/signal_history.db", "--out", f"{out}/H-VR-H-BS.json"]]),
        ("H-DR", "Data long/short memprediksi harga", on(hdr),
         [[PY, "scripts/hdr_eval.py", "--derivs", "data/derivs", "--fetch-klines"],
          [PY, "scripts/hdr_eval.py", "--derivs", "data/derivs", "--out", f"{out}/H-DR.json"]]),
        ("H-VG", "Veto Claude untuk bot BTC", on(hvg),
         [[PY, "scripts/veto_gate_eval.py", "--db", "data/signal_history.db", "--out", f"{out}/H-VG.json"]]),
        ("H-FLP-C", "Opini Claude saat bot berbalik arah",
         lambda f, now: (f["flip_events"] >= 20, f'{f["flip_events"]} dari 20 kejadian flip'),
         [[PY, "scripts/flip_shadow_eval.py", "--db", "data/signal_history.db", "--start",
           facts.get("run_start") or "", "--out", f"{out}/H-FLP-C.json"]]),
    ]


def collect_facts(root=ROOT):
    """Counts the count-gated tests wait on. Counts only: no return or verdict is read."""
    from agents.dashboard import _v2_days
    data = Path(root) / "data"
    pure, hyb = _v2_days(data / "scanner.db"), _v2_days(data / "scanner_hybrid.db")
    f = {"pure_days": len(pure["days"]) if pure else 0, "pure_picks": pure["picks"] if pure else 0,
         "paired_days": len(pure["days"] & hyb["days"]) if pure and hyb else 0,
         "flip_events": 0, "run_start": None}
    try:
        f["run_start"] = json.loads((data / "paper_run_manifest.json").read_text())["started_at"]
    except (OSError, ValueError, KeyError):
        pass
    if f["run_start"] and (data / "signal_history.db").exists():
        con = sqlite3.connect(f"file:{data / 'signal_history.db'}?mode=ro", uri=True)
        try:
            f["flip_events"] = con.execute(
                "SELECT COUNT(DISTINCT o.position_id) FROM shadow_flip_opinions o JOIN paper_positions p "
                "ON p.id = o.position_id WHERE o.error IS NULL AND p.outcome = 'FLIP' "
                "AND substr(replace(o.timestamp, 'T', ' '), 1, 19) >= substr(replace(?, 'T', ' '), 1, 19)",
                (f["run_start"],)).fetchone()[0]
        except sqlite3.Error:
            pass
        finally:
            con.close()
    return f


def _load(path):
    try:
        s = json.loads(Path(path).read_text())
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def _run(cmd, cwd):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT_S)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def message(key, name, output, ok, when):
    """Short, escaped. The verdict lines are near the end of every instrument's output."""
    tail = output.strip()[-TELEGRAM_CHARS:]
    head = (f"🧪 <b>Hasil uji {html.escape(key)}</b>: {html.escape(name)}\n" if ok else
            f"⚠️ <b>Uji {html.escape(key)} gagal dijalankan</b>: {html.escape(name)}\n")
    return (head + f"{when:%Y-%m-%d %H:%M} UTC, dihitung sekali sesuai prereg.\n"
            f"<pre>{html.escape(tail)}</pre>")


def run_due(now, facts, state_path, root=ROOT, run=_run, send=None, dry=False):
    """Run every due, not-yet-scored test. Returns the keys scored this time."""
    if send is None:
        from notifier.common import _send_telegram_message
        send = lambda t: _send_telegram_message(t, "scoring")
    state = _load(state_path)
    done = []
    out_dir = Path(root) / "data" / "scoring"
    for key, name, due, cmds in tests(facts):
        if key in state:
            continue
        is_due, why = due(facts, now)
        if dry:
            print(f"{key:10s} {'DUE' if is_due else 'not yet'}  ({why})")
            continue
        if not is_due:
            continue
        outputs, ok = [], True
        for cmd in cmds:
            code, text = run(cmd, root)
            outputs.append(f"$ {' '.join(cmd[1:])}\n{text}")
            if code != 0:
                ok = False
                break
        full = "\n".join(outputs)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{key.replace('/', '-')}-{now:%Y%m%d-%H%M}.txt").write_text(full)
        sent = send(message(key, name, full, ok, now))
        if ok and sent:
            state[key] = {"scored_at": now.isoformat(), "why": why}
            done.append(key)
            p = Path(state_path)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=1))
            tmp.replace(p)
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--state", default=None, help="default <root>/data/scoring_state.json")
    ap.add_argument("--dry-run", action="store_true", help="list what is due; run nothing")
    args = ap.parse_args()
    root = Path(args.root)
    sys.path.insert(0, str(root))
    from dotenv import load_dotenv
    load_dotenv(root / ".env")
    now = datetime.now(UTC)
    facts = collect_facts(root)
    state = args.state or root / "data" / "scoring_state.json"
    done = run_due(now, facts, state, root, dry=args.dry_run)
    if not args.dry_run:
        print(f"{now:%Y-%m-%d %H:%M} UTC  facts {facts}  scored {done or 'nothing'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

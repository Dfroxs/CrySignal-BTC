#!/usr/bin/env python3
"""Hourly real-time alarm for both bots. No LLM: rules only, Telegram only on change.

The daily ops report (03:30 UTC) is too slow for a dead bot. This runs every hour, ten
minutes after the bot's :01 cycle, and sends a short Telegram message the moment
something is wrong — then stays quiet while it persists (one reminder every
`REALERT_H` hours) and sends one "✅ pulih" when it clears. It reads health only, never
hypothesis statistics: no forward returns, no variant or shadow performance.

    python3 -m agents.alarm --dry-run      # print what would be sent; state untouched
    python3 -m agents.alarm                # send on change, update data/alarm_state.json

Exit code 1 while any condition is active, so cron mail / a wrapper can see it.

Cron (user crontab on the VPS). XDG_RUNTIME_DIR lets `systemctl --user` reach the user
manager that owns nakhoda-alloc; without it the check reads 'unknown' and alarms:
    10 * * * * cd ~/playground/CrySignal-BTC && XDG_RUNTIME_DIR=/run/user/$(id -u) \
               ./venv/bin/python -m agents.alarm >> data/alarm.log 2>&1
"""
from __future__ import annotations

import argparse
import html
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MAX_CYCLE_AGE_MIN = 75          # cycles run hourly at :01
MIN_MEM_AVAILABLE_MB = 120      # host has 960 MB, ~380 MB free with both bots up
MAX_DISK_USED_PCT = 90
SHADOW_ERROR_RUN = 3            # this many newest opinions all errored → provider down
REALERT_H = 6


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------

def _ts(col):
    """Rows hold both 'YYYY-MM-DD HH:MM:SS' and ISO 'YYYY-MM-DDTHH:MM:SS+00:00', which
    do not compare as strings. Same normalisation as agents.ops_report."""
    return f"datetime(substr({col}, 1, 19))"


def collect_db_facts(db_path, now):
    """Last cycle age, whether the newest futures cycle is blind, and failing shadow
    providers. An unreadable database comes back as `db_error`, never raises."""
    f = {"db_error": None, "last_cycle_age_min": None, "futures_funding_zero": False,
         "shadow_failing": []}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return dict(f, db_error=str(exc))
    try:
        q = lambda sql, *p: con.execute(sql, p).fetchall()
        last = q(f"SELECT MAX({_ts('timestamp')}) FROM cycle_log")[0][0]
        if last:
            age = now.replace(tzinfo=None) - datetime.fromisoformat(last)
            f["last_cycle_age_min"] = round(age.total_seconds() / 60, 1)
        row = q(f"SELECT funding_rate FROM cycle_log WHERE mode='futures' "
                f"ORDER BY {_ts('timestamp')} DESC, id DESC LIMIT 1")
        f["futures_funding_zero"] = bool(row) and row[0][0] == 0
        tables = {r[0] for r in q("SELECT name FROM sqlite_master WHERE type='table'")}
        if "shadow_opinions" in tables:
            for (prov,) in q("SELECT DISTINCT provider FROM shadow_opinions ORDER BY provider"):
                errs = [e for (e,) in q("SELECT error FROM shadow_opinions WHERE provider=? "
                                        "ORDER BY id DESC LIMIT ?", prov, SHADOW_ERROR_RUN)]
                if len(errs) == SHADOW_ERROR_RUN and all(e is not None for e in errs):
                    f["shadow_failing"].append(prov)
    except sqlite3.Error as exc:
        f["db_error"] = str(exc)
    finally:
        con.close()
    return f


def _run(cmd, timeout=20):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=timeout).stdout.strip()
    except Exception:
        return ""


def collect_host_facts(run=_run, meminfo_path="/proc/meminfo", disk_usage=shutil.disk_usage,
                       home=None):
    """Services, available memory, home-filesystem use. A service that cannot be read is
    'unknown' (alarms); memory/disk that cannot be read are None (no /proc on a Mac)."""
    mem = None
    try:
        for line in Path(meminfo_path).read_text().splitlines():
            if line.startswith("MemAvailable:"):
                mem = round(int(line.split()[1]) / 1024, 1)
    except (OSError, ValueError, IndexError):
        pass
    disk = None
    try:
        u = disk_usage(str(home or Path.home()))
        disk = round(u.used / (u.used + u.free) * 100, 1)    # df's Use%: excludes reserved
    except (OSError, ZeroDivisionError, AttributeError):
        pass
    return {"spot_svc": run("systemctl is-active spotsignal") or "unknown",
            "alloc_svc": run("systemctl --user is-active nakhoda-alloc") or "unknown",
            "mem_available_mb": mem, "disk_used_pct": disk}


# ---------------------------------------------------------------------------
# Rules — the only place a condition is decided. Keys are stable across runs so the
# state file can tell a new condition from one that persists.
# ---------------------------------------------------------------------------

def conditions(f):
    out = {}
    for svc, key in (("spotsignal", "spot_svc"), ("nakhoda-alloc", "alloc_svc")):
        if f.get(key) != "active":
            out[f"svc:{svc}"] = f"layanan {svc}: {f.get(key)}"
    if f.get("db_error"):
        out["db"] = f"database tidak bisa dibaca: {f['db_error']}"
    else:
        age = f.get("last_cycle_age_min")
        if age is None:
            out["cycle_stale"] = "tidak ada siklus di cycle_log"
        elif age > MAX_CYCLE_AGE_MIN:
            out["cycle_stale"] = f"siklus terakhir {age:.0f} menit lalu (batas {MAX_CYCLE_AGE_MIN})"
        if f.get("futures_funding_zero"):
            out["futures_blind"] = "siklus futures terakhir tanpa data funding — Binance futures terputus?"
        for prov in f.get("shadow_failing") or []:
            out[f"shadow:{prov}"] = (f"agent shadow {prov}: {SHADOW_ERROR_RUN} pendapat terakhir "
                                     "gagal — key/saldo?")
    mem = f.get("mem_available_mb")
    if mem is not None and mem < MIN_MEM_AVAILABLE_MB:
        out["memory"] = f"RAM tersedia {mem:.0f} MB (batas {MIN_MEM_AVAILABLE_MB})"
    disk = f.get("disk_used_pct")
    if disk is not None and disk > MAX_DISK_USED_PCT:
        out["disk"] = f"disk terpakai {disk:.0f}% (batas {MAX_DISK_USED_PCT}%)"
    return out


# ---------------------------------------------------------------------------
# De-duplication
# ---------------------------------------------------------------------------

def step(state, active, now):
    """One transition. Returns (new, again, recovered, new_state):
      new        {key: msg}               first seen this run
      again      {key: (msg, hours_down)} still active, last sent ≥ REALERT_H ago
      recovered  {key: msg}               was active, now clear
    """
    prev = (state or {}).get("active") or {}
    nxt, new, again, rec = {}, {}, {}, {}
    for key, msg in active.items():
        if key not in prev:
            new[key] = msg
            nxt[key] = {"since": now.isoformat(), "last_sent": now.isoformat(), "msg": msg}
            continue
        entry = dict(prev[key], msg=msg)
        since = datetime.fromisoformat(entry["since"])
        if (now - datetime.fromisoformat(entry["last_sent"])).total_seconds() >= REALERT_H * 3600:
            again[key] = (msg, round((now - since).total_seconds() / 3600, 1))
            entry["last_sent"] = now.isoformat()
        nxt[key] = entry
    for key, entry in prev.items():
        if key not in active:
            hours = (now - datetime.fromisoformat(entry["since"])).total_seconds() / 3600
            rec[key] = f"{_label(key)} normal lagi (setelah {hours:.1f} jam)"
    return new, again, rec, {"active": nxt}


def _label(key):
    """What recovered, in words — the stored message describes the fault, not the fix."""
    kind, _, name = key.partition(":")
    return {"svc": f"layanan {name}", "shadow": f"agent shadow {name}", "db": "database",
            "cycle_stale": "siklus bot", "futures_blind": "data funding futures",
            "memory": "RAM", "disk": "disk"}.get(kind, key)


def render(new, again, recovered, now):
    """One short Telegram message, or None when there is nothing to say."""
    if not (new or again or recovered):
        return None
    e = lambda s: html.escape(str(s), quote=False)
    lines = []
    if new or again:
        lines.append(f"🚨 <b>ALARM bot</b> · {now:%Y-%m-%d %H:%M} UTC")
        lines += [f"• {e(m)}" for m in new.values()]
        lines += [f"• (masih, {h:g} jam) {e(m)}" for m, h in again.values()]
    if recovered:
        if not lines:
            lines.append(f"✅ <b>pulih</b> · {now:%Y-%m-%d %H:%M} UTC")
            lines += [f"• {e(m)}" for m in recovered.values()]
        else:
            lines.append("✅ <b>pulih</b>: " + "; ".join(e(m) for m in recovered.values()))
    return "\n".join(lines)


def _load_state(path):
    try:
        s = json.loads(Path(path).read_text())
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}                      # missing or corrupt: start clean


def run_alarm(facts, state_path, now, send_fn=None, save=True):
    """Decide, send on change, persist. State is written only after a successful send,
    so a Telegram outage retries next hour instead of swallowing the alert."""
    active = conditions(facts)
    new, again, rec, state = step(_load_state(state_path), active, now)
    text = render(new, again, rec, now)
    if send_fn is None:
        from notifier.common import _send_telegram_message
        send_fn = lambda t: _send_telegram_message(t, "alarm")
    sent = text is None or send_fn(text)
    if not sent:
        print("Telegram send failed", file=sys.stderr)
    if save and sent:
        p = Path(state_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1))
        tmp.replace(p)
    return 1 if active else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/signal_history.db")
    ap.add_argument("--root", default=".")
    ap.add_argument("--state", default="data/alarm_state.json")
    ap.add_argument("--dry-run", action="store_true", help="print instead of sending; no state write")
    args = ap.parse_args()
    from dotenv import load_dotenv
    load_dotenv(Path(args.root) / ".env")
    now = datetime.now(UTC)
    facts = {**collect_db_facts(args.db, now), **collect_host_facts()}
    if args.dry_run:
        print(json.dumps(facts, indent=1, default=str))
        send = lambda t: print(t) or True
    else:
        send = None
    return run_alarm(facts, args.state, now, send_fn=send, save=not args.dry_run)


if __name__ == "__main__":
    sys.exit(main())

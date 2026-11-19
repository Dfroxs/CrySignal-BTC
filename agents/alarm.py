#!/usr/bin/env python3
"""Hourly real-time alarm for the bot and its side processes. No LLM: rules only, Telegram only on change.

The daily ops report (03:30 UTC) is too slow for a dead bot. This runs every hour, ten
minutes after the bot's :01 cycle, and sends a short Telegram message the moment
something is wrong — then stays quiet while it persists (one reminder every
`REALERT_H` hours) and sends one "✅ pulih" when it clears. It reads health only, never
hypothesis statistics: no forward returns, no variant, shadow or scanner performance.
Beyond the bot it watches the scanner arms, the daily backups and the derivatives archive:
each of those fails silently, and a forward-only test cannot recover the hours it lost.

    python3 -m agents.alarm --dry-run      # print what would be sent; state untouched
    python3 -m agents.alarm                # send on change, update data/alarm_state.json

Exit code 1 while any condition is active, so cron mail / a wrapper can see it.

Cron (user crontab on the VPS):
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
SCANNER_ARMS = (("murni", "scanner.db"), ("hybrid", "scanner_hybrid.db"),
                ("acak", "scanner_random.db"))
SCANNER_ERROR_RUN = 3           # this many newest scan cycles all errored → arm down
MAX_FILE_AGE_H = 26             # daily jobs (backup 03:00, archive 03:40) plus slack
MAX_OFFSITE_AGE_H = 72          # the Mac pulls twice a day; a weekend off is not an alarm
MAX_MACRO_AGE_H = 6             # the bot rewrites the calendar every cycle; stale = macro rule blind
BACKUPS = (("db", "signal_history.db"),) + tuple((Path(f).stem, f) for _, f in SCANNER_ARMS)


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
        # Entry opinions (H-S/H-VG), and since 2026-11-19 exit and flip opinions too: the flip
        # shadow feeds H-FLP-C and could fail every time with nothing saying so.
        for table, label in (("shadow_opinions", ""), ("shadow_exit_opinions", " (exit)"),
                             ("shadow_flip_opinions", " (flip)")):
            if table not in tables:
                continue
            for (prov,) in q(f"SELECT DISTINCT provider FROM {table} ORDER BY provider"):
                errs = [e for (e,) in q(f"SELECT error FROM {table} WHERE provider=? "
                                        "ORDER BY id DESC LIMIT ?", prov, SHADOW_ERROR_RUN)]
                if len(errs) == SHADOW_ERROR_RUN and all(e is not None for e in errs):
                    f["shadow_failing"].append(prov + label)
    except sqlite3.Error as exc:
        f["db_error"] = str(exc)
    finally:
        con.close()
    return f


def collect_scanner_facts(data_dir, now):
    """Per scanner arm: minutes since its newest stored cycle, and whether its newest
    SCANNER_ERROR_RUN cycles all errored. An arm without a database is `missing`."""
    out = {}
    for arm, name in SCANNER_ARMS:
        path = Path(data_dir) / name
        if not path.exists():
            out[arm] = {"missing": True}
            continue
        f = {"missing": False, "age_min": None, "failing": False, "db_error": None}
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                rows = con.execute("SELECT timestamp, error FROM scan_cycles ORDER BY id DESC "
                                   "LIMIT ?", (SCANNER_ERROR_RUN,)).fetchall()
            finally:
                con.close()
        except sqlite3.Error as exc:
            out[arm] = dict(f, db_error=str(exc))
            continue
        if rows:
            last = datetime.fromisoformat(rows[0][0])
            last = last if last.tzinfo else last.replace(tzinfo=UTC)
            f["age_min"] = round((now - last).total_seconds() / 60, 1)
            f["failing"] = len(rows) == SCANNER_ERROR_RUN and all(e for _, e in rows)
        out[arm] = f
    return out


def collect_file_facts(data_dir, now):
    """Hours since the newest backup of each database that exists, and since the
    derivatives archive last wrote. None = no such file at all."""
    data = Path(data_dir)

    def age_h(paths):
        ts = [p.stat().st_mtime for p in paths]
        return round((now.timestamp() - max(ts)) / 3600, 1) if ts else None
    backups = {prefix: age_h((data / "backups").glob(f"{prefix}-*.db"))
               for prefix, src in BACKUPS if (data / src).exists()}
    derivs = data / "derivs"
    stamp = data / "backups" / ".offsite_ok"      # written by the Mac after a verified pull
    return {"backup_age_h": backups,
            "derivs_age_h": age_h(derivs.glob("*.csv")) if derivs.is_dir() else None,
            "offsite_age_h": age_h([stamp] if stamp.exists() else []),
            "news_age_h": age_h((data / "news").glob("*.csv")) if (data / "news").is_dir() else None,
            # Daily jobs that otherwise fail in silence (2026-11-19 audit):
            "report_age_h": age_h([p for p in [data / "ops_report.ok"] if p.exists()]),
            "scoring_age_h": age_h([p for p in [data / "scoring" / "scoring_day.log"] if p.exists()]),
            "macro_age_h": age_h([p for p in [data / "macro_events.csv"] if p.exists()])}


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
            "mem_available_mb": mem, "disk_used_pct": disk}


# ---------------------------------------------------------------------------
# Rules — the only place a condition is decided. Keys are stable across runs so the
# state file can tell a new condition from one that persists.
# ---------------------------------------------------------------------------

def conditions(f):
    out = {}
    if f.get("spot_svc") != "active":           # nakhoda-alloc was removed 2026-11-11
        out["svc:spotsignal"] = f"layanan spotsignal: {f.get('spot_svc')}"
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
    for arm, a in (f.get("scanner") or {}).items():
        if a.get("missing"):
            if arm == "murni":                  # the other arms may not be deployed yet
                out[f"scanner:{arm}"] = "scanner murni: database tidak ada"
        elif a.get("db_error"):
            out[f"scanner:{arm}"] = f"scanner {arm}: database tidak bisa dibaca: {a['db_error']}"
        elif a.get("age_min") is None:
            out[f"scanner:{arm}"] = f"scanner {arm}: belum ada siklus"
        elif a["age_min"] > MAX_CYCLE_AGE_MIN:
            out[f"scanner:{arm}"] = (f"scanner {arm} macet: siklus terakhir {a['age_min']:.0f} menit lalu "
                                     f"(batas {MAX_CYCLE_AGE_MIN})")
        if a.get("failing"):
            out[f"scanner_err:{arm}"] = (f"scanner {arm}: {SCANNER_ERROR_RUN} siklus terakhir error "
                                         "— API key/saldo/Binance?")
    for prefix, age in (f.get("backup_age_h") or {}).items():
        if age is None or age > MAX_FILE_AGE_H:
            when = "tidak ada" if age is None else f"terakhir {age:.0f} jam lalu"
            out[f"backup:{prefix}"] = f"backup {prefix}: {when} (batas {MAX_FILE_AGE_H} jam)"
    if "derivs_age_h" in f:
        age = f["derivs_age_h"]
        if age is None or age > MAX_FILE_AGE_H:
            when = "tidak ada" if age is None else f"terakhir menulis {age:.0f} jam lalu"
            out["derivs"] = f"arsip derivatives: {when} (batas {MAX_FILE_AGE_H} jam)"
    if "news_age_h" in f:
        age = f["news_age_h"]
        if age is None or age > MAX_FILE_AGE_H:
            when = "tidak ada" if age is None else f"tidak bertambah {age:.0f} jam (feed berita mati?)"
            out["news"] = f"arsip berita: {when} (batas {MAX_FILE_AGE_H} jam)"
    for key, limit, what, why in (
            ("report_age_h", MAX_FILE_AGE_H, "laporan harian", "tidak terkirim"),
            ("scoring_age_h", MAX_FILE_AGE_H, "penilaian otomatis", "tidak jalan"),
            ("macro_age_h", MAX_MACRO_AGE_H, "kalender ekonomi",
             "tidak diperbarui, proteksi berita makro mati")):
        if key in f:
            age = f[key]
            if age is None or age > limit:
                when = "belum pernah" if age is None else f"{age:.0f} jam"
                out[key.removesuffix("_age_h")] = f"{what} {why} ({when}; batas {limit} jam)"
    if "offsite_age_h" in f:
        age = f["offsite_age_h"]
        if age is None or age > MAX_OFFSITE_AGE_H:
            when = "belum pernah jalan" if age is None else f"terakhir {age:.0f} jam lalu (Mac mati?)"
            out["offsite"] = f"backup ke Mac {when} (batas {MAX_OFFSITE_AGE_H} jam)"
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
            "memory": "RAM", "disk": "disk", "scanner": f"scanner {name}",
            "scanner_err": f"scanner {name}", "backup": f"backup {name}",
            "derivs": "arsip derivatives", "offsite": "backup ke Mac", "news": "arsip berita",
            "report": "laporan harian", "scoring": "penilaian otomatis",
            "macro": "kalender ekonomi"}.get(kind, key)


def render(new, again, recovered, now):
    """One short Telegram message, or None when there is nothing to say. Plain words and a
    pointer to where to look (owner-approved format, 2026-11-18)."""
    if not (new or again or recovered):
        return None
    e = lambda s: html.escape(str(s), quote=False)
    lines = []
    if new or again:
        lines.append(f"🚨 <b>Ada masalah</b> · {now:%H:%M} UTC")
        lines += [f"• {e(m)}" for m in new.values()]
        lines += [f"• {e(m)} (sudah {h:g} jam)" for m, h in again.values()]
        if recovered:
            lines.append("✅ Sudah normal lagi: " + "; ".join(e(m) for m in recovered.values()))
        lines.append("Cek dashboard, tab Sistem.")
    else:
        lines.append(f"✅ <b>Sudah normal lagi</b> · {now:%H:%M} UTC")
        lines += [f"• {e(m)}" for m in recovered.values()]
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
    data = Path(args.root) / "data"
    facts = {**collect_db_facts(args.db, now), **collect_host_facts(),
             "scanner": collect_scanner_facts(data, now), **collect_file_facts(data, now)}
    if args.dry_run:
        print(json.dumps(facts, indent=1, default=str))
        send = lambda t: print(t) or True
    else:
        send = None
    code = run_alarm(facts, args.state, now, send_fn=send, save=not args.dry_run)
    if not args.dry_run:                  # the Mac's watchdog reads this: is the alarm itself alive?
        (Path(args.root) / "data" / "alarm.ok").write_text(now.isoformat())
    return code


if __name__ == "__main__":
    sys.exit(main())

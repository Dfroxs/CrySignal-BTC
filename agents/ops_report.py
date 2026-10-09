#!/usr/bin/env python3
"""Daily operations report for both bots, sent to Telegram. Runs ON the VPS from cron.

`scripts/morning_check.sh`, automated: the same checks, plus run-3 ones (variants logged,
positions actually opening, which gate blocks most). Run on the host itself, so it needs
no SSH and survives the laptop being off.

Division of labour, on purpose:
  code   collects the facts and DECIDES what is an anomaly (`anomalies`)
  LLM    only writes a short Indonesian summary of those facts
The model never decides whether something is wrong, and its failure never costs the
report: without it the deterministic report goes out with a note. The database is
opened read-only, no news/RSS text enters the prompt, and model output is HTML-escaped
before Telegram renders it.

    python3 -m agents.ops_report --dry-run          # print, send nothing
    python3 -m agents.ops_report --no-llm           # deterministic report only
    python3 -m agents.ops_report                    # LLM summary + send

Cron (user crontab on the VPS, after the 03:00 backup):
    30 3 * * * cd ~/playground/CrySignal-BTC && XDG_RUNTIME_DIR=/run/user/$(id -u) \
               ./venv/bin/python -m agents.ops_report >> data/ops_report.log 2>&1
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TELEGRAM_LIMIT = 4000
MAX_CYCLE_AGE_H = 2.0
MAX_VARIANT_NULL = 0.05


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------

def _ts(col):
    """A timestamp column normalised for comparison: rows hold both 'YYYY-MM-DD HH:MM:SS'
    and ISO 'YYYY-MM-DDTHH:MM:SS+00:00', which do not compare as strings."""
    return f"datetime(substr({col}, 1, 19))"


def collect_db_facts(db_path, now):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    q = lambda sql, *p: con.execute(sql, p).fetchall()
    d24 = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    d7 = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    since = f"{_ts('timestamp')} >= datetime(?)"
    f = {"cycles_24h": dict(q(f"SELECT mode, COUNT(*) FROM cycle_log WHERE {since} GROUP BY mode", d24)),
         "fired_24h": dict(q(f"SELECT mode, COUNT(*) FROM cycle_log WHERE {since} AND type != 'HOLD' "
                             "GROUP BY mode", d24)),
         "futures_blind_24h": q(f"SELECT COUNT(*) FROM cycle_log WHERE {since} AND mode='futures' "
                                "AND funding_rate = 0", d24)[0][0]}
    last = q(f"SELECT MAX({_ts('timestamp')}) FROM cycle_log")[0][0]
    f["last_cycle"] = last
    f["last_cycle_age_h"] = (round((now.replace(tzinfo=None) - datetime.fromisoformat(last))
                                   .total_seconds() / 3600, 2) if last else None)
    cols = {r[1] for r in q("PRAGMA table_info(cycle_log)")}
    if "variants" in cols:
        n, nulls = q(f"SELECT COUNT(*), SUM(variants IS NULL) FROM cycle_log WHERE {since}", d24)[0]
        f["variants_null_share_24h"] = (nulls or 0) / n if n else None
    else:
        f["variants_null_share_24h"] = None
    opened = f"{_ts('opened_at')} >= datetime(?)"
    f["opened_24h"] = q(f"SELECT COUNT(*) FROM paper_positions WHERE {opened}", d24)[0][0]
    f["opened_7d"] = q(f"SELECT COUNT(*) FROM paper_positions WHERE {opened}", d7)[0][0]
    f["open_positions"] = q("SELECT COUNT(*) FROM paper_positions WHERE closed_at IS NULL")[0][0]
    f["closed_24h"] = [{"mode": m, "outcome": o, "pnl_pct": p} for m, o, p in q(
        f"SELECT mode, outcome, pnl_pct FROM paper_positions WHERE closed_at IS NOT NULL "
        f"AND {_ts('closed_at')} >= datetime(?) ORDER BY closed_at", d24)]
    f["blocks_24h"] = dict(q(f"SELECT gate, COUNT(*) FROM signal_blocks WHERE {since} "
                             "GROUP BY gate ORDER BY COUNT(*) DESC", d24))
    f["thresholds"] = dict(q("SELECT mode, threshold FROM cycle_log c WHERE id = "
                             "(SELECT MAX(id) FROM cycle_log WHERE mode = c.mode)"))
    con.close()
    return f


def _run(cmd, timeout=20):
    try:
        out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip()
    except Exception:
        return ""


def collect_host_facts(root, now, run=_run):
    """Services, logs and backups. Anything that cannot be read comes back 'unknown' /
    None, which the rules treat as something to look at, never as fine."""
    root = Path(root)
    j = "journalctl --user -u nakhoda-alloc --no-pager -S '24 hours ago' 2>/dev/null"
    num = lambda s: int(s) if s.strip().isdigit() else None
    log = root / "paper_run.log"
    errs = None
    if log.exists():
        tail = log.read_text(errors="replace").splitlines()[-400:]
        errs = sum(bool(re.search(r"error|traceback|exception", ln, re.I)) for ln in tail)
    backups = sorted((root / "data" / "backups").glob("db-*.db"))
    return {"spot_svc": run("systemctl is-active spotsignal") or "unknown",
            "alloc_svc": run("systemctl --user is-active nakhoda-alloc") or "unknown",
            "spot_log_errors": errs,
            "alloc_errors_24h": num(run(f"{j} | grep -ciE 'error|traceback|consecutive'")),
            "alloc_decisions_24h": num(run(f"{j} | grep -cE 'Rebalance|already at target'")),
            "backup_latest": backups[-1].name if backups else None,
            "today": now.strftime("%Y%m%d")}


# ---------------------------------------------------------------------------
# Rules — the only place an anomaly is decided
# ---------------------------------------------------------------------------

def anomalies(f):
    out = []
    for svc, key in (("spotsignal", "spot_svc"), ("nakhoda-alloc", "alloc_svc")):
        if f.get(key) != "active":
            out.append(f"layanan {svc}: {f.get(key)}")
    if not sum((f.get("cycles_24h") or {}).values()):
        out.append("tidak ada siklus dalam 24 jam — bot diam")
    age = f.get("last_cycle_age_h")
    if age is None or age > MAX_CYCLE_AGE_H:
        out.append(f"siklus terakhir {age} jam lalu (batas {MAX_CYCLE_AGE_H})")
    if f.get("futures_blind_24h"):
        out.append(f"{f['futures_blind_24h']} siklus futures tanpa data funding — Binance terdegradasi?")
    share = f.get("variants_null_share_24h")
    if share is not None and share > MAX_VARIANT_NULL:
        out.append(f"varian kosong di {share:.0%} siklus (batas {MAX_VARIANT_NULL:.0%})")
    if f.get("spot_log_errors"):
        out.append(f"{f['spot_log_errors']} baris error di paper_run.log")
    if f.get("alloc_errors_24h"):
        out.append(f"{f['alloc_errors_24h']} error allocbot dalam 24 jam")
    if f.get("alloc_decisions_24h") == 0:
        out.append("allocbot tidak membuat keputusan harian")
    if f.get("backup_latest") != f"db-{f.get('today')}.db":
        out.append(f"backup hari ini tidak ada (terbaru: {f.get('backup_latest')})")
    if f.get("opened_7d") == 0:
        out.append("tidak ada posisi baru dalam 7 hari — data trade tidak bertambah")
    return out


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

SYSTEM = ("Kamu asisten operasional untuk bot paper-trading BTC. Tulis ringkasan harian "
          "maksimal 120 kata dalam bahasa Indonesia untuk pemiliknya. Gunakan HANYA fakta "
          "dalam JSON; jangan mengarang angka dan jangan memberi saran membeli atau menjual. "
          "Jika ada anomali, sebut itu lebih dulu. Lalu hal yang paling penting: posisi yang "
          "dibuka atau ditutup, gerbang yang paling banyak memblokir, dan apakah data trade "
          "bertambah. Teks biasa, tanpa markdown.")


def build_prompt(f, anoms):
    return ("Fakta 24 jam terakhir (JSON):\n" + json.dumps(f, indent=1, default=str) +
            "\n\nAnomali yang sudah diputuskan oleh aturan (jangan tambah atau kurangi):\n" +
            json.dumps(anoms, ensure_ascii=False))


def render(f, anoms, summary=None, source=None):
    e = html.escape
    lines = [f"<b>📋 Laporan harian bot</b> · {e(str(f.get('today')))}"]
    if anoms:
        lines.append(f"\n⚠️ <b>{len(anoms)} hal perlu diperiksa</b>")
        lines += [f"• {e(a)}" for a in anoms]
    else:
        lines.append("\n✅ semua bersih")
    cyc = f.get("cycles_24h") or {}
    lines.append(f"\nsiklus 24j: futures {cyc.get('futures', 0)} · spot {cyc.get('spot', 0)}")
    lines.append(f"posisi baru 24j / 7h: {f.get('opened_24h')} / {f.get('opened_7d')} · "
                 f"terbuka: {f.get('open_positions')}")
    for c in f.get("closed_24h") or []:
        lines.append(f"ditutup: {e(str(c['mode']))} {e(str(c['outcome']))} {c['pnl_pct']:+.2f}%")
    top = list((f.get("blocks_24h") or {}).items())[:3]
    if top:
        lines.append("gerbang teratas: " + ", ".join(f"{e(g)} {n}" for g, n in top))
    if f.get("thresholds"):
        lines.append("threshold: " + ", ".join(f"{e(m)} {t}" for m, t in f["thresholds"].items()))
    if summary:
        lines.append(f"\n<b>Ringkasan AI</b> <i>({e(source or '')})</i>\n{e(summary)}")
    else:
        lines.append("\n<i>ringkasan AI tidak tersedia</i>")
    text = "\n".join(lines)
    return text if len(text) <= TELEGRAM_LIMIT else text[:TELEGRAM_LIMIT - 1] + "…"


def run_report(facts, ask_fn=None, send_fn=None, provider=None, use_llm=True):
    """Render and send. Returns 1 when any anomaly fired, so cron can surface it."""
    from agents.llm import LLMError, ask
    ask_fn = ask_fn or ask
    anoms = anomalies(facts)
    summary = source = None
    if use_llm:
        try:
            reply = ask_fn(build_prompt(facts, anoms), system=SYSTEM, provider=provider)
            summary, source = reply.text, f"{reply.provider}/{reply.model}"
        except LLMError as exc:
            print(f"LLM unavailable: {exc}", file=sys.stderr)
    text = render(facts, anoms, summary, source)
    if send_fn is None:
        from notifier.common import _send_telegram_message
        send_fn = lambda t: _send_telegram_message(t, "ops-report")
    if not send_fn(text):
        print("Telegram send failed", file=sys.stderr)
    return 1 if anoms else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/signal_history.db")
    ap.add_argument("--root", default=".")
    ap.add_argument("--provider", default=None, help="anthropic | deepseek (default: LLM_PROVIDER)")
    ap.add_argument("--dry-run", action="store_true", help="print instead of sending")
    ap.add_argument("--no-llm", action="store_true")
    args = ap.parse_args()
    from dotenv import load_dotenv
    load_dotenv(Path(args.root) / ".env")
    now = datetime.now(UTC)
    facts = {**collect_db_facts(args.db, now), **collect_host_facts(args.root, now)}
    send = (lambda t: print(t) or True) if args.dry_run else None
    return run_report(facts, send_fn=send, provider=args.provider, use_llm=not args.no_llm)


if __name__ == "__main__":
    sys.exit(main())

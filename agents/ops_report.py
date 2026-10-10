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

Health and spend only: positions opened/closed, signals fired, whether the score
variants still differ from base, shadow agents' calls/errors/latency, and LLM tokens and
estimated USD (24h, month to date, projection) against LLM_MONTHLY_BUDGET_USD. The
report's own summary call is logged to data/llm_usage.jsonl and counted from then on.

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
import os
import re
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TELEGRAM_LIMIT = 4000
MAX_CYCLE_AGE_H = 2.0
MAX_VARIANT_NULL = 0.05
BACKUP_DUE_UTC = (3, 15)         # cron backs up at 03:00 UTC; before this, yesterday's is newest


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------

def _ts(col):
    """A timestamp column normalised for comparison: rows hold both 'YYYY-MM-DD HH:MM:SS'
    and ISO 'YYYY-MM-DDTHH:MM:SS+00:00', which do not compare as strings."""
    return f"datetime(substr({col}, 1, 19))"


# What this report may and may not compute. Run 3's pre-registration
# (docs/superpowers/specs/2026-10-09-run3-prereg.md) forbids computing ANY hypothesis
# figure before day 30 (2026-11-08): no IC of a field against forward returns, no
# variant P&L or variant book, no shadow AGREE/DISAGREE against outcomes, no BTC vs
# random. Everything here is a count, a latency, an error rate or a cost: health, never
# performance. A position's own P&L is the bot's ledger, not a hypothesis figure.
# Do not add a fact that relates a variant, a verdict or a field to an outcome.

def collect_db_facts(db_path, now, run_start=None):
    """Facts for the last 24h. Run-scoped checks (variants, positions) start at the
    later of 24h ago and `run_start`: the previous run's rows are not this run's faults."""
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
    run_from = max(d24, run_start.strftime("%Y-%m-%d %H:%M:%S")) if run_start else d24
    if "variants" in cols:
        n, nulls = q(f"SELECT COUNT(*), SUM(variants IS NULL) FROM cycle_log WHERE {since}",
                     run_from)[0]
        f["variants_null_share_24h"] = (nulls or 0) / n if n else None
        f["variant_diff_24h"] = _variant_diff(q(
            f"SELECT mode, variants FROM cycle_log WHERE {since} AND variants IS NOT NULL",
            run_from))
    else:
        f["variants_null_share_24h"] = None
        f["variant_diff_24h"] = {}
    opened = f"{_ts('opened_at')} >= datetime(?)"
    f["opened_24h"] = q(f"SELECT COUNT(*) FROM paper_positions WHERE {opened}", d24)[0][0]
    f["opened_7d"] = q(f"SELECT COUNT(*) FROM paper_positions WHERE {opened}", d7)[0][0]
    f["run_age_h"] = (round((now - run_start).total_seconds() / 3600, 2) if run_start else None)
    f["opened_since"] = (q(f"SELECT COUNT(*) FROM paper_positions WHERE {opened}",
                           run_start.strftime("%Y-%m-%d %H:%M:%S"))[0][0]
                         if run_start else f["opened_7d"])
    f["open_positions"] = q("SELECT COUNT(*) FROM paper_positions WHERE closed_at IS NULL")[0][0]
    f["opened_list_24h"] = [{"mode": m, "side": t, "entry": e} for m, t, e in q(
        f"SELECT mode, type, entry_price FROM paper_positions WHERE {opened} "
        "ORDER BY opened_at", d24)]
    f["closed_24h"] = [{"mode": m, "side": t, "entry": e, "outcome": o, "pnl_pct": p}
                       for m, t, e, o, p in q(
        f"SELECT mode, type, entry_price, outcome, pnl_pct FROM paper_positions "
        f"WHERE closed_at IS NOT NULL AND {_ts('closed_at')} >= datetime(?) ORDER BY closed_at",
        d24)]
    f["blocks_24h"] = dict(q(f"SELECT gate, COUNT(*) FROM signal_blocks WHERE {since} "
                             "GROUP BY gate ORDER BY COUNT(*) DESC", d24))
    f["thresholds"] = dict(q("SELECT mode, threshold FROM cycle_log c WHERE id = "
                             "(SELECT MAX(id) FROM cycle_log WHERE mode = c.mode)"))
    tables = {r[0] for r in q("SELECT name FROM sqlite_master WHERE type='table'")}
    f["shadow_24h"] = ({p: [n, e or 0] for p, n, e in q(
        f"SELECT provider, COUNT(*), SUM(error IS NOT NULL) FROM shadow_opinions "
        f"WHERE {since} GROUP BY provider ORDER BY provider", d24)}
        if "shadow_opinions" in tables else {})
    # Median over ANSWERED calls: a timeout's latency is the cap, not the model's speed.
    lat = {}
    if "shadow_opinions" in tables:
        for p, ms in q(f"SELECT provider, latency_ms FROM shadow_opinions WHERE {since} "
                       "AND error IS NULL AND latency_ms IS NOT NULL", d24):
            lat.setdefault(p, []).append(ms)
    f["shadow_latency_ms_24h"] = {p: int(round(median(v))) for p, v in sorted(lat.items())}
    con.close()
    return f


def _variant_diff(rows):
    """Per mode: cycles with variants, cycles where each variant's `type` differs from
    base's, and cycles whose JSON is unreadable or lacks base or a variant (a variant
    that raises is skipped by score_variants). A liveness check, nothing more."""
    from signals.variants import VARIANTS
    out = {}
    for mode, raw in rows:
        d = out.setdefault(mode, {"cycles": 0, **{n: 0 for n in VARIANTS}, "broken": 0})
        d["cycles"] += 1
        try:
            v = json.loads(raw)
            base = v["base"]["type"]
            types = {n: v[n]["type"] for n in VARIANTS}
        except (ValueError, TypeError, KeyError):
            d["broken"] += 1
            continue
        for n, t in types.items():
            d[n] += t != base
    return out


# ---------------------------------------------------------------------------
# LLM spend — every model call the agents make, priced from agents/llm.PRICES_USD_PER_MTOK
# ---------------------------------------------------------------------------

def _parse_ts(raw):
    try:
        t = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _usage_rows(db_path, usage_path):
    """(timestamp, provider, model, input_tokens, output_tokens) from shadow_opinions and
    from the report's own log. A row without tokens (a timeout, a missing key) is skipped:
    what it may have cost is not knowable from here."""
    rows = []
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                       "AND name='shadow_opinions'").fetchone():
            rows += con.execute("SELECT timestamp, provider, model, input_tokens, output_tokens "
                                "FROM shadow_opinions WHERE COALESCE(input_tokens, 0) + "
                                "COALESCE(output_tokens, 0) > 0").fetchall()
    finally:
        con.close()
    path = Path(usage_path) if usage_path else None
    if path and path.exists():
        for line in path.read_text(errors="replace").splitlines():
            try:
                r = json.loads(line)
                rows.append((r["timestamp"], r["provider"], r.get("model"),
                             int(r.get("input_tokens") or 0), int(r.get("output_tokens") or 0)))
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
    return rows


def collect_cost_facts(db_path, usage_path, now):
    """Tokens and estimated USD per provider, last 24h and month to date, plus a linear
    projection to month end: spend so far + the last 24h's rate for the days left.
    (Projecting from the month's average would understate a run that started mid-month
    and explode on the 1st, when the report runs three hours into the month.)"""
    from agents.llm import cost_usd
    month0 = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month1 = (month0 + timedelta(days=32)).replace(day=1)
    d24 = now - timedelta(hours=24)
    win = {"24h": {}, "mtd": {}}
    unpriced = set()
    for ts, prov, model, i, o in _usage_rows(db_path, usage_path):
        t = _parse_ts(ts)
        if t is None or t > now:
            continue
        usd = cost_usd(model, i, o)
        if usd is None:
            unpriced.add(str(model))
        for key, start in (("24h", d24), ("mtd", month0)):
            if t < start:
                continue
            d = win[key].setdefault(prov, {"input_tokens": 0, "output_tokens": 0, "usd": 0.0,
                                           "unpriced": []})
            d["input_tokens"] += i or 0
            d["output_tokens"] += o or 0
            if usd is None:
                if str(model) not in d["unpriced"]:
                    d["unpriced"].append(str(model))
            else:
                d["usd"] += usd
    total = lambda w: round(sum(d["usd"] for d in w.values()), 6)
    usd24, mtd = total(win["24h"]), total(win["mtd"])
    days_left = (month1 - now).total_seconds() / 86400
    return {"llm_cost_24h": win["24h"], "llm_cost_mtd": win["mtd"],
            "llm_usd_24h": usd24, "llm_usd_mtd": mtd,
            "llm_usd_projected_month": round(mtd + usd24 * days_left, 6),
            "llm_unpriced": sorted(unpriced)}


def budget_from_env():
    """LLM_MONTHLY_BUDGET_USD as a float, or None (unset, or not a positive number)."""
    raw = os.getenv("LLM_MONTHLY_BUDGET_USD", "").strip()
    try:
        v = float(raw)
    except ValueError:
        if raw:
            print(f"LLM_MONTHLY_BUDGET_USD={raw!r} is not a number; budget check off",
                  file=sys.stderr)
        return None
    return v if v > 0 else None


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
    # Newest by TIME among db-YYYYMMDD.db: by name, an old 'db-26-08-30-0503.db' sorts last.
    backups = sorted((p for p in (root / "data" / "backups").glob("db-*.db")
                      if re.fullmatch(r"db-\d{8}\.db", p.name)), key=lambda p: p.stat().st_mtime)
    return {"spot_svc": run("systemctl is-active spotsignal") or "unknown",
            "alloc_svc": run("systemctl --user is-active nakhoda-alloc") or "unknown",
            "spot_log_errors": errs,
            "alloc_errors_24h": num(run(f"{j} | grep -ciE 'error|traceback|consecutive'")),
            "alloc_decisions_24h": num(run(f"{j} | grep -cE 'Rebalance|already at target'")),
            "backup_latest": backups[-1].name if backups else None,
            "today": now.strftime("%Y%m%d"),
            "backup_due": _backup_due(now)}


def _backup_due(now):
    """The date of the newest backup that should exist by now. Between 00:00 and the
    03:00 cron, today's has not been written yet; demanding it then raised a false
    'backup hari ini tidak ada' on every Q&A question asked after midnight UTC."""
    day = now if (now.hour, now.minute) >= BACKUP_DUE_UTC else now - timedelta(days=1)
    return day.strftime("%Y%m%d")


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
    due = f.get("backup_due") or f.get("today")
    if (f.get("backup_latest") or "") < f"db-{due}.db":     # YYYYMMDD sorts as a date
        out.append(f"backup {due} tidak ada (terbaru: {f.get('backup_latest')})")
    for mode, d in (f.get("variant_diff_24h") or {}).items():
        if d.get("broken"):
            out.append(f"varian rusak/hilang di {d['broken']} dari {d['cycles']} siklus {mode}")
    for prov, (n, errs) in (f.get("shadow_24h") or {}).items():
        if n and errs == n:
            out.append(f"agent shadow {prov} gagal di semua {n} panggilan — key/saldo?")
    if f.get("llm_unpriced"):
        out.append("harga model tidak dikenal, biaya LLM tidak lengkap: "
                   + ", ".join(f["llm_unpriced"]))
    budget = f.get("llm_budget_usd")
    if budget:
        mtd, proj = f.get("llm_usd_mtd") or 0.0, f.get("llm_usd_projected_month") or 0.0
        if mtd > budget:
            out.append(f"biaya LLM bulan ini ${mtd:.2f} melewati anggaran ${budget:.2f}")
        elif proj > budget:
            out.append(f"proyeksi biaya LLM bulan ini ${proj:.2f} > anggaran ${budget:.2f}")
    age = f.get("run_age_h")
    if age is None:
        if f.get("opened_7d") == 0:
            out.append("tidak ada posisi baru dalam 7 hari — data trade tidak bertambah")
    elif age >= 48 and f.get("opened_since") == 0:
        out.append(f"tidak ada posisi baru sejak run mulai ({age / 24:.1f} hari) — "
                   "data trade tidak bertambah")
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


MAX_POSITION_LINES = 5


def _money(v):
    return f"{v:,.0f}" if isinstance(v, (int, float)) else "?"


def _pct(v):
    return f"{v:+.2f}%" if isinstance(v, (int, float)) else "?"


def _tok(n):
    return f"{n / 1e6:.2f}M" if n >= 1e6 else f"{n / 1e3:.1f}k" if n >= 1e3 else str(n)


def _capped(lines, cap=MAX_POSITION_LINES):
    return lines if len(lines) <= cap else lines[:cap] + [f"… +{len(lines) - cap} lagi"]


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
    fired = f.get("fired_24h") or {}
    lines.append(f"sinyal 24j: futures {fired.get('futures', 0)} · spot {fired.get('spot', 0)}")
    lines.append(f"posisi baru 24j / 7h: {f.get('opened_24h')} / {f.get('opened_7d')} · "
                 f"terbuka: {f.get('open_positions')}")
    pos = lambda p: f"{e(str(p['mode']))} {e(str(p['side']))} @ {_money(p.get('entry'))}"
    lines += _capped([f"dibuka: {pos(p)}" for p in f.get("opened_list_24h") or []])
    lines += _capped([f"ditutup: {pos(c)} {e(str(c['outcome']))} {_pct(c.get('pnl_pct'))}"
                      for c in f.get("closed_24h") or []])
    for mode, d in sorted((f.get("variant_diff_24h") or {}).items()):
        lines.append(f"varian ≠ base 24j {e(mode)}: engine {d.get('rel_engine_dir', 0)} · "
                     f"ic {d.get('rel_ic_dir', 0)} /{d.get('cycles', 0)} siklus")
    top = list((f.get("blocks_24h") or {}).items())[:3]
    if top:
        lines.append("gerbang teratas: " + ", ".join(f"{e(g)} {n}" for g, n in top))
    lat = f.get("shadow_latency_ms_24h") or {}
    for prov, (n, errs) in (f.get("shadow_24h") or {}).items():
        ms = lat.get(prov)
        med = f", median {ms / 1000:.1f} dtk" if ms is not None else ""
        lines.append(f"shadow {e(prov)}: {n} pendapat, {errs} gagal{med}")
    cost = f.get("llm_cost_24h") or {}
    if cost or f.get("llm_usd_mtd"):
        per = " · ".join(f"{e(p)} {_tok(d['input_tokens'] + d['output_tokens'])} tok "
                         f"${d['usd']:.2f}{'+?' if d.get('unpriced') else ''}"
                         for p, d in sorted(cost.items()))
        lines.append(f"biaya LLM 24j: {per or '$0.00'}")
        budget = f.get("llm_budget_usd")
        lines.append(f"biaya LLM bulan ini: ${f.get('llm_usd_mtd') or 0:.2f} · proyeksi "
                     f"${f.get('llm_usd_projected_month') or 0:.2f}"
                     + (f" / anggaran ${budget:.2f}" if budget else ""))
    if f.get("thresholds"):
        lines.append("threshold: " + ", ".join(f"{e(m)} {t}" for m, t in f["thresholds"].items()))
    if summary:
        lines.append(f"\n<b>Ringkasan AI</b> <i>({e(source or '')})</i>\n{e(summary)}")
    else:
        lines.append("\n<i>ringkasan AI tidak tersedia</i>")
    text = "\n".join(lines)
    return text if len(text) <= TELEGRAM_LIMIT else text[:TELEGRAM_LIMIT - 1] + "…"


def record_usage(path, reply, purpose, now=None):
    """Append one JSON line per model call. Never raises: losing a cost line must not
    lose the report."""
    rec = {"timestamp": (now or datetime.now(UTC)).isoformat(), "purpose": purpose,
           "provider": reply.provider, "model": reply.model,
           "input_tokens": reply.input_tokens, "output_tokens": reply.output_tokens}
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except OSError as exc:
        print(f"LLM usage not recorded: {exc}", file=sys.stderr)


def run_report(facts, ask_fn=None, send_fn=None, provider=None, use_llm=True, usage_path=None):
    """Render and send. Returns 1 when any anomaly fired, so cron can surface it.

    With `usage_path`, the summary call's tokens are appended there; the cost totals
    in this report were read before the call, so it counts from the next report on."""
    from agents.llm import LLMError, ask
    ask_fn = ask_fn or ask
    anoms = anomalies(facts)
    summary = source = None
    if use_llm:
        try:
            reply = ask_fn(build_prompt(facts, anoms), system=SYSTEM, provider=provider)
            summary, source = reply.text, f"{reply.provider}/{reply.model}"
            if usage_path:
                record_usage(usage_path, reply, "ops_report")
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
    run_start = None
    try:
        m = json.loads((Path(args.root) / "data" / "paper_run_manifest.json").read_text())
        run_start = datetime.fromisoformat(m["started_at"])
    except Exception:
        pass                          # no manifest: plain 24h / 7-day windows
    usage = Path(args.root) / "data" / "llm_usage.jsonl"
    facts = {**collect_db_facts(args.db, now, run_start), **collect_host_facts(args.root, now),
             **collect_cost_facts(args.db, usage, now), "llm_budget_usd": budget_from_env()}
    send = (lambda t: print(t) or True) if args.dry_run else None
    return run_report(facts, send_fn=send, provider=args.provider, use_llm=not args.no_llm,
                      usage_path=usage)


if __name__ == "__main__":
    sys.exit(main())

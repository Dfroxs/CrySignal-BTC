"""Read-only web dashboard for the VPS: health, the bot's distance to a signal, positions,
book statistics, market context, macro agenda, gates, Claude and cost on one page.

Binds to 127.0.0.1 only, so nothing is reachable from the internet. Open it through an SSH
tunnel from the owner's machine:

    ssh -N -L 8080:localhost:8080 dmonk@<vps>       # then browse http://localhost:8080

Every database is opened `mode=ro`; the page writes nothing, sends nothing, asks no model
and imports nothing from the trading path, so running it changes no paper-run window.
Standard library only (http.server), sized for the 1 GB host. The page reloads itself
every REFRESH_S seconds.

Colour encodes direction everywhere: BUY/LONG blue, SELL/SHORT orange (validated for
colour-vision deficiency on both surfaces). Good/bad P&L use the status pair, always with
a sign. Modes are separate small charts, never two colours on one axis.

USER unit (no sudo), as spotsignal-qa:
    cp deploy/spotsignal-dash.service ~/.config/systemd/user/
    systemctl --user daemon-reload && systemctl --user enable --now spotsignal-dash

    python3 -m agents.dashboard --db data/snap.db --port 8080     # locally, on a pulled copy
    python3 -m agents.dashboard --db data/snap.db --once > page.html
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFRESH_S = 300
CLOSED_ROWS = 12
OPINION_ROWS = 5
MAX_CYCLE_AGE_MIN = 75
MIN_MEM_MB = 150
MAX_DISK_PCT = 85
MAX_BACKUP_AGE_H = 36
MACRO_CLOSE_H = 2                    # signals/sentiment.check_upcoming_macro_events
e = html.escape


def _ts(col):
    """The DB holds both 'YYYY-MM-DD HH:MM:SS' and ISO with offset; compare on the first 19."""
    return f"datetime(substr({col}, 1, 19))"


def _dt(raw):
    """Any stored timestamp as an aware UTC datetime, or None. Naive values are UTC."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw)[:19].replace(" ", "T")).replace(tzinfo=UTC)
    except ValueError:
        return None


def _ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)


def _tables(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


# ── Facts ───────────────────────────────────────────────────────────────────────────

def host_facts(root, now, run=None):
    from agents.alarm import _run, collect_host_facts
    run = run or _run
    h = collect_host_facts(run=run)
    svc = {"bot": h["spot_svc"],
           "Q&A": run("systemctl --user is-active spotsignal-qa") or "unknown"}
    since = run("systemctl show spotsignal -p ActiveEnterTimestamp --value")
    m = re.search(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", since or "")
    backups = sorted((root / "data" / "backups").glob("db-*.db"), key=lambda p: p.stat().st_mtime)
    b_age = ((now.timestamp() - backups[-1].stat().st_mtime) / 3600) if backups else None
    log = root / "paper_run.log"
    log_errors = None
    if log.exists():
        tail = log.read_text(errors="replace").splitlines()[-400:]
        log_errors = sum(bool(re.search(r"error|traceback|exception", ln, re.I)) for ln in tail)
    head = run(f"git -C '{root}' rev-parse --short HEAD")
    return {"services": svc, "mem_available_mb": h["mem_available_mb"],
            "disk_used_pct": h["disk_used_pct"], "bot_since": _dt(m.group(1)) if m else None,
            "backup_latest": backups[-1].stem.removeprefix("db-") if backups else None,
            "backup_age_h": b_age, "log_errors": log_errors, "git_head": head or None}


def manifest_facts(root):
    p = root / "data" / "paper_run_manifest.json"
    if not p.exists():
        return None
    m = json.loads(p.read_text())
    return {"started_at": _dt(m.get("started_at")), "futures_restart_at": _dt(m.get("futures_restart_at")),
            "git_sha": (m.get("git_sha") or "")[:7] or None}


_LAST = ("ts", "type", "price", "buy", "sell", "strength", "threshold", "gap", "funding", "basis",
         "ls", "oi", "taker", "fng", "dxy", "dxy_chg", "sp", "sp_chg", "vix", "vix_chg", "gold",
         "gold_chg", "btc_dom", "stable", "news", "rsi", "atr", "ema200", "htf", "reasons", "open")


ACCOUNT_START_USD, ACCOUNT_FRACTION = 1000.0, 0.10   # the scanner's demo-account rule


def usd_account(closed, open_, start=ACCOUNT_START_USD, frac=ACCOUNT_FRACTION):
    """Translate a book kept in percent into a USD account: each position is `frac` of the
    realised equity at the moment it opened. Display only; nothing is traded or stored.
    `closed` items need opened_at, closed_at, pnl_pct; `open_` items opened_at, move_pct."""
    events = sorted([(_dt(c["opened_at"]), 0, i, "c") for i, c in enumerate(closed)] +
                    [(_dt(c["closed_at"]), 1, i, "c") for i, c in enumerate(closed)] +
                    [(_dt(o["opened_at"]), 0, i, "o") for i, o in enumerate(open_)],
                    key=lambda x: (x[0] or datetime.min.replace(tzinfo=UTC), x[1]))
    realised, size, curve = start, {}, []
    for t, kind, i, src in events:
        if kind == 0:
            size[(src, i)] = realised * frac
            if not curve and t:
                curve.append((t.timestamp(), start))
        else:
            realised += closed[i]["pnl_pct"] / 100 * size.get(("c", i), start * frac)
            curve.append((t.timestamp(), round(realised, 2)))
    upnl = sum((o.get("move_pct") or 0) / 100 * size.get(("o", i), 0) for i, o in enumerate(open_))
    used = sum(size.get(("o", i), 0) for i in range(len(open_)))
    return {"start": start, "realised": realised, "upnl": upnl, "equity": realised + upnl,
            "used": used, "cash": realised - used, "curve": curve}


# Third book (owner, 2026-11-16): the BTC bot as if Claude's entry opinion were a veto.
# Positions it DISAGREED with are dropped; AGREE, no opinion or an errored one are kept, as a
# veto gate that fails open would. Counterfactual only: a vetoed position would have freed its
# slot for another signal, which this cannot see. Positions opened before CLAUDE_FRESH_FROM had
# already been read when H-S was scored, so only later ones are fresh evidence.
CLAUDE_FRESH_FROM = datetime(2026, 11, 16, 20, 0, tzinfo=UTC)


def claude_filter(closed, open_, verdict, now):
    keep = lambda p: verdict.get(p.get("signal_id")) != "DISAGREE"
    kc = [c for c in closed if c["pnl_pct"] is not None and keep(c)]
    ko = [o for o in open_ if keep(o)]
    vetoed = [c for c in closed if c["pnl_pct"] is not None and not keep(c)]
    fresh = lambda xs: [x for x in xs if (_dt(x["opened_at"]) or now) >= CLAUDE_FRESH_FROM]
    acc = usd_account(kc, ko)
    acc.update(kept=len(kc), vetoed=len(vetoed), vetoed_open=len(open_) - len(ko),
               vetoed_pnl=sum(c["pnl_pct"] for c in vetoed), kept_pnl=sum(c["pnl_pct"] for c in kc),
               fresh_kept=len(fresh(kc)), fresh_vetoed=len(fresh(vetoed)),
               fresh_kept_pnl=sum(c["pnl_pct"] for c in fresh(kc)),
               fresh_vetoed_pnl=sum(c["pnl_pct"] for c in fresh(vetoed)),
               vetoed_rows=vetoed[::-1][:CLOSED_ROWS])
    return acc


def _book(rows):
    """Statistics of closed positions [(opened, closed, pnl, outcome)] in closing order."""
    pn = [r[2] for r in rows]
    if not pn:
        return {"n": 0, "sum": 0.0, "win": None}
    wins, losses = [p for p in pn if p > 0], [p for p in pn if p <= 0]
    acc, peak, dd = 0.0, 0.0, 0.0
    for p in pn:
        acc += p
        peak = max(peak, acc)
        dd = max(dd, peak - acc)
    holds = [(c - o).total_seconds() / 3600 for o, c, _, _ in rows if o and c]
    outcomes = {}
    for r in rows:
        outcomes[r[3]] = outcomes.get(r[3], 0) + 1
    return {"n": len(pn), "sum": round(acc, 2), "win": round(len(wins) / len(pn) * 100),
            "avg_win": sum(wins) / len(wins) if wins else None,
            "avg_loss": sum(losses) / len(losses) if losses else None,
            "pf": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None,
            "best": max(pn), "worst": min(pn), "max_dd": dd,
            "hold_h": sum(holds) / len(holds) if holds else None, "outcomes": outcomes}


def db_facts(db, now, run_start=None):
    """Everything the page shows from signal_history.db, as plain data."""
    con = _ro(db)
    try:
        q = lambda sql, *p: con.execute(sql, p).fetchall()
        tables = _tables(con)
        f = {"db_mb": round(Path(db).stat().st_size / 1e6, 1)}
        last = {}
        for mode in ("spot", "futures"):
            r = q(f"SELECT {_ts('timestamp')}, type, price, buy_score, sell_score, strength, "
                  f"threshold, gap_to_fire, funding_rate, basis_pct, ls_ratio, oi_change, "
                  f"taker_ratio, fear_greed, dxy, dxy_change, sp500, sp500_change, vix, vix_change, "
                  f"gold, gold_change, btc_dom, stablecoin_b, news_sentiment, rsi, atr, ema200, "
                  f"htf_data, reasons, open_positions FROM cycle_log WHERE mode=? "
                  f"ORDER BY {_ts('timestamp')} DESC, id DESC LIMIT 1", mode)
            if r:
                d = dict(zip(_LAST, r[0]))
                try:
                    d["htf"] = json.loads(d["htf"]) if d["htf"] else {}
                except ValueError:
                    d["htf"] = {}
                d["reasons"] = [x.strip() for x in (d["reasons"] or "").split("|") if x.strip()]
                last[mode] = d
        f["last"] = last
        ts = [_dt(v["ts"]) for v in last.values()]
        f["last_cycle_age_min"] = (round((now - max(ts)).total_seconds() / 60, 1) if ts else None)
        d24 = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        d72 = (now - timedelta(hours=72)).strftime("%Y-%m-%d %H:%M:%S")
        d7 = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        f["cycles_24h"] = q(f"SELECT COUNT(*) FROM cycle_log WHERE {_ts('timestamp')} >= ?", d24)[0][0]
        f["futures_blind_24h"] = q(f"SELECT COUNT(*) FROM cycle_log WHERE mode='futures' AND "
                                   f"funding_rate = 0 AND {_ts('timestamp')} >= ?", d24)[0][0]
        f["scores"] = {m: q(f"SELECT {_ts('timestamp')}, buy_score, sell_score, threshold "
                            f"FROM cycle_log WHERE mode=? AND {_ts('timestamp')} >= ? "
                            f"ORDER BY {_ts('timestamp')}", m, d72) for m in ("spot", "futures")}
        f["prices"] = q(f"SELECT {_ts('timestamp')}, price FROM cycle_log WHERE mode='futures' "
                        f"AND {_ts('timestamp')} >= ? ORDER BY {_ts('timestamp')}", d7)
        cols = ("id", "mode", "type", "entry_price", "stop_loss", "trailing_stop", "take_profit",
                "tp2", "opened_at", "partial_closed", "signal_id")
        f["open"] = [dict(zip(cols, r)) for r in q(
            f"SELECT {', '.join(cols)} FROM paper_positions WHERE closed_at IS NULL ORDER BY id")]
        for p in f["open"]:
            px = (last.get(p["mode"]) or {}).get("price")
            sgn = 1 if p["type"] == "BUY" else -1
            p["price"] = px
            p["move_pct"] = ((px - p["entry_price"]) / p["entry_price"] * 100 * sgn
                             if px and p["entry_price"] else None)
            stop = p["trailing_stop"] or p["stop_loss"]
            p["stop"] = stop
            p["to_stop_pct"] = ((stop - px) / px * 100 * sgn if px and stop else None)
            p["hours"] = ((now - _dt(p["opened_at"])).total_seconds() / 3600
                          if _dt(p["opened_at"]) else None)
        cols = ("id", "mode", "type", "entry_price", "exit_price", "opened_at", "closed_at",
                "outcome", "pnl_pct", "signal_id")
        closed = [dict(zip(cols, r)) for r in q(
            f"SELECT {', '.join(cols)} FROM paper_positions WHERE closed_at IS NOT NULL "
            f"ORDER BY {_ts('closed_at')}")]
        if run_start:
            closed = [c for c in closed if (_dt(c["opened_at"]) or now) >= run_start]
        f["closed_recent"] = closed[::-1][:CLOSED_ROWS]
        f["account"] = usd_account([c for c in closed if c["pnl_pct"] is not None], f["open"])
        if "shadow_opinions" in tables:
            verdict = dict(q("SELECT signal_id, verdict FROM shadow_opinions WHERE provider='anthropic' "
                             "AND error IS NULL AND verdict IS NOT NULL AND signal_id IS NOT NULL "
                             "ORDER BY id"))
            f["claude_account"] = claude_filter(closed, f["open"], verdict, now)
        f["curve"], f["totals"] = {}, {}
        for mode in ("spot", "futures"):
            rows = [(_dt(c["opened_at"]), _dt(c["closed_at"]), c["pnl_pct"], c["outcome"])
                    for c in closed if c["mode"] == mode and c["pnl_pct"] is not None]
            acc = 0.0
            pts = [(rows[0][0].timestamp(), 0.0)] if rows and rows[0][0] else []   # from zero
            for o, c, p, _ in rows:
                acc += p
                pts.append((c.timestamp(), round(acc, 3)))
            f["curve"][mode] = pts
            f["totals"][mode] = _book(rows)
        week = now - timedelta(days=7)
        f["marks"] = [c for c in closed if (_dt(c["closed_at"]) or now) >= week] + \
            [dict(p, closed_at=None, exit_price=None) for p in f["open"]]
        f["blocks"] = {}
        if "signal_blocks" in tables:
            for mode, gate, n24, n7 in q(
                    f"SELECT mode, gate, SUM({_ts('timestamp')} >= ?), COUNT(*) FROM signal_blocks "
                    f"WHERE {_ts('timestamp')} >= ? GROUP BY mode, gate ORDER BY 4 DESC", d24, d7):
                f["blocks"].setdefault(mode, []).append((gate, n24 or 0, n7))
        op = {}
        for name, sql in (
                ("entry", "SELECT timestamp, mode, signal_type, provider, verdict, "
                          "opinion_confidence, reason, error FROM shadow_opinions"),
                ("exit", "SELECT timestamp, mode, side, provider, verdict, opinion_confidence, "
                         "reason, error FROM shadow_exit_opinions"),
                ("flip", "SELECT timestamp, 'futures', side, provider, verdict, "
                         "opinion_confidence, reason, error FROM shadow_flip_opinions")):
            table = sql.rsplit(" ", 1)[1]
            if table not in tables:
                continue
            stats = q(f"SELECT provider, COUNT(*), SUM(error IS NOT NULL), "
                      f"GROUP_CONCAT(verdict) FROM {table} WHERE {_ts('timestamp')} >= ? "
                      f"GROUP BY provider", d7)
            counts = []
            for prov, n, errs, verdicts in stats:
                vc = {}
                for v in (verdicts or "").split(","):
                    if v:
                        vc[v] = vc.get(v, 0) + 1
                counts.append({"provider": prov, "n": n, "errors": errs or 0, "verdicts": vc})
            op[name] = {"stats": counts, "rows": q(f"{sql} ORDER BY id DESC LIMIT {OPINION_ROWS}")}
        f["opinions"] = op
        return f
    finally:
        con.close()


def macro_facts(root, now):
    """Upcoming HIGH-impact events from the scraper's calendar (times are UTC)."""
    p = root / "data" / "macro_events.csv"
    if not p.exists():
        return None
    out = []
    with p.open(newline="") as fh:
        for r in csv.DictReader(fh):
            if (r.get("impact") or "").lower() != "high":
                continue
            try:
                t = datetime.strptime(r["timestamp"].strip(), "%m-%d-%Y %I:%M%p").replace(tzinfo=UTC)
            except (ValueError, KeyError):
                continue
            if t >= now - timedelta(hours=1):
                out.append({"t": t, "event": r.get("event"), "forecast": r.get("forecast"),
                            "previous": r.get("previous"), "actual": r.get("actual")})
    out.sort(key=lambda x: x["t"])
    return out[:6]


def scanner_facts(path):
    """The newest scan cycle and its picks, plus counts, or None without a scanner DB."""
    if not Path(path).exists():
        return None
    con = _ro(path)
    try:
        if "scan_cycles" not in _tables(con):
            return None
        n, errs, usd = con.execute("SELECT COUNT(*), SUM(error IS NOT NULL), SUM(cost_usd) "
                                   "FROM scan_cycles").fetchone()
        last = con.execute("SELECT id, bar_close, model, error FROM scan_cycles "
                           "ORDER BY bar_close DESC LIMIT 1").fetchone()
        picks = con.execute("SELECT coin, side, confidence, reason FROM scan_picks WHERE "
                            "cycle_id = ? ORDER BY side = 'NONE', confidence DESC",
                            (last[0],)).fetchall() if last else []
        days = con.execute("SELECT COUNT(DISTINCT substr(c.bar_close, 1, 10)) FROM scan_picks p "
                           "JOIN scan_cycles c ON c.id = p.cycle_id "
                           "WHERE p.side != 'NONE'").fetchone()[0]
        n_picks = con.execute("SELECT COUNT(*) FROM scan_picks WHERE side != 'NONE'").fetchone()[0]
        out = {"cycles": n, "errors": errs or 0, "usd": usd or 0.0, "last": last,
               "picks": picks, "days": days, "n_picks": n_picks, "book": None}
        ctx = con.execute("SELECT context FROM scan_cycles WHERE context IS NOT NULL "
                          "ORDER BY bar_close DESC LIMIT 1").fetchone()
        coins = (json.loads(ctx[0]).get("coins") or {}) if ctx else {}
        px = dict(con.execute("SELECT coin, close FROM scan_picks WHERE cycle_id = ?",
                              (last[0],)).fetchall()) if last else {}
        pick = {c: (sd, cf, r) for c, sd, cf, r in picks}
        pos = []
        if "scan_positions" in _tables(con):
            pcols = [r[1] for r in con.execute("PRAGMA table_info(scan_positions)")]
            cols = [c for c in ("id", "coin", "side", "confidence", "opened_bar", "entry", "stop",
                                "target", "closed_bar", "exit", "outcome", "pnl_pct", "pnl_usd",
                                "size_usd") if c in pcols]
            pos = [dict(zip(cols, r)) for r in con.execute(
                f"SELECT {', '.join(cols)} FROM scan_positions ORDER BY id")]
        opened = [p for p in pos if p["closed_bar"] is None]
        for p in opened:
            cur = px.get(p["coin"])
            p["price"] = cur
            p["move_pct"] = ((cur / p["entry"] - 1) * 100 * (1 if p["side"] == "LONG" else -1)
                             if cur else None)
            p["upnl"] = (p["move_pct"] / 100 * (p.get("size_usd") or 0)
                         if p["move_pct"] is not None else 0.0)
        closed = sorted((p for p in pos if p["closed_bar"]), key=lambda p: p["closed_bar"])
        rows = [(_dt(p["opened_bar"]), _dt(p["closed_bar"]), p["pnl_pct"], p["outcome"])
                for p in closed]
        from agents.scanner import BOOK_START_USD, UNIVERSE
        realised = BOOK_START_USD + sum(p["pnl_usd"] or 0 for p in closed)
        curve = ([(rows[0][0].timestamp(), BOOK_START_USD)] if rows else [])
        acc = BOOK_START_USD
        for p in closed:
            acc += p["pnl_usd"] or 0
            curve.append((_dt(p["closed_bar"]).timestamp(), round(acc, 2)))
        used = sum(p.get("size_usd") or 0 for p in opened)
        upnl = sum(p["upnl"] for p in opened)
        out["book"] = {"open": opened, "closed": closed[::-1][:CLOSED_ROWS], "stats": _book(rows),
                       "start": BOOK_START_USD, "realised": realised, "equity": realised + upnl,
                       "upnl": upnl, "used": used, "cash": realised - used, "curve": curve}
        held = {p["coin"]: p for p in opened}
        out["coins"] = [{"coin": c, "f": coins.get(c), "price": px.get(c), "pick": pick.get(c),
                         "pos": held.get(c)} for c in UNIVERSE]
        return out
    finally:
        con.close()


def cost_facts(db, root, now):
    from agents.ops_report import budget_from_env, collect_cost_facts
    c = collect_cost_facts(db, root / "data" / "llm_usage.jsonl", now)
    c["budget"] = budget_from_env()
    return c


def collect(root, db, scanner_db, now=None, run=None):
    """One dict for the page. A part that fails is reported in `errors`, never raised."""
    now = now or datetime.now(UTC)
    out = {"now": now, "errors": []}

    def part(key, fn):
        try:
            out[key] = fn()
        except Exception as exc:  # noqa: BLE001 - a broken panel must not blank the page
            out[key] = None
            out["errors"].append(f"{key}: {type(exc).__name__}: {exc}")
    part("manifest", lambda: manifest_facts(root))
    run_start = (out.get("manifest") or {}).get("started_at")
    part("host", lambda: host_facts(root, now, run))
    part("db", lambda: db_facts(db, now, run_start))
    part("macro", lambda: macro_facts(root, now))
    part("scanner", lambda: scanner_facts(scanner_db))
    part("cost", lambda: cost_facts(db, root, now))
    return out


def issues(f):
    """What needs a look, in plain words. Empty means healthy."""
    out = []
    h, d = f.get("host") or {}, f.get("db") or {}
    for name, st in (h.get("services") or {}).items():
        if st != "active":
            out.append(f"layanan {name} {st}")
    age = d.get("last_cycle_age_min")
    if f.get("db") is not None and (age is None or age > MAX_CYCLE_AGE_MIN):
        out.append("tidak ada siklus baru" if age is None else f"siklus terakhir {age:.0f} menit lalu")
    fut = (d.get("last") or {}).get("futures")
    if fut and fut.get("funding") == 0:
        out.append("data futures Binance kosong di siklus terakhir")
    mem = h.get("mem_available_mb")
    if mem is not None and mem < MIN_MEM_MB:
        out.append(f"RAM tersisa {mem:.0f} MB")
    disk = h.get("disk_used_pct")
    if disk is not None and disk > MAX_DISK_PCT:
        out.append(f"disk {disk:.0f}% terpakai")
    b = h.get("backup_age_h")
    if b is not None and b > MAX_BACKUP_AGE_H:
        out.append(f"backup terakhir {b:.0f} jam lalu")
    for err in f.get("errors") or []:
        out.append(f"panel {err.split(':')[0]} gagal dibaca")
    return out


# ── Render helpers ──────────────────────────────────────────────────────────────────

FONTS = ("https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600&"
         "family=Barlow+Condensed:wght@500;600&display=swap")

CSS = """
:root{--paper:#EEF1F0;--surface:#FFFFFF;--ink:#17201F;--ink2:#46524F;--mute:#68746F;
--rule:#D3DAD8;--rule2:#E4E9E7;--buy:#2F6DB5;--sell:#C8641A;--good:#1F7A50;--bad:#B3261E;
--warn:#8A5A00;--chip:#E2E8E6;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--paper:#12171A;--surface:#1A2125;--ink:#E3E9E7;
--ink2:#BAC4C1;--mute:#8E9A97;--rule:#2E383C;--rule2:#252D31;--buy:#5B93DB;--sell:#D86F24;
--good:#4CC38A;--bad:#F0857D;--warn:#E0A84A;--chip:#252D31;color-scheme:dark}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);
font:15px/1.5 Barlow,system-ui,-apple-system,"Segoe UI",sans-serif;font-variant-numeric:tabular-nums}
main{max-width:1240px;margin:0 auto;padding:28px 24px 64px}
a{color:inherit}
.cond{font-family:"Barlow Condensed",Barlow,system-ui,sans-serif}
h1{font:600 15px/1.2 Barlow,system-ui,sans-serif;margin:0;color:var(--ink2)}
h2{font:600 22px/1.2 "Barlow Condensed",Barlow,system-ui,sans-serif;margin:0 0 4px}
h3{font:600 15px/1.3 Barlow,system-ui,sans-serif;margin:0 0 8px}
.lede{color:var(--mute);margin:0 0 16px;max-width:72ch}
.top{display:flex;justify-content:space-between;align-items:baseline;gap:16px;flex-wrap:wrap}
.stamp{color:var(--mute);font-size:13px}
.verdict{font:600 clamp(26px,3.6vw,40px)/1.12 "Barlow Condensed",Barlow,sans-serif;
margin:10px 0 6px;max-width:30ch}
.verdict.bad{color:var(--bad)}
.sub{color:var(--ink2);margin:0 0 18px;max-width:80ch}
.pills{display:flex;flex-wrap:wrap;gap:6px 18px;margin:0;padding:12px 0;border-top:1px solid var(--rule);
border-bottom:1px solid var(--rule);list-style:none;font-size:14px}
.pills li{display:flex;gap:7px;align-items:center;white-space:nowrap}
.pills b{font-weight:600}
.dot{width:9px;height:9px;border-radius:50%;background:var(--good);flex:none}
.dot.bad{background:var(--bad)}.dot.warn{background:var(--warn)}.dot.idle{background:var(--mute)}
section{margin-top:44px}
.panel{background:var(--surface);border:1px solid var(--rule);border-radius:6px;padding:18px 20px}
.cols{display:grid;gap:20px;grid-template-columns:repeat(auto-fit,minmax(min(340px,100%),1fr))}
.cols3{display:grid;gap:20px;grid-template-columns:repeat(auto-fit,minmax(min(260px,100%),1fr))}
/* trigger rails: the hero */
.rails{display:grid;gap:22px 36px;grid-template-columns:repeat(auto-fit,minmax(min(360px,100%),1fr))}
.rail h3{display:flex;justify-content:space-between;align-items:baseline}
.rail h3 span{font-weight:400;color:var(--mute);font-size:13px}
.track{position:relative;height:30px;margin:6px 0 2px;background:var(--rule2);border-radius:3px}
.fill{position:absolute;left:0;top:0;bottom:0;border-radius:3px}
.fill.buy{background:var(--buy)}.fill.sell{background:var(--sell)}
.tick{position:absolute;top:-6px;bottom:-6px;width:2px;background:var(--ink)}
.tick::after{content:attr(data-l);position:absolute;top:-18px;left:4px;font-size:12px;
color:var(--ink2);white-space:nowrap}
.trow{display:grid;grid-template-columns:48px 1fr 56px;gap:10px;align-items:center;margin-top:12px}
.trow:first-of-type{margin-top:30px}
.trow .lbl{font-weight:600}.trow .val{text-align:right;font:600 22px/1 "Barlow Condensed",sans-serif}
.lbl.buy{color:var(--buy)}.lbl.sell{color:var(--sell)}
.note{color:var(--mute);font-size:13px;margin-top:10px}
.big{font:600 34px/1 "Barlow Condensed",sans-serif}
.kv{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(150px,45%),1fr));gap:14px 18px;margin:0}
.kv div{border-left:2px solid var(--rule);padding-left:10px}
.kv dt{color:var(--mute);font-size:13px}.kv dd{margin:0;font:600 20px/1.2 "Barlow Condensed",sans-serif}
.kv dd small{font:400 13px Barlow,sans-serif;color:var(--ink2);margin-left:4px}
table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:7px 12px 7px 0;border-bottom:1px solid var(--rule2);vertical-align:top;white-space:nowrap}
td.r{white-space:normal}
th{font-weight:500;color:var(--mute);font-size:13px;border-bottom-color:var(--rule)}
td.n,th.n{text-align:right}
td.r{min-width:240px;color:var(--ink2)}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
.pos{color:var(--good)}.neg{color:var(--bad)}.mute{color:var(--mute)}
.dir{font-weight:600}.dir.BUY,.dir.LONG{color:var(--buy)}.dir.SELL,.dir.SHORT{color:var(--sell)}
.empty{color:var(--ink2);padding:6px 0}
.bars{display:grid;grid-template-columns:minmax(120px,190px) 1fr 44px 44px;gap:6px 10px;align-items:center;font-size:14px}
.bars .b{height:10px;background:var(--ink2);border-radius:0 3px 3px 0;min-width:2px}
.bars .h{color:var(--mute);font-size:12px}
.reasons{margin:0;padding:0;list-style:none;columns:2 300px;column-gap:28px;font-size:14px;color:var(--ink2)}
.reasons li{break-inside:avoid;padding:3px 0;border-bottom:1px solid var(--rule2)}
.agenda li{display:grid;grid-template-columns:96px 1fr;gap:10px;padding:7px 0;border-bottom:1px solid var(--rule2)}
.agenda{list-style:none;margin:0;padding:0}
.agenda .when{font:600 17px/1.2 "Barlow Condensed",sans-serif}
.agenda .soon .when{color:var(--sell)}
.chart{position:relative}
.chart svg{display:block;width:100%;height:auto;overflow:visible}
.chart .ax{font:12px Barlow,sans-serif;fill:var(--mute)}
.chart .grid{stroke:var(--rule2)}.chart .zero{stroke:var(--rule)}
.tip{position:absolute;pointer-events:none;background:var(--ink);color:var(--paper);font-size:13px;
padding:6px 9px;border-radius:4px;white-space:nowrap;opacity:0;transform:translate(-50%,-100%);
transition:opacity .08s}
.legend{display:flex;gap:16px;font-size:13px;color:var(--ink2);margin:0 0 6px}
.legend i{display:inline-block;width:14px;height:3px;vertical-align:middle;margin-right:6px}
details{margin-top:10px}summary{cursor:pointer;color:var(--ink2);font-size:14px}
summary:focus-visible,a:focus-visible{outline:2px solid var(--buy);outline-offset:2px}
.err{border:1px solid var(--bad);color:var(--bad);border-radius:6px;padding:10px 14px;margin:12px 0}
.meta{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(220px,100%),1fr));gap:4px 24px;font-size:14px;color:var(--ink2)}
.meta b{color:var(--ink);font-weight:500}
.books{margin-top:22px;grid-template-columns:repeat(auto-fit,minmax(min(300px,100%),1fr))}
a.book{display:block;text-decoration:none;color:inherit}
a.book:hover{border-color:var(--ink2)}
.book h3 span{font-weight:400;color:var(--mute);font-size:13px;margin-left:6px}
.tabs{display:flex;gap:4px;margin:34px 0 0;border-bottom:1px solid var(--rule);overflow-x:auto}
.tabs button{font:600 17px/1 "Barlow Condensed",Barlow,sans-serif;color:var(--mute);background:none;
border:0;border-bottom:3px solid transparent;padding:12px 16px 11px;cursor:pointer;white-space:nowrap}
.tabs button:hover{color:var(--ink)}
.tabs button[aria-selected=true]{color:var(--ink);border-bottom-color:var(--ink)}
.tabs button:focus-visible{outline:2px solid var(--buy);outline-offset:-2px}
.tabpanel[hidden]{display:none}
.tabpanel>section:first-child{margin-top:28px}
@media (max-width:560px){main{padding:18px 16px 48px}.rails{grid-template-columns:1fr}
.cols,.cols3{grid-template-columns:1fr}.bars{grid-template-columns:110px 1fr 34px 34px}
td.r{min-width:180px}}
@media (prefers-reduced-motion:reduce){.tip{transition:none}}
"""

JS = """

(function(){
  var tabs=[].slice.call(document.querySelectorAll('.tabs button')),
      ids=tabs.map(function(b){return b.dataset.tab});
  function show(id,focus){
    if(ids.indexOf(id)<0)id='scanner';
    tabs.forEach(function(b){var on=b.dataset.tab===id;b.setAttribute('aria-selected',on);
      b.tabIndex=on?0:-1;document.getElementById('tab-'+b.dataset.tab).hidden=!on;if(on&&focus)b.focus()});
    try{localStorage.setItem('dash-tab',id)}catch(e){}
    if(location.hash!=='#'+id)history.replaceState(null,'','#'+id);
  }
  tabs.forEach(function(b,i){
    b.addEventListener('click',function(){show(b.dataset.tab)});
    b.addEventListener('keydown',function(ev){
      var k={ArrowRight:1,ArrowLeft:-1}[ev.key];if(!k)return;
      show(ids[(i+k+ids.length)%ids.length],true);ev.preventDefault()});
  });
  document.querySelectorAll('a[data-tab]').forEach(function(a){
    a.addEventListener('click',function(ev){ev.preventDefault();show(a.dataset.tab);
      document.querySelector('.tabs').scrollIntoView({behavior:'smooth',block:'start'})})});
  var saved=null;try{saved=localStorage.getItem('dash-tab')}catch(e){}
  show(location.hash.slice(1)||saved||'scanner');
})();
document.querySelectorAll('.chart[data-s]').forEach(function(c){
  var d=JSON.parse(c.dataset.s),svg=c.querySelector('svg'),tip=c.querySelector('.tip'),
      ln=svg.querySelector('.cross'),dots=svg.querySelectorAll('.hov');
  function at(ev){
    var r=svg.getBoundingClientRect(),vx=(ev.clientX-r.left)/r.width*d.W;
    if(vx<d.L||vx>d.W-d.R){hide();return}
    var t=d.t0+(vx-d.L)/(d.W-d.L-d.R)*(d.t1-d.t0),rows=[],best=null;
    d.s.forEach(function(s,k){
      var p=null,dist=1e18;
      s.p.forEach(function(q){var dd=Math.abs(q[0]-t);if(dd<dist){dist=dd;p=q}});
      if(!p){dots[k].setAttribute('opacity',0);return}
      if(best===null)best=p[0];
      dots[k].setAttribute('cx',p[2]);dots[k].setAttribute('cy',p[3]);dots[k].setAttribute('opacity',1);
      rows.push((s.n?s.n+' ':'')+p[1]);
    });
    if(best===null){hide();return}
    var x=d.s[0].p.length?nearest(d.s[0].p,t)[2]:vx;
    ln.setAttribute('x1',x);ln.setAttribute('x2',x);ln.setAttribute('opacity',1);
    tip.innerHTML='<b>'+fmt(best)+'</b><br>'+rows.join('<br>');
    tip.style.left=(x/d.W*r.width)+'px';tip.style.top='8px';tip.style.opacity=1;
  }
  function nearest(ps,t){var b=ps[0];ps.forEach(function(q){if(Math.abs(q[0]-t)<Math.abs(b[0]-t))b=q});return b}
  function hide(){tip.style.opacity=0;ln.setAttribute('opacity',0);dots.forEach(function(x){x.setAttribute('opacity',0)})}
  function fmt(s){var x=new Date(s*1000);return x.toISOString().slice(5,16).replace('T',' ')+' UTC'}
  svg.addEventListener('pointermove',at);svg.addEventListener('pointerleave',hide);
});
"""


def _n(v, nd=2, sign=False, suffix=""):
    if v is None:
        return "–"
    s = f"{v:+,.{nd}f}" if sign else f"{v:,.{nd}f}"
    return s.replace(",", "\u2009").replace(".", ",").replace("\u2009", ".") + suffix


def _pnl(v, nd=2, suffix=""):
    if v is None:
        return '<span class="mute">–</span>'
    cls = "pos" if v > 0 else "neg" if v < 0 else "mute"
    return f'<span class="{cls}">{_n(v, nd, True, suffix)}</span>'


def _t(raw):
    d = raw if isinstance(raw, datetime) else _dt(raw)
    return d.strftime("%d %b %H:%M") if d else "–"


def _ago(now, d):
    if not d:
        return "–"
    s = (now - d).total_seconds()
    if s < 3600:
        return f"{s / 60:.0f} mnt lalu"
    if s < 172800:
        return f"{s / 3600:.0f} jam lalu"
    return f"{s / 86400:.0f} hari lalu"


def _until(now, d):
    s = (d - now).total_seconds()
    if s < 0:
        return "baru lewat"
    h, m = int(s // 3600), int(s % 3600 // 60)
    return f"{h} j {m:02d} m" if h else f"{m} mnt"


def _dir(side):
    side = str(side or "–")
    return f'<span class="dir {e(side)}">{e(side)}</span>'


def _table(head, rows, num=()):
    h = "".join(f'<th class="{"n" if i in num else ""}">{e(x)}</th>' for i, x in enumerate(head))
    b = "".join("<tr>" + "".join(f'<td class="{"n" if i in num else ""}">{c}</td>'
                                 if not c.startswith("<td") else c for i, c in enumerate(r)) + "</tr>"
                for r in rows)
    return f'<div class="scroll"><table><tr>{h}</tr>{b}</table></div>'


def _nice_ticks(lo, hi, n=5):
    """About n - 1 round values (1, 2, 2.5, 5 x 10^k) inside [lo, hi]."""
    import math
    raw = (hi - lo) / max(n - 1, 1)
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    t = math.ceil(lo / step) * step
    out = []
    while t <= hi + 1e-9:
        out.append(round(t, 10))
        t += step
    return out


def chart(series, *, width=1100, height=220, y_fmt="{:.2f}", zero=False, markers=(), t_range=None,
          label=""):
    """A time-series line chart as inline SVG plus the data its hover layer reads. `width` is
    the viewBox width: set it near the rendered width so 12-unit axis text stays ~12px.
    series = [{"n": name, "c": css colour, "p": [(epoch_s, y)], "dash": bool}]"""
    pts = [p for s in series for p in s["p"]]
    if not pts:
        return '<p class="empty">Belum ada data untuk grafik ini.</p>'
    W, H, L, R, T, B = width, height, 56, 14, 14, 26
    t0, t1 = t_range or (min(p[0] for p in pts), max(p[0] for p in pts))
    t1 = t1 if t1 > t0 else t0 + 3600
    ys = [p[1] for p in pts] + [m[1] for m in markers] + ([0.0] if zero else [])
    lo, hi = min(ys), max(ys)
    pad = (hi - lo) * 0.08 or abs(hi) * 0.02 or 1.0
    lo, hi = lo - pad, hi + pad
    X = lambda t: L + (t - t0) / (t1 - t0) * (W - L - R)
    Y = lambda y: T + (hi - y) / (hi - lo) * (H - T - B)
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{e(label)}">']
    for y in _nice_ticks(lo, hi):
        out.append(f'<line class="grid" x1="{L}" x2="{W - R}" y1="{Y(y):.1f}" y2="{Y(y):.1f}"/>'
                   f'<text class="ax" x="{L - 8}" y="{Y(y) + 4:.1f}" text-anchor="end">'
                   f'{e(y_fmt.format(y))}</text>')
    if zero and lo < 0 < hi:
        out.append(f'<line class="zero" x1="{L}" x2="{W - R}" y1="{Y(0):.1f}" y2="{Y(0):.1f}"/>')
    span = t1 - t0
    step = 86400 if span > 3 * 86400 else 21600 if span > 86400 else 3600 * 4
    if span > 30 * 86400 or (span > 6 * 86400 and W < 800):
        step = 7 * 86400
    elif span > 3 * 86400 and W < 800:
        step = 2 * 86400
    elif span > 86400 and W < 800:
        step = 43200
    first = (int(t0) // step + 1) * step
    for t in range(first, int(t1) + 1, step):
        d = datetime.fromtimestamp(t, UTC)
        lab = d.strftime("%d %b") if step >= 86400 else d.strftime("%H:%M")
        out.append(f'<text class="ax" x="{X(t):.1f}" y="{H - 6}" text-anchor="middle">{lab}</text>')
    data = {"W": W, "L": L, "R": R, "t0": t0, "t1": t1, "s": []}
    for s in series:
        if not s["p"]:
            data["s"].append({"n": s["n"], "p": []})
            continue
        d = " ".join(f"{X(t):.1f},{Y(y):.1f}" for t, y in s["p"])
        dash = ' stroke-dasharray="5 4"' if s.get("dash") else ""
        out.append(f'<polyline fill="none" stroke="{s["c"]}" stroke-width="2" stroke-linejoin="round"'
                   f'{dash} points="{d}"/>')
        data["s"].append({"n": s["n"], "p": [[t, y_fmt.format(y), round(X(t), 1), round(Y(y), 1)]
                                             for t, y in s["p"]]})
    for t, y, col, filled, tip in markers:
        out.append(f'<circle cx="{X(t):.1f}" cy="{Y(y):.1f}" r="6" fill="{col if filled else "var(--surface)"}" '
                   f'stroke="{col}" stroke-width="2.5"><title>{e(tip)}</title></circle>')
    out.append(f'<line class="cross" x1="0" x2="0" y1="{T}" y2="{H - B}" stroke="var(--mute)" opacity="0"/>')
    for s in series:
        out.append(f'<circle class="hov" r="5" fill="{s["c"]}" stroke="var(--surface)" stroke-width="2" opacity="0"/>')
    out.append("</svg>")
    return (f'<div class="chart" data-s="{e(json.dumps(data, separators=(",", ":")))}">'
            f'{"".join(out)}<div class="tip"></div></div>')


# ── Render ──────────────────────────────────────────────────────────────────────────

def _rail(mode, l):
    """The hero: BUY and SELL score against the threshold, on one scale per mode."""
    if not l:
        return f'<div class="rail"><h3>{mode}</h3><p class="empty">Belum ada siklus.</p></div>'
    thr = l["threshold"] or 0
    top = max(l["buy"] or 0, l["sell"] or 0, thr) * 1.25 or 1
    pct = lambda v: max(0.0, min(100.0, (v or 0) / top * 100))
    rows = []
    for k, (side, v) in enumerate((("BUY", l["buy"]), ("SELL", l["sell"]))):
        cls = side.lower()
        lab = f' data-l="ambang {_n(thr)}"' if k == 0 else ""
        rows.append(f'<div class="trow"><span class="lbl {cls}">{side}</span>'
                    f'<div class="track" role="img" aria-label="skor {side} {_n(v)} dari ambang {_n(thr)}">'
                    f'<div class="fill {cls}" style="width:{pct(v):.1f}%"></div>'
                    f'<div class="tick" style="left:{pct(thr):.1f}%"{lab}></div></div>'
                    f'<span class="val">{_n(v)}</span></div>')
    # Spot only ever opens BUY; its SELL score closes or blocks, never shorts.
    sides = (("BUY", l["buy"] or 0),) if mode == "spot" else (("BUY", l["buy"] or 0), ("SELL", l["sell"] or 0))
    lead = max(sides, key=lambda x: x[1])
    gap = (lead[1] - thr)
    if l["type"] in ("BUY", "SELL"):
        say = f"Sinyal {l['type']} menyala di siklus ini."
    elif gap >= 0:
        say = (f"Skor {lead[0]} sudah di atas ambang, tapi siklus ini HOLD: "
               f"filter lain (konfirmasi, gate, atau zona mati) menahannya.")
    else:
        say = f"Skor {lead[0]} butuh {_n(-gap)} poin lagi untuk menyentuh ambang."
    if mode == "spot":
        say += " Spot hanya membuka BUY."
    return (f'<div class="rail"><h3>{mode.capitalize()} <span>{_t(l["ts"])} UTC · harga '
            f'{_n(l["price"])}</span></h3>{"".join(rows)}<p class="note">{say}</p></div>')


def _book_table(totals):
    rows = []
    lab = {"spot": "Spot", "futures": "Futures"}
    for m, t in totals.items():
        if not t["n"]:
            rows.append([lab[m], "0"] + ["–"] * 8)
            continue
        oc = ", ".join(f"{k} {v}" for k, v in sorted(t["outcomes"].items(), key=lambda x: -x[1]))
        rows.append([lab[m], str(t["n"]), _pnl(t["sum"], 2, " pp"), f'{t["win"]}%',
                     _pnl(t["avg_win"]), _pnl(t["avg_loss"]),
                     _n(t["pf"]) if t["pf"] is not None else "–", _pnl(-t["max_dd"]),
                     _n(t["hold_h"], 0, suffix=" j") if t["hold_h"] is not None else "–",
                     f'<span class="mute">{e(oc)}</span>'])
    return _table(["buku", "posisi", "total", "menang", "rata menang", "rata kalah",
                   "profit factor", "drawdown", "lama", "hasil"], rows, num=(1, 2, 3, 4, 5, 6, 7, 8))


def _overview(s, d):
    """Both books side by side: shown above the tabs."""
    bk = (s or {}).get("book")
    tot = d.get("totals") or {}
    fu, sp = tot.get("futures") or {}, tot.get("spot") or {}
    ac = d.get("account")
    P = ['<div class="cols books">']
    P.append('<a class="panel book" href="#btc" data-tab="btc"><h3>Bot BTC <span>paper run, diuji hipotesis</span></h3>'
             '<dl class="kv">'
             + (f'<div><dt>Ekuitas</dt><dd>${_n(ac["equity"])}<small>'
                f'{_pnl((ac["equity"] / ac["start"] - 1) * 100, 2, "%")}</small></dd></div>' if ac else "")
             + f'<div><dt>Futures / spot</dt><dd>{_pnl(fu.get("sum"), 1, " pp")} / {_pnl(sp.get("sum"), 1, " pp")}</dd></div>'
             f'<div><dt>Terbuka</dt><dd>{len(d.get("open") or [])}</dd></div></dl></a>')
    ca = d.get("claude_account")
    if ca:
        P.append('<a class="panel book" href="#btc" data-tab="btc"><h3>BTC + filter Claude '
                 '<span>posisi yang ditolak Claude dibuang</span></h3><dl class="kv">'
                 f'<div><dt>Ekuitas</dt><dd>${_n(ca["equity"])}<small>'
                 f'{_pnl((ca["equity"] / ca["start"] - 1) * 100, 2, "%")}</small></dd></div>'
                 f'<div><dt>Diambil / dibuang</dt><dd>{ca["kept"]} / {ca["vetoed"]}</dd></div>'
                 f'<div><dt>Data baru</dt><dd>{ca["fresh_kept"] + ca["fresh_vetoed"]}<small>posisi</small></dd></div>'
                 '</dl></a>')
    if bk:
        ret = (bk["equity"] / bk["start"] - 1) * 100
        P.append('<a class="panel book" href="#scanner" data-tab="scanner"><h3>Scanner 9 koin '
                 '<span>akun demo dari pick Claude</span></h3><dl class="kv">'
                 f'<div><dt>Ekuitas</dt><dd>${_n(bk["equity"])}<small>{_pnl(ret, 2, "%")}</small></dd></div>'
                 f'<div><dt>Kas bebas</dt><dd>${_n(bk["cash"])}</dd></div>'
                 f'<div><dt>Terbuka</dt><dd>{len(bk["open"])}</dd></div></dl></a>')
    else:
        P.append('<div class="panel book"><h3>Scanner 9 koin</h3><p class="empty">Scanner belum '
                 'berjalan di host ini.</p></div>')
    P.append("</div>")
    return "".join(P)


def _scanner_tab(s):
    """The scanner tab: account, open positions, the nine coins, history, H-SC progress."""
    if not s:
        return '<p class="empty">Scanner belum berjalan di host ini.</p>'
    bk, P = s.get("book"), []
    if bk:
        st = bk["stats"]
        ret = (bk["equity"] / bk["start"] - 1) * 100
        P.append('<section><h2>Akun demo</h2><p class="lede">Saldo awal $' + _n(bk["start"], 0) +
                 '. Setiap pick LONG/SHORT dibuka sebagai posisi 10% dari ekuitas terealisasi: stop '
                 '1,5×ATR, target 3×ATR, paling lama 24 jam, satu posisi per koin, biaya 0,20pp. '
                 'Untuk mengumpulkan data, bukan klaim profit.</p>')
        kv = [("Ekuitas", f'${_n(bk["equity"])}<small>{_pnl(ret, 2, "%")}</small>'),
              ("Terealisasi", _pnl(bk["realised"] - bk["start"], 2, " USD")),
              ("Mengambang", _pnl(bk["upnl"], 2, " USD")),
              ("Kas bebas", f'${_n(bk["cash"])}<small>${_n(bk["used"])} di posisi</small>'),
              ("Ditutup", str(st["n"])), ("Menang", f'{st["win"]}%' if st["win"] is not None else "–"),
              ("Profit factor", _n(st.get("pf")) if st.get("pf") is not None else "–"),
              ("Drawdown", _pnl(-st["max_dd"], 2, " pp") if st["n"] else "–"),
              ("Rata menang / kalah", f'{_pnl(st.get("avg_win"))} / {_pnl(st.get("avg_loss"))}' if st["n"] else "–"),
              ("Hasil exit", e(", ".join(f"{k} {v}" for k, v in (st.get("outcomes") or {}).items())) or "–")]
        P.append('<div class="panel"><dl class="kv">' + "".join(
            f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in kv) + "</dl></div>")
        P.append('<h3 style="margin-top:22px">Posisi terbuka</h3>')
        if bk["open"]:
            rows = [[f"<b>{e(p['coin'])}</b>", _dir(p["side"]), _t(p["opened_bar"]),
                     _n(p.get("size_usd")), _n(p["entry"], 4), _n(p["price"], 4),
                     _pnl(p["move_pct"], 2, "%"), _pnl(p["upnl"], 2), _n(p["stop"], 4),
                     _n(p["target"], 4)] for p in bk["open"]]
            P.append(_table(["koin", "arah", "dibuka", "ukuran $", "entry", "harga", "gerak", "USD",
                             "stop", "target"], rows, num=(3, 4, 5, 6, 7, 8, 9)))
        else:
            P.append('<p class="empty">Tidak ada posisi demo terbuka.</p>')
        P.append("</section>")
    coins = s.get("coins") or []
    if coins:
        rows = []
        for c in coins:
            f = c["f"] or {}
            pk, ps = c["pick"], c["pos"]
            pick = (f'{_dir(pk[0])} <span class="mute">{_n(pk[1], 0)}</span>'
                    if pk and pk[0] != "NONE" else '<span class="mute">–</span>')
            reason = (f'<td class="r">{e(pk[2] or "")}</td>' if pk and pk[0] != "NONE"
                      else '<td class="r"></td>')
            pos = (f'{_dir(ps["side"])} {_pnl(ps["move_pct"], 2, "%")}' if ps else '<span class="mute">–</span>')
            rows.append([f"<b>{e(c['coin'])}</b>", _n(c["price"], 4 if (c["price"] or 0) < 10 else 2),
                         _pnl(f.get("ret_1h_pct")), _pnl(f.get("ret_24h_pct")), _pnl(f.get("ret_7d_pct")),
                         _pnl(f.get("ret_24h_vs_universe_pct")), _n(f.get("funding_pct"), 4, True),
                         _pnl(f.get("oi_change_24h_pct")), _n(f.get("ls_ratio")), pick, pos, reason])
        bar = (s.get("last") or [None, None])[1]
        P.append(f'<section><h2>Sembilan koin</h2><p class="lede">Bar {_t(bar)} UTC, angka yang sama '
                 'dengan yang dilihat Claude. Return dalam persen.</p><div class="panel">' + _table(
                     ["koin", "harga", "1 j", "24 j", "7 h", "vs rata 24 j", "funding", "OI 24 j",
                      "L/S", "pick Claude", "posisi demo", "alasan"], rows,
                     num=(1, 2, 3, 4, 5, 6, 7, 8)) + "</div></section>")
    if bk:
        rows = [[f"<b>{e(p['coin'])}</b>", _dir(p["side"]), _t(p["closed_bar"]), e(str(p["outcome"])),
                 _pnl(p["pnl_pct"], 2, "%"), _pnl(p["pnl_usd"], 2)] for p in bk["closed"]]
        P.append('<section><h2>Riwayat akun</h2><div class="cols"><div><h3>Ekuitas terealisasi (USD)</h3>'
                 + chart([{"n": "", "c": "var(--ink)", "p": bk["curve"]}], width=560, height=190,
                         y_fmt="{:,.0f}", label="ekuitas akun demo scanner") + "</div><div>"
                 '<h3>Ditutup terakhir</h3>' + (_table(["koin", "arah", "ditutup", "hasil", "P&L", "USD"],
                                                       rows, num=(4, 5)) if rows else
                                                '<p class="empty">Belum ada posisi yang ditutup.</p>')
                 + "</div></div></section>")
    lastc = s["last"]
    P.append('<section><h2>Uji H-SC</h2><p class="lede">Apakah koin pilihan Claude lebih baik dari '
             'koin acak ke arah yang sama. Verdict baru keluar setelah 90 hari dan 300 pick; '
             'sebelum itu angka hanya deskriptif.</p><dl class="kv">'
             f'<div><dt>Pick</dt><dd>{s["n_picks"]}<small>dari 300</small></dd></div>'
             f'<div><dt>Hari dengan pick</dt><dd>{s["days"]}<small>dari 90</small></dd></div>'
             f'<div><dt>Siklus</dt><dd>{s["cycles"]}<small>{s["errors"]} error</small></dd></div>'
             f'<div><dt>Biaya scanner</dt><dd>${s["usd"]:.2f}</dd></div>'
             f'<div><dt>Bar terakhir</dt><dd>{_t(lastc[1]) if lastc else "–"}</dd></div>'
             "</dl></section>")
    return "".join(P)


def render(f):
    now = f["now"]
    h, d, c, s = f.get("host") or {}, f.get("db") or {}, f.get("cost"), f.get("scanner")
    man, macro = f.get("manifest") or {}, f.get("macro")
    last = d.get("last") or {}
    probs = issues(f)
    P = [f"<!doctype html><html lang=id><head><meta charset=utf-8>"
         f"<meta name=viewport content='width=device-width,initial-scale=1'>"
         f"<meta http-equiv=refresh content={REFRESH_S}><title>SpotSignal VPS</title>"
         f"<link rel=preconnect href=https://fonts.gstatic.com crossorigin>"
         f"<link rel=stylesheet href='{FONTS}'><style>{CSS}</style></head><body><main>"]

    # ── Status
    P.append(f'<div class="top"><h1>SpotSignal · paper run di VPS</h1>'
             f'<span class="stamp">Diperbarui {now:%d %b %H:%M} UTC, muat ulang otomatis tiap '
             f'{REFRESH_S // 60} menit</span></div>')
    if probs:
        P.append(f'<p class="verdict bad">{len(probs)} hal perlu dicek: {e("; ".join(probs))}.</p>')
    else:
        P.append('<p class="verdict">Semua berjalan normal.</p>')
    n_open = len(d.get("open") or [])
    nxt = (macro or [None])[0] if macro else None
    sub = [f"Bot BTC: {n_open} posisi terbuka." if n_open else "Bot BTC: tidak ada posisi terbuka."]
    bk = (s or {}).get("book")
    if bk:
        sub.append(f"Akun demo scanner: ${_n(bk['equity'])} "
                   f"({_n((bk['equity'] / bk['start'] - 1) * 100, 2, True)}%), {len(bk['open'])} posisi.")
    if nxt:
        sub.append(f"Agenda makro berikutnya: {e(nxt['event'] or '')} dalam {_until(now, nxt['t'])}.")
    P.append(f'<p class="sub">{" ".join(sub)}</p>')
    for err in f["errors"]:
        P.append(f'<div class="err">Panel tidak terbaca: {e(err)}</div>')
    pills = []
    for name, st in (h.get("services") or {}).items():
        extra = f" sejak {_ago(now, h.get('bot_since'))}" if name == "bot" and st == "active" and h.get("bot_since") else ""
        pills.append(("" if st == "active" else "bad", f"{name}", f"{st}{extra}"))
    age = d.get("last_cycle_age_min")
    pills.append(("" if age is not None and age <= MAX_CYCLE_AGE_MIN else "bad", "siklus",
                  "–" if age is None else f"{age:.0f} mnt lalu"))
    blind = d.get("futures_blind_24h")
    pills.append(("" if blind == 0 else "warn", "data futures",
                  "lengkap 24 jam" if blind == 0 else f"{blind} siklus kosong 24 jam" if blind else "–"))
    mem, disk = h.get("mem_available_mb"), h.get("disk_used_pct")
    pills.append(("" if mem and mem >= MIN_MEM_MB else "bad" if mem is not None else "idle", "RAM",
                  f"{mem:.0f} MB bebas" if mem is not None else "–"))
    pills.append(("" if disk is not None and disk <= MAX_DISK_PCT else "bad" if disk is not None else "idle",
                  "disk", f"{disk:.0f}%" if disk is not None else "–"))
    ba = h.get("backup_age_h")
    pills.append(("" if ba is not None and ba <= MAX_BACKUP_AGE_H else "warn" if ba is not None else "idle",
                  "backup", f"{ba:.0f} jam lalu" if ba is not None else "–"))
    le = h.get("log_errors")
    pills.append(("" if not le else "warn", "log", "bersih" if le == 0 else f"{le} baris error" if le else "–"))
    P.append('<ul class="pills">' + "".join(
        f'<li><span class="dot {cls}" aria-hidden="true"></span>{e(k)} <b>{e(v)}</b></li>'
        for cls, k, v in pills) + "</ul>")
    P.append(_overview(s, d))
    P.append('<nav class="tabs" role="tablist" aria-label="Bagian dashboard">'
             '<button role="tab" id="t-scanner" aria-controls="tab-scanner" data-tab="scanner">Scanner 9 koin</button>'
             '<button role="tab" id="t-btc" aria-controls="tab-btc" data-tab="btc">Bot BTC</button>'
             '<button role="tab" id="t-sistem" aria-controls="tab-sistem" data-tab="sistem">Sistem &amp; biaya</button>'
             '</nav>')
    P.append('<div class="tabpanel" role="tabpanel" id="tab-scanner" aria-labelledby="t-scanner">'
             + _scanner_tab(s) + '</div><div class="tabpanel" role="tabpanel" id="tab-btc" aria-labelledby="t-btc">')

    # ── Hero: distance to a signal
    P.append('<section><h2>Seberapa dekat ke sinyal</h2><p class="lede">Skor siklus terakhir '
             'dibanding ambangnya. Sinyal menyala saat salah satu skor melewati garis, lalu masih '
             'harus lolos gate sebelum posisi dibuka.</p><div class="rails panel">'
             + _rail("futures", last.get("futures")) + _rail("spot", last.get("spot")) + "</div>")
    sc = d.get("scores") or {}
    blocks = []
    for m in ("futures", "spot"):
        rows = sc.get(m) or []
        ser = [{"n": "BUY", "c": "var(--buy)", "p": [(_dt(r[0]).timestamp(), r[1] or 0) for r in rows]},
               {"n": "SELL", "c": "var(--sell)", "p": [(_dt(r[0]).timestamp(), r[2] or 0) for r in rows]},
               {"n": "ambang", "c": "var(--mute)", "dash": True,
                "p": [(_dt(r[0]).timestamp(), r[3] or 0) for r in rows]}]
        blocks.append(f'<div><h3>{m.capitalize()}, 72 jam</h3>'
                      + chart(ser, width=560, height=200, label=f"skor {m} 72 jam") + "</div>")
    P.append('<div class="legend" style="margin-top:18px"><span><i style="background:var(--buy)"></i>skor BUY</span>'
             '<span><i style="background:var(--sell)"></i>skor SELL</span>'
             '<span><i style="background:var(--mute)"></i>ambang</span></div>'
             f'<div class="cols">{"".join(blocks)}</div></section>')

    # ── Positions
    P.append('<section><h2>Posisi</h2>')
    if d.get("open"):
        rows = [[f"#{p['id']}", e(p["mode"]), _dir(p["type"]), _t(p["opened_at"]),
                 _n(p["hours"], 0, suffix=" j"), _n(p["entry_price"]), _n(p["price"]),
                 _pnl(p["move_pct"], 2, "%"), _n(p["stop"]), _pnl(p["to_stop_pct"], 2, "%"),
                 _n(p["take_profit"]), "ya" if p["partial_closed"] else "belum"]
                for p in d["open"]]
        P.append('<div class="panel">' + _table(
            ["id", "mode", "arah", "dibuka", "umur", "entry", "harga", "gerak", "stop",
             "ke stop", "TP1", "TP1 kena"], rows, num=(4, 5, 6, 7, 8, 9, 10)) +
            '<p class="note">Gerak dihitung kasar dari harga siklus terakhir, belum termasuk biaya.</p></div>')
    else:
        lc = (d.get("closed_recent") or [None])[0]
        tail = (f" Terakhir ditutup: #{lc['id']} {lc['mode']} {lc['type']}, {lc['outcome']}, "
                f"{_n(lc['pnl_pct'], 2, True)}% ({_ago(now, _dt(lc['closed_at']))}).") if lc else ""
        P.append(f'<p class="empty">Tidak ada posisi terbuka.{e(tail)}</p>')
    ac = d.get("account")
    if ac:
        ret = (ac["equity"] / ac["start"] - 1) * 100
        kv = [("Ekuitas", f'${_n(ac["equity"])}<small>{_pnl(ret, 2, "%")}</small>'),
              ("Terealisasi", _pnl(ac["realised"] - ac["start"], 2, " USD")),
              ("Mengambang", _pnl(ac["upnl"], 2, " USD")),
              ("Kas bebas", f'${_n(ac["cash"])}<small>${_n(ac["used"])} di posisi</small>')]
        P.append('<h3 style="margin-top:22px">Akun $1.000</h3><p class="note" style="margin-top:0">'
                 'Posisi bot yang sama, diterjemahkan ke dolar dengan aturan akun scanner: tiap posisi '
                 '10% dari ekuitas terealisasi saat dibuka, spot dan futures dalam satu akun. Hanya '
                 'tampilan; bot tidak diubah.</p><div class="cols"><div><dl class="kv">'
                 + "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in kv) + "</dl></div><div>"
                 + chart([{"n": "", "c": "var(--ink)", "p": ac["curve"]}], width=560, height=170,
                         y_fmt="{:,.0f}", label="ekuitas akun BTC") + "</div></div>")
    ca = d.get("claude_account")
    if ca:
        ret = (ca["equity"] / ca["start"] - 1) * 100
        base = (ac["equity"] / ac["start"] - 1) * 100 if ac else None
        kv = [("Ekuitas", f'${_n(ca["equity"])}<small>{_pnl(ret, 2, "%")}</small>'),
              ("Tanpa filter", f'${_n(ac["equity"])}<small>{_pnl(base, 2, "%")}</small>' if ac else "–"),
              ("Posisi diambil", f'{ca["kept"]}<small>{_pnl(ca["kept_pnl"], 2, " pp")}</small>'),
              ("Posisi dibuang", f'{ca["vetoed"]}<small>{_pnl(ca["vetoed_pnl"], 2, " pp")}</small>'),
              ("Data baru: diambil", f'{ca["fresh_kept"]}<small>{_pnl(ca["fresh_kept_pnl"], 2, " pp")}</small>'),
              ("Data baru: dibuang", f'{ca["fresh_vetoed"]}<small>{_pnl(ca["fresh_vetoed_pnl"], 2, " pp")}</small>')]
        rows = [[f"#{c['id']}", e(c["mode"]), _dir(c["type"]), _t(c["closed_at"]), e(str(c["outcome"])),
                 _pnl(c["pnl_pct"], 2, "%")] for c in ca["vetoed_rows"]]
        P.append('<h3 style="margin-top:26px">Akun $1.000 dengan filter Claude</h3>'
                 '<p class="note" style="margin-top:0">Posisi bot yang sama, tapi yang sinyal pembukanya '
                 'ditolak Claude (DISAGREE) dibuang; tanpa opini tetap diambil. Hanya tampilan. Posisi '
                 f'sebelum {CLAUDE_FRESH_FROM:%d %b %H:%M} UTC sudah dipakai saat H-S dinilai, jadi hanya '
                 '"data baru" yang merupakan bukti segar. Posisi yang dibuang mungkin akan diganti sinyal '
                 'lain di dunia nyata; itu tidak terlihat di sini.</p>'
                 '<div class="cols"><div><dl class="kv">'
                 + "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in kv) + "</dl></div><div>"
                 + chart([{"n": "filter Claude", "c": "var(--ink)", "p": ca["curve"]},
                          {"n": "tanpa filter", "c": "var(--mute)", "dash": True,
                           "p": ac["curve"] if ac else []}],
                         width=560, height=170, y_fmt="{:,.0f}", label="ekuitas dengan dan tanpa filter Claude")
                 + '<div class="legend"><span><i style="background:var(--ink)"></i>filter Claude</span>'
                   '<span><i style="background:var(--mute)"></i>tanpa filter</span></div></div></div>'
                 + ('<details><summary>Posisi yang dibuang Claude</summary>' + _table(
                     ["id", "mode", "arah", "ditutup", "hasil", "P&L"], rows, num=(5,)) + "</details>"
                    if rows else ""))
    since = man.get("started_at")
    P.append(f'<h3 style="margin-top:22px">Kinerja buku{f" sejak run dimulai {_t(since)}" if since else ""}</h3>'
             + _book_table(d.get("totals") or {}))
    curves = []
    for m in ("futures", "spot"):
        pts = (d.get("curve") or {}).get(m) or []
        curves.append(f'<div><h3>{m.capitalize()}: P&amp;L kumulatif (pp)</h3>'
                      + chart([{"n": "", "c": "var(--ink)", "p": pts}], width=560, height=190, zero=True,
                              y_fmt="{:+.1f}", label=f"P&L kumulatif {m}") + "</div>")
    P.append(f'<div class="cols" style="margin-top:18px">{"".join(curves)}</div>')

    prices = d.get("prices") or []
    marks = []
    for p in d.get("marks") or []:
        col = "var(--buy)" if p["type"] == "BUY" else "var(--sell)"
        o = _dt(p["opened_at"])
        if o and p["entry_price"]:
            marks.append((o.timestamp(), p["entry_price"], col, True,
                          f"#{p['id']} {p['mode']} {p['type']} dibuka {_n(p['entry_price'])}"))
        cdt = _dt(p.get("closed_at"))
        if cdt and p.get("exit_price"):
            marks.append((cdt.timestamp(), p["exit_price"], col, False,
                          f"#{p['id']} ditutup {p['outcome']} {_n(p['pnl_pct'], 2, True)}%"))
    t_range = None
    if prices:
        t_range = (_dt(prices[0][0]).timestamp(), _dt(prices[-1][0]).timestamp())
        marks = [m for m in marks if t_range[0] <= m[0] <= t_range[1]]
    P.append('<h3 style="margin-top:22px">BTC 7 hari, dengan entri (titik penuh) dan exit (titik kosong)</h3>'
             + chart([{"n": "", "c": "var(--ink2)", "p": [(_dt(t).timestamp(), px) for t, px in prices]}],
                     height=240, y_fmt="{:,.0f}", markers=marks, t_range=t_range, label="harga BTC 7 hari"))
    rows = [[f"#{p['id']}", e(p["mode"]), _dir(p["type"]), _t(p["opened_at"]), _t(p["closed_at"]),
             e(str(p["outcome"])), _n(p["entry_price"]), _n(p["exit_price"]), _pnl(p["pnl_pct"], 2, "%")]
            for p in d.get("closed_recent") or []]
    P.append('<details open><summary>Posisi tertutup terakhir</summary>' + _table(
        ["id", "mode", "arah", "dibuka", "ditutup", "hasil", "entry", "exit", "P&L"], rows,
        num=(6, 7, 8)) + "</details></section>")

    # ── Market
    fu = last.get("futures") or {}
    htf = fu.get("htf") or {}
    ema = ((fu["price"] - fu["ema200"]) / fu["ema200"] * 100) if fu.get("price") and fu.get("ema200") else None
    chg = lambda v, c, nd=2: f"{_n(v, nd)}<small>{_n(c, 2, True, '%')}</small>" if v is not None else "–"
    kv = [("Funding", _n(fu.get("funding"), 4, True, "%")), ("Basis", _n(fu.get("basis"), 3, True, "%")),
          ("Long/short akun", _n(fu.get("ls"))), ("Open interest", _n(fu.get("oi"), 2, True, "%")),
          ("Taker beli/jual", _n(fu.get("taker"), 3)), ("Fear & Greed", _n(fu.get("fng"), 0)),
          ("RSI 1j", _n(fu.get("rsi"), 1)), ("Jarak EMA200 1j", _n(ema, 2, True, "%")),
          ("ATR 1j", _n(fu.get("atr"), 0)),
          ("Tren 4j / 1h", f"{e(str(htf.get('4h', '–')))} / {e(str(htf.get('1d', '–')))}"),
          ("DXY", chg(fu.get("dxy"), fu.get("dxy_chg"))),
          ("S&amp;P 500", chg(fu.get("sp"), fu.get("sp_chg"), 0)),
          ("VIX", chg(fu.get("vix"), fu.get("vix_chg"))),
          ("Emas", chg(fu.get("gold"), fu.get("gold_chg"), 0)),
          ("Dominasi BTC", _n(fu.get("btc_dom"), 1, suffix="%")),
          ("Stablecoin", _n(fu.get("stable"), 1, suffix=" mlr")), ("Sentimen berita", e(str(fu.get("news") or "–")))]
    P.append(f'<section><h2>Pasar saat siklus futures terakhir</h2><p class="lede">{_t(fu.get("ts"))} UTC. '
             'Angka yang sama dengan yang dibaca engine.</p><div class="panel"><dl class="kv">'
             + "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in kv) + "</dl></div>")
    reasons = fu.get("reasons") or []
    sp_reasons = (last.get("spot") or {}).get("reasons") or []
    P.append('<div class="cols" style="margin-top:20px">')
    P.append('<div><h3>Alasan skor futures</h3>' + ('<ul class="reasons">' + "".join(
        f"<li>{e(r)}</li>" for r in reasons) + "</ul>" if reasons else '<p class="empty">–</p>') + "</div>")
    P.append('<div><h3>Alasan skor spot</h3>' + ('<ul class="reasons">' + "".join(
        f"<li>{e(r)}</li>" for r in sp_reasons) + "</ul>" if sp_reasons else '<p class="empty">–</p>') + "</div>")
    P.append("</div></section>")

    # ── Macro + gates
    P.append('<section class="cols">')
    if macro is not None:
        items = []
        for ev in macro:
            soon = 0 <= (ev["t"] - now).total_seconds() <= MACRO_CLOSE_H * 3600
            fc = f"perkiraan {ev['forecast']}, sebelumnya {ev['previous']}" if ev.get("forecast") not in (None, "", "N/A") else ""
            items.append(f'<li class="{"soon" if soon else ""}"><span class="when">{_until(now, ev["t"])}</span>'
                         f'<span>{e(ev["event"] or "")}<br><span class="mute">{ev["t"]:%d %b %H:%M} UTC'
                         f'{" · " + e(fc) if fc else ""}{" · bot menutup posisi & menahan sinyal" if soon else ""}'
                         f'</span></span></li>')
        P.append('<div><h2>Agenda makro</h2><p class="lede">Event USD berdampak tinggi. Dua jam '
                 'sebelumnya bot menahan sinyal dan menutup posisi.</p>'
                 + (f'<ul class="agenda">{"".join(items)}</ul>' if items else '<p class="empty">Tidak ada event terjadwal.</p>')
                 + "</div>")
    gb = d.get("blocks") or {}
    blk = []
    for m in ("futures", "spot"):
        rows = gb.get(m) or []
        top = max([r[2] for r in rows] or [1])
        blk.append(f'<h3 style="margin-top:12px">{m.capitalize()}</h3>' + (
            '<div class="bars"><span class="h">gate</span><span class="h">7 hari</span>'
            '<span class="h n">24 j</span><span class="h n">7 h</span>'
            + "".join(f'<span>{e(g)}</span><div class="b" style="width:{n7 / top * 100:.0f}%"></div>'
                      f'<span class="n" style="text-align:right">{n24}</span>'
                      f'<span style="text-align:right">{n7}</span>' for g, n24, n7 in rows) + "</div>"
            if rows else '<p class="empty">Tidak ada sinyal diblokir 7 hari terakhir.</p>'))
    P.append('<div><h2>Sinyal yang diblokir gate</h2><p class="lede">Sinyal yang lolos ambang tapi '
             'ditahan sebelum posisi dibuka.</p>' + "".join(blk) + "</div></section>")

    # ── Claude
    labels = {"entry": ("Opini atas sinyal", "AGREE/DISAGREE per sinyal yang menyala (H-S)"),
              "exit": ("Opini atas posisi terbuka", "CLOSE/HOLD tiap jam selama posisi terbuka (H-X)"),
              "flip": ("Opini sebelum flip", "FLIP/HOLD saat sinyal berlawanan muncul (H-FLP-C)")}
    P.append('<section><h2>Opini Claude atas bot BTC</h2><p class="lede">Semua opini dicatat saja, tidak pernah '
             'mengubah trade.</p><div class="cols3">')
    for key, data in (d.get("opinions") or {}).items():
        title, what = labels[key]
        stats = data["stats"]
        st = "".join(f'<p class="big">{x["n"]}<span class="mute" style="font:400 14px Barlow"> opini 7 hari, '
                     f'{x["provider"]}</span></p><p class="note">'
                     + e(", ".join(f"{k} {v}" for k, v in sorted(x["verdicts"].items())))
                     + (f' · <span class="neg">{x["errors"]} error</span>' if x["errors"] else "")
                     + "</p>" for x in stats) or '<p class="empty">Tidak ada opini 7 hari terakhir.</p>'
        rows = [[_t(ts), e(str(m)), _dir(side), e(str(v)) if v else '<span class="neg">error</span>',
                 _n(conf, 0), f'<td class="r">{e(str(err if err else r or ""))}</td>']
                for ts, m, side, p, v, conf, r, err in data["rows"]]
        P.append(f'<div class="panel"><h3>{title}</h3><p class="note" style="margin-top:0">{what}</p>{st}'
                 f'<details><summary>{len(rows)} opini terakhir</summary>'
                 + _table(["waktu", "mode", "arah", "verdict", "yakin", "alasan"], rows, num=(4,))
                 + "</details></div>")
    P.append("</div></section>")

    # ── Cost + run
    P.append('</div><div class="tabpanel" role="tabpanel" id="tab-sistem" aria-labelledby="t-sistem">')
    P.append('<section class="cols">')
    if c:
        bud = c.get("budget")
        over = bud and c["llm_usd_projected_month"] > bud
        prov = ", ".join(f"{p} ${v['usd']:.2f}" for p, v in (c.get("llm_cost_mtd") or {}).items())
        P.append('<div><h2>Biaya API</h2><dl class="kv">'
                 f'<div><dt>24 jam</dt><dd>${c["llm_usd_24h"]:.2f}</dd></div>'
                 f'<div><dt>Bulan ini</dt><dd>${c["llm_usd_mtd"]:.2f}</dd></div>'
                 f'<div><dt>Proyeksi akhir bulan</dt><dd class="{"neg" if over else ""}">'
                 f'${c["llm_usd_projected_month"]:.2f}{f"<small>dari ${bud:.0f}</small>" if bud else ""}</dd></div>'
                 f'</dl><p class="note">{e(prov)}. Angka yang sama dengan laporan ops harian.</p></div>')
    meta = [("Run dimulai", f"{_t(man.get('started_at'))} UTC"),
            ("Futures dihitung sejak", f"{_t(man.get('futures_restart_at'))} UTC"),
            ("Commit di VPS", h.get("git_head") or "–"), ("Commit saat run dimulai", man.get("git_sha") or "–"),
            ("Bot berjalan sejak", f"{_t(h.get('bot_since'))} UTC" if h.get("bot_since") else "–"),
            ("Siklus 24 jam", _n(d.get("cycles_24h"), 0)), ("Database", f"{d.get('db_mb', '–')} MB"),
            ("Backup terakhir", e(h.get("backup_latest") or "–"))]
    P.append('<div><h2>Run</h2><div class="meta">' + "".join(
        f"<div>{k}: <b>{v}</b></div>" for k, v in meta) + "</div></div></section>")
    P.append(f"</div></main><script>{JS}</script></body></html>")
    return "".join(P)


# ── Server ──────────────────────────────────────────────────────────────────────────

def make_handler(root, db, scanner_db):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path.split("?")[0] == "/healthz":
                body, ctype = b"ok", "text/plain"
            elif self.path.split("?")[0] == "/":
                body = render(collect(root, db, scanner_db)).encode()
                ctype = "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # quiet: one line per request would flood the journal
            pass
    return H


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--db", help="default <root>/data/signal_history.db")
    ap.add_argument("--scanner-db", help="default <root>/data/scanner.db")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--once", action="store_true", help="print the page to stdout and exit")
    args = ap.parse_args(argv)
    root = Path(args.root)
    from dotenv import load_dotenv
    load_dotenv(root / ".env")                  # LLM_MONTHLY_BUDGET_USD only
    db = Path(args.db or root / "data" / "signal_history.db")
    sdb = Path(args.scanner_db or root / "data" / "scanner.db")
    if args.once:
        sys.stdout.write(render(collect(root, db, sdb)))
        return 0
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(root, db, sdb))
    print(f"dashboard on http://127.0.0.1:{args.port} (db {db})", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())

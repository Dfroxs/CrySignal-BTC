"""Read-only web dashboard for the VPS: health, positions, signals, gates, Claude, cost.

Binds to 127.0.0.1 only, so nothing is reachable from the internet. Open it through an SSH
tunnel from the owner's machine:

    ssh -N -L 8080:localhost:8080 dmonk@<vps>       # then browse http://localhost:8080

Every database is opened `mode=ro`; the page writes nothing, sends nothing, asks no model
and imports nothing from the trading path, so running it changes no paper-run window.
Standard library only (http.server), sized for the 1 GB host. The page reloads itself
every REFRESH_S seconds.

USER unit (no sudo), as spotsignal-qa:
    cp deploy/spotsignal-dash.service ~/.config/systemd/user/
    systemctl --user daemon-reload && systemctl --user enable --now spotsignal-dash

    python3 -m agents.dashboard --db data/snap.db --port 8080     # locally, on a pulled copy
"""
from __future__ import annotations

import argparse
import html
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFRESH_S = 300
CLOSED_ROWS = 15
OPINION_ROWS = 6
SERVICES = ("spotsignal", "spotsignal-qa")      # system unit, user unit
e = html.escape


def _ts(col):
    """The DB holds both 'YYYY-MM-DD HH:MM:SS' and ISO with offset; compare on the first 19."""
    return f"datetime(substr({col}, 1, 19))"


def _ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)


def _tables(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


# ── Facts ───────────────────────────────────────────────────────────────────────────

def host_facts(root, run=None):
    from agents.alarm import _run, collect_host_facts
    run = run or _run
    h = collect_host_facts(run=run)
    svc = {"spotsignal": h["spot_svc"],
           "spotsignal-qa": run("systemctl --user is-active spotsignal-qa") or "unknown"}
    backups = sorted((root / "data" / "backups").glob("db-*.db"), key=lambda p: p.stat().st_mtime)
    return {"services": svc, "mem_available_mb": h["mem_available_mb"],
            "disk_used_pct": h["disk_used_pct"],
            "backup_latest": backups[-1].stem.removeprefix("db-") if backups else None}


def db_facts(db, now):
    """Everything the page shows from signal_history.db, as plain data."""
    con = _ro(db)
    try:
        q = lambda sql, *p: con.execute(sql, p).fetchall()
        tables = _tables(con)
        f = {"db_mb": round(Path(db).stat().st_size / 1e6, 1)}
        last = {}
        for mode in ("spot", "futures"):
            r = q(f"SELECT {_ts('timestamp')}, type, price, strength, threshold, gap_to_fire, "
                  f"funding_rate, basis_pct, open_positions FROM cycle_log WHERE mode=? "
                  f"ORDER BY {_ts('timestamp')} DESC, id DESC LIMIT 1", mode)
            if r:
                k = ("ts", "type", "price", "strength", "threshold", "gap", "funding", "basis", "open")
                last[mode] = dict(zip(k, r[0]))
        f["last"] = last
        ts = [v["ts"] for v in last.values()]
        f["last_cycle_age_min"] = (round((now.replace(tzinfo=None) - datetime.fromisoformat(max(ts)))
                                         .total_seconds() / 60, 1) if ts else None)
        d24 = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        f["cycles_24h"] = q(f"SELECT COUNT(*) FROM cycle_log WHERE {_ts('timestamp')} >= ?", d24)[0][0]
        f["futures_blind_24h"] = q(f"SELECT COUNT(*) FROM cycle_log WHERE mode='futures' AND "
                                   f"funding_rate = 0 AND {_ts('timestamp')} >= ?", d24)[0][0]
        cols = ("id", "mode", "type", "entry_price", "stop_loss", "trailing_stop", "take_profit",
                "opened_at", "partial_closed")
        f["open"] = [dict(zip(cols, r)) for r in q(
            f"SELECT {', '.join(cols)} FROM paper_positions WHERE closed_at IS NULL ORDER BY id")]
        for p in f["open"]:
            px = (last.get(p["mode"]) or {}).get("price")
            p["price"] = px
            p["move_pct"] = (((px - p["entry_price"]) / p["entry_price"] * 100) *
                             (1 if p["type"] == "BUY" else -1)) if px and p["entry_price"] else None
        cols = ("id", "mode", "type", "entry_price", "exit_price", "opened_at", "closed_at",
                "outcome", "pnl_pct")
        closed = [dict(zip(cols, r)) for r in q(
            f"SELECT {', '.join(cols)} FROM paper_positions WHERE closed_at IS NOT NULL "
            f"ORDER BY {_ts('closed_at')}")]
        f["closed_recent"] = closed[::-1][:CLOSED_ROWS]
        f["curve"] = {}
        f["totals"] = {}
        for mode in ("spot", "futures"):
            rows = [c for c in closed if c["mode"] == mode and c["pnl_pct"] is not None]
            acc, pts = 0.0, []
            for c in rows:
                acc += c["pnl_pct"]
                pts.append((c["closed_at"][:19], round(acc, 3)))
            f["curve"][mode] = pts
            wins = sum(c["pnl_pct"] > 0 for c in rows)
            f["totals"][mode] = {"n": len(rows), "sum": round(acc, 2),
                                 "win": round(wins / len(rows) * 100) if rows else None}
        f["blocks_24h"] = q(f"SELECT mode, gate, COUNT(*) FROM signal_blocks WHERE "
                            f"{_ts('timestamp')} >= ? GROUP BY mode, gate ORDER BY 3 DESC", d24) \
            if "signal_blocks" in tables else []
        op = {}
        for name, sql in (
                ("entry", "SELECT timestamp, mode, signal_type, provider, verdict, "
                          "opinion_confidence, reason, error FROM shadow_opinions"),
                ("exit", "SELECT timestamp, mode, side, provider, verdict, opinion_confidence, "
                         "reason, error FROM shadow_exit_opinions"),
                ("flip", "SELECT timestamp, 'futures', side, provider, verdict, "
                         "opinion_confidence, reason, error FROM shadow_flip_opinions")):
            table = sql.rsplit(" ", 1)[1]
            if table in tables:
                op[name] = q(f"{sql} ORDER BY id DESC LIMIT {OPINION_ROWS}")
        f["opinions"] = op
        return f
    finally:
        con.close()


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
        return {"cycles": n, "errors": errs or 0, "usd": usd or 0.0, "last": last,
                "picks": picks, "days": days, "n_picks": n_picks}
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
    for key, fn in (("host", lambda: host_facts(root, run)), ("db", lambda: db_facts(db, now)),
                    ("scanner", lambda: scanner_facts(scanner_db)),
                    ("cost", lambda: cost_facts(db, root, now))):
        try:
            out[key] = fn()
        except Exception as exc:  # noqa: BLE001 - a broken panel must not blank the page
            out[key] = None
            out["errors"].append(f"{key}: {type(exc).__name__}: {exc}")
    return out


# ── Render ──────────────────────────────────────────────────────────────────────────

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1b1f24;--mute:#667085;--line:#e4e7ec;
--ok:#12805c;--bad:#c4320a;--warn:#b54708;--spot:#3b6fd8;--fut:#b4499b}
@media (prefers-color-scheme:dark){:root{--bg:#0f1216;--card:#171b21;--fg:#e6e8eb;
--mute:#98a2b3;--line:#2a3039;--ok:#3ccb8f;--bad:#f97066;--warn:#fdb022;--spot:#7aa2f7;--fut:#e58fd1}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:16px}
h1{font-size:18px;margin:0 0 2px}h2{font-size:14px;margin:0 0 10px;color:var(--mute);
text-transform:uppercase;letter-spacing:.04em}
.sub{color:var(--mute);font-size:12px;margin-bottom:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;overflow-x:auto}
.wide{grid-column:1/-1}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px}
.tile{border:1px solid var(--line);border-radius:8px;padding:8px 10px}
.tile b{display:block;font-size:18px;font-variant-numeric:tabular-nums}
.tile span{color:var(--mute);font-size:12px}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--mute);font-weight:500;font-size:12px}td.r{white-space:normal;min-width:220px}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}.mute{color:var(--mute)}
.err{background:var(--bad);color:#fff;border-radius:8px;padding:8px 12px;margin-bottom:12px}
svg{width:100%;height:auto}
"""


def _n(v, nd=2, sign=False):
    if v is None:
        return "–"
    return f"{v:+,.{nd}f}" if sign else f"{v:,.{nd}f}"


def _pnl(v, nd=2):
    cls = "mute" if v is None else ("ok" if v > 0 else "bad" if v < 0 else "mute")
    return f'<span class="{cls}">{_n(v, nd, True)}</span>'


def _t(raw):
    return e(str(raw or "–")[:16].replace("T", " "))


def _table(head, rows):
    h = "".join(f"<th>{e(x)}</th>" for x in head)
    b = "".join("<tr>" + "".join(rows_i) + "</tr>" for rows_i in rows)
    return f"<table><tr>{h}</tr>{b or '<tr><td class=mute>(kosong)</td></tr>'}</table>"


def _td(v, cls=""):
    return f'<td class="{cls}">{v}</td>'


def _curve_svg(curve):
    """Cumulative closed P&L per mode, one inline SVG; points in closing order."""
    series = {m: pts for m, pts in curve.items() if pts}
    if not series:
        return '<p class="mute">Belum ada posisi tertutup.</p>'
    allx = sorted({p[0] for pts in series.values() for p in pts})
    pos = {x: i for i, x in enumerate(allx)}
    ys = [0.0] + [p[1] for pts in series.values() for p in pts]
    lo, hi = min(ys), max(ys)
    span = (hi - lo) or 1.0
    W, H, P = 1000, 220, 30
    X = lambda x: P + pos[x] / max(len(allx) - 1, 1) * (W - 2 * P)
    Y = lambda y: H - P - (y - lo) / span * (H - 2 * P)
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Kurva P&L kumulatif">',
           f'<line x1="{P}" x2="{W - P}" y1="{Y(0):.1f}" y2="{Y(0):.1f}" stroke="var(--line)"/>',
           f'<text x="4" y="{Y(hi) + 4:.0f}" font-size="12" fill="var(--mute)">{hi:+.1f}</text>',
           f'<text x="4" y="{Y(lo) + 4:.0f}" font-size="12" fill="var(--mute)">{lo:+.1f}</text>']
    for m, pts in series.items():
        col = "var(--spot)" if m == "spot" else "var(--fut)"
        d = " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in pts)
        out.append(f'<polyline fill="none" stroke="{col}" stroke-width="2.5" points="{d}"/>')
        x, y = pts[-1]
        out.append(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="4" fill="{col}"/>')
        near_end = X(x) > W - 160               # label left of the point, inside the frame
        out.append(f'<text x="{X(x) + (-8 if near_end else 8):.0f}" y="{Y(y) - 8:.0f}" '
                   f'font-size="13" text-anchor="{"end" if near_end else "start"}" '
                   f'fill="{col}">{m} {y:+.2f}pp</text>')
    out.append("</svg>")
    return "".join(out)


def render(f):
    now = f["now"]
    parts = [f"<!doctype html><html lang=id><head><meta charset=utf-8>"
             f"<meta name=viewport content='width=device-width,initial-scale=1'>"
             f"<meta http-equiv=refresh content={REFRESH_S}><title>SpotSignal VPS</title>"
             f"<style>{CSS}</style></head><body><main>",
             "<h1>SpotSignal · VPS</h1>",
             f"<div class=sub>{now:%Y-%m-%d %H:%M} UTC · paper only · refresh tiap "
             f"{REFRESH_S // 60} menit · read-only</div>"]
    for err in f["errors"]:
        parts.append(f"<div class=err>{e(err)}</div>")
    h, d, c, s = f.get("host") or {}, f.get("db") or {}, f.get("cost"), f.get("scanner")

    # Health
    tiles = []
    for name, st in (h.get("services") or {}).items():
        tiles.append((name, st, "ok" if st == "active" else "bad"))
    age = d.get("last_cycle_age_min")
    tiles.append(("siklus terakhir", "–" if age is None else f"{age:.0f} mnt lalu",
                  "ok" if age is not None and age <= 75 else "bad"))
    blind = d.get("futures_blind_24h")
    tiles.append(("futures buta 24j", _n(blind, 0) if blind is not None else "–",
                  "ok" if blind == 0 else "warn"))
    tiles.append(("siklus 24j", _n(d.get("cycles_24h"), 0), ""))
    mem = h.get("mem_available_mb")
    tiles.append(("RAM tersedia", f"{mem:.0f} MB" if mem is not None else "–",
                  "ok" if mem and mem >= 150 else "bad"))
    disk = h.get("disk_used_pct")
    tiles.append(("disk terpakai", f"{disk:.0f}%" if disk is not None else "–",
                  "ok" if disk is not None and disk <= 85 else "bad"))
    tiles.append(("DB", f"{d.get('db_mb', '–')} MB", ""))
    tiles.append(("backup terakhir", e(h.get("backup_latest") or "–"), ""))
    parts.append('<div class="grid"><section class="card wide"><h2>Kesehatan</h2><div class=tiles>'
                 + "".join(f'<div class=tile><b class="{cls}">{v}</b><span>{e(k)}</span></div>'
                           for k, v, cls in tiles) + "</div></section>")

    # Last signal per mode
    rows = []
    for mode, l in (d.get("last") or {}).items():
        rows.append([_td(e(mode)), _td(_t(l["ts"])), _td(e(str(l["type"]))), _td(_n(l["price"], 2)),
                     _td(f"{_n(l['strength'])} / {_n(l['threshold'])}"), _td(_n(l["gap"], 2, True)),
                     _td(_n(l["funding"], 4) if mode == "futures" else "–"),
                     _td(_n(l["basis"], 4) if mode == "futures" else "–"), _td(_n(l["open"], 0))])
    parts.append('<section class="card wide"><h2>Siklus terakhir</h2>' + _table(
        ["mode", "waktu", "sinyal", "harga", "skor / ambang", "jarak", "funding", "basis", "posisi"],
        rows) + "</section>")

    # Positions
    tot = d.get("totals") or {}
    summ = " · ".join(f"{m}: {t['n']} tertutup, total {_pnl(t['sum'])}pp, menang "
                      f"{t['win'] if t['win'] is not None else '–'}%" for m, t in tot.items())
    rows = [[_td(f"#{p['id']}"), _td(e(p["mode"])), _td(e(p["type"])), _td(_t(p["opened_at"])),
             _td(_n(p["entry_price"])), _td(_n(p["price"])), _td(_pnl(p["move_pct"])),
             _td(_n(p["trailing_stop"] or p["stop_loss"])), _td(_n(p["take_profit"])),
             _td("ya" if p["partial_closed"] else "–")] for p in d.get("open") or []]
    parts.append('<section class="card wide"><h2>Posisi terbuka</h2>' + _table(
        ["id", "mode", "arah", "dibuka", "entry", "harga", "gerak % (kasar)", "stop", "TP1", "TP1 kena"],
        rows) + "</section>")
    parts.append(f'<section class="card wide"><h2>P&L kumulatif (pp, posisi tertutup)</h2>'
                 f'<div class=sub>{summ}</div>{_curve_svg(d.get("curve") or {})}</section>')
    rows = [[_td(f"#{p['id']}"), _td(e(p["mode"])), _td(e(p["type"])), _td(_t(p["closed_at"])),
             _td(e(str(p["outcome"]))), _td(_pnl(p["pnl_pct"]))] for p in d.get("closed_recent") or []]
    parts.append('<section class="card wide"><h2>Posisi tertutup terakhir</h2>' + _table(
        ["id", "mode", "arah", "ditutup", "hasil", "P&L %"], rows) + "</section>")

    rows = [[_td(e(m)), _td(e(g)), _td(_n(n, 0))] for m, g, n in d.get("blocks_24h") or []]
    parts.append('<section class=card><h2>Sinyal diblokir gate, 24 jam</h2>' + _table(
        ["mode", "gate", "jumlah"], rows) + "</section>")

    # Claude
    labels = {"entry": "Opini sinyal (H-S)", "exit": "Opini exit (H-X)", "flip": "Opini flip (H-FLP-C)"}
    for key, rows_raw in (d.get("opinions") or {}).items():
        rows = [[_td(_t(ts)), _td(e(str(m))), _td(e(str(side))), _td(e(str(p))),
                 _td(e(str(v)) if v else '<span class=bad>error</span>'),
                 _td(_n(conf, 0)), _td(e(str(err if err else r or "")), "r")]
                for ts, m, side, p, v, conf, r, err in rows_raw]
        parts.append(f'<section class="card wide"><h2>{labels[key]}</h2>' + _table(
            ["waktu", "mode", "arah", "provider", "verdict", "yakin", "alasan"], rows) + "</section>")

    if s:
        last = s["last"]
        head = (f"bar {_t(last[1])} · {e(str(last[2] or '–'))}"
                + (f' · <span class=bad>{e(last[3])}</span>' if last[3] else "")) if last else "–"
        rows = [[_td(e(cn)), _td(f'<span class="{"ok" if sd == "LONG" else "bad" if sd == "SHORT" else "mute"}">'
                                f'{e(sd)}</span>'), _td(_n(cf, 0)), _td(e(r or ""), "r")]
                for cn, sd, cf, r in s["picks"]]
        parts.append(f'<section class="card wide"><h2>Scanner Claude (H-SC)</h2><div class=sub>{head} · '
                     f'{s["cycles"]} siklus, {s["errors"]} error · {s["n_picks"]} pick / 300, '
                     f'{s["days"]} hari / 90 untuk verdict · ${s["usd"]:.2f}</div>'
                     + _table(["koin", "arah", "yakin", "alasan"], rows) + "</section>")

    if c:
        rows = [[_td(e(p)), _td(f"${v['usd']:.2f}"),
                 _td(f"{v['input_tokens']:,} / {v['output_tokens']:,}")]
                for p, v in (c.get("llm_cost_mtd") or {}).items()]
        bud = c.get("budget")
        parts.append(f'<section class=card><h2>Biaya LLM</h2><div class=tiles>'
                     f'<div class=tile><b>${c["llm_usd_24h"]:.2f}</b><span>24 jam</span></div>'
                     f'<div class=tile><b>${c["llm_usd_mtd"]:.2f}</b><span>bulan ini</span></div>'
                     f'<div class=tile><b class="{"bad" if bud and c["llm_usd_projected_month"] > bud else ""}">'
                     f'${c["llm_usd_projected_month"]:.2f}</b><span>proyeksi'
                     f'{f" / budget ${bud:.0f}" if bud else ""}</span></div></div>'
                     + _table(["provider", "bulan ini", "token in / out"], rows)
                     + '<div class=sub>Dari shadow_opinions dan llm_usage.jsonl, sama dengan laporan ops.</div>'
                     + "</section>")
    parts.append("</div></main></body></html>")
    return "".join(parts)


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

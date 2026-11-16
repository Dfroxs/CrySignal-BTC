"""Multi-coin scanner: once an hour Claude sees numbers for nine USD-M perpetuals and says
LONG, SHORT or NONE for each. Logged, never traded.

Pre-registered as H-SC (`docs/superpowers/specs/2026-11-17-claude-scanner-prereg.md`),
scored by `scripts/scanner_eval.py`. It cannot be backtested: any historical candle sits
inside the model's training data, so only opinions given after deployment count.

A separate process with its own database (`data/scanner.db`). It imports nothing from the
trading path and run_bot never imports it, so deploying it restarts no running window.
Numbers only go into the prompt: no news, no headlines, no free text from any feed.

Cron (user crontab on the VPS, a few minutes after the bot's :01 cycle):
    5 * * * * cd ~/playground/CrySignal-BTC && ./venv/bin/python -m agents.scanner \
              >> data/scanner.log 2>&1

    python3 -m agents.scanner --dry-run     # fetch and print the prompt, ask nothing
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import requests

logger = logging.getLogger(__name__)

UNIVERSE = ("BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "LINK", "AVAX")  # prereg, fixed
SIDES = ("LONG", "SHORT", "NONE")
DEFAULT_MODEL = "claude-opus-5-5"            # prereg: the model of H-S/H-X
FAPI = "https://fapi.binance.com"
BAR_MS = 3_600_000
KLINES = 1000                                 # EMA200's seed weight after 1000 bars ~ e^-10
HTTP_TIMEOUT_S = 10
ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "scanner.db"
USAGE_PATH = ROOT / "data" / "llm_usage.jsonl"

SYSTEM = (
    "You are a crypto futures scanner. Once an hour you receive numbers only for a fixed "
    "set of Binance USD-M perpetuals (all quoted in USDT), measured on the 1h bar that has "
    "just closed. For EACH coin decide LONG, SHORT or NONE for a trade held about 24 hours. "
    "NONE is a normal answer; pick only where you expect the coin to beat a random coin "
    "taken in the same direction. Fields: ret_*_pct are close-to-close returns; "
    "close_vs_ema*_pct is (close - EMA) / EMA x 100; range_pos_24h is 0 at the 24h low and "
    "1 at the 24h high; vol_ratio is the last bar's volume over its 24h mean; funding_pct is "
    "the last funding rate in percent; basis_pct is (mark - index) / index x 100; "
    "oi_change_24h_pct is the open-interest change; ls_ratio is the global long/short "
    "account ratio; taker_ratio is taker buy over sell volume; ret_24h_vs_universe_pct is "
    "the coin's 24h return minus the mean of all coins. A null is a value that could not be "
    "fetched. Reply with ONLY a JSON object, one entry per coin: "
    '{"BTC": {"side": "LONG" or "SHORT" or "NONE", "confidence": integer 0-100, '
    '"reason": "at most 25 words, in Indonesian"}, ...}.'
)


# ── Fetch ───────────────────────────────────────────────────────────────────────────

def _get(path, params=None, session=None):
    s = session or requests
    r = s.get(FAPI + path, params=params or {}, timeout=HTTP_TIMEOUT_S,
              headers={"User-Agent": "curl/8.4"})
    r.raise_for_status()
    return r.json()


def _closed_klines(rows, now_ms):
    """[(open_ms, o, h, l, c, v)] with the bar still forming dropped."""
    out = [(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]))
           for k in rows]
    return [k for k in out if k[0] + BAR_MS <= now_ms]


def _num(v, nd=4):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, nd) if np.isfinite(f) else None


def _ema(x, n):
    a, e = 2 / (n + 1), x[0]
    for v in x[1:]:
        e = a * v + (1 - a) * e
    return e


def features(bars):
    """Price features from closed 1h bars (oldest first), or None if too short."""
    if len(bars) < 200:
        return None
    h, l, c, v = (np.array([b[k] for b in bars], float) for k in range(2, 6))
    ret = lambda n: (c[-1] / c[-1 - n] - 1) * 100
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    atr = tr[-14:].mean()
    hi, lo = h[-24:].max(), l[-24:].min()
    e50, e200 = _ema(c, 50), _ema(c, 200)
    return {"close": _num(c[-1], 6), "ret_1h_pct": _num(ret(1)), "ret_4h_pct": _num(ret(4)),
            "ret_24h_pct": _num(ret(24)),
            "ret_7d_pct": _num(ret(168)) if len(c) > 168 else None,
            "atr_pct": _num(atr / c[-1] * 100),
            "vol_ratio": _num(v[-1] / v[-24:].mean()) if v[-24:].mean() > 0 else None,
            "close_vs_ema50_pct": _num((c[-1] - e50) / e50 * 100),
            "close_vs_ema200_pct": _num((c[-1] - e200) / e200 * 100),
            "range_pos_24h": _num((c[-1] - lo) / (hi - lo)) if hi > lo else None}


def fetch_coin(coin, premium, now_ms, get=_get):
    """One coin's numbers. Derivatives fields that fail are None; the price must not."""
    sym = coin + "USDT"
    bars = _closed_klines(get("/fapi/v1/klines", {"symbol": sym, "interval": "1h",
                                                    "limit": KLINES}), now_ms)
    f = features(bars)
    if f is None:
        raise ValueError(f"{sym}: only {len(bars)} closed bars")
    f["bar_open_ms"] = bars[-1][0]
    p = premium.get(sym) or {}
    f["funding_pct"] = _num(float(p["lastFundingRate"]) * 100, 5) if "lastFundingRate" in p else None
    mark, idx = _num(p.get("markPrice"), 8), _num(p.get("indexPrice"), 8)
    f["basis_pct"] = _num((mark - idx) / idx * 100) if mark and idx else None
    for key, path, field in (("oi_change_24h_pct", "/futures/data/openInterestHist", None),
                             ("ls_ratio", "/futures/data/globalLongShortAccountRatio",
                              "longShortRatio"),
                             ("taker_ratio", "/futures/data/takerlongshortRatio",
                              "buySellRatio")):
        try:
            if field is None:
                rows = get(path, {"symbol": sym, "period": "1h", "limit": 25})
                a, b = float(rows[0]["sumOpenInterestValue"]), float(rows[-1]["sumOpenInterestValue"])
                f[key] = _num((b - a) / a * 100) if a > 0 and len(rows) >= 25 else None
            else:
                f[key] = _num(get(path, {"symbol": sym, "period": "1h", "limit": 1})[-1][field])
        except Exception as exc:  # noqa: BLE001 - a missing side field is data, not a failure
            logger.warning("%s %s: %s", sym, key, exc)
            f[key] = None
    return f


def snapshot(now_ms=None, get=_get):
    """{coin: features or None} for the universe, plus the decision bar's close time."""
    now_ms = now_ms or int(time.time() * 1000)
    try:
        premium = {p["symbol"]: p for p in get("/fapi/v1/premiumIndex")}
    except Exception as exc:  # noqa: BLE001
        logger.warning("premiumIndex: %s", exc)
        premium = {}
    coins = {}
    for c in UNIVERSE:
        try:
            coins[c] = fetch_coin(c, premium, now_ms, get)
        except Exception as exc:  # noqa: BLE001 - passed as missing, never dropped
            logger.warning("%s: %s", c, exc)
            coins[c] = None
    opens = {f["bar_open_ms"] for f in coins.values() if f}
    bar_close_ms = max(opens) + BAR_MS if opens else None
    for c, f in coins.items():           # a coin whose newest bar lags is stale, so missing
        if f and f["bar_open_ms"] + BAR_MS != bar_close_ms:
            coins[c] = None
    return {"bar_close_ms": bar_close_ms, "coins": coins}


# ── Prompt and reply ────────────────────────────────────────────────────────────────

def build_context(snap):
    live = [f for f in snap["coins"].values() if f and f.get("ret_24h_pct") is not None]
    mean24 = float(np.mean([f["ret_24h_pct"] for f in live])) if live else None
    coins = {}
    for c, f in snap["coins"].items():
        if not f:
            coins[c] = None
            continue
        d = {k: v for k, v in f.items() if k not in ("bar_open_ms", "close")}
        d["ret_24h_vs_universe_pct"] = (_num(f["ret_24h_pct"] - mean24)
                                        if mean24 is not None and f["ret_24h_pct"] is not None
                                        else None)
        coins[c] = d
    bar = datetime.fromtimestamp(snap["bar_close_ms"] / 1000, UTC).strftime("%Y-%m-%d %H:%M")
    return {"bar_closed_utc": bar, "coins": coins}


def parse_picks(text, coins=UNIVERSE):
    """{coin: {side, confidence, reason}}. A coin left out is NONE; a bad entry raises."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("no JSON object in reply")
    o = json.loads(m.group(0))
    out = {}
    for c in coins:
        e = o.get(c)
        if e is None:
            out[c] = {"side": "NONE", "confidence": None, "reason": "(not answered)"}
            continue
        side, conf = e.get("side"), e.get("confidence")
        if side not in SIDES:
            raise ValueError(f"{c}: bad side {side!r}")
        if not isinstance(conf, int) or isinstance(conf, bool) or not 0 <= conf <= 100:
            raise ValueError(f"{c}: bad confidence {conf!r}")
        out[c] = {"side": side, "confidence": conf, "reason": str(e.get("reason") or "")[:300]}
    return out


# ── Storage ─────────────────────────────────────────────────────────────────────────

def connect(path=DB_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS scan_cycles (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp     TEXT NOT NULL,     -- when the answer was stored (UTC)
            bar_close     TEXT NOT NULL,     -- close of the newest bar in the prompt (UTC)
            model         TEXT,
            error         TEXT,
            latency_ms    INTEGER,
            input_tokens  INTEGER,
            output_tokens INTEGER,
            cost_usd      REAL,
            n_coins       INTEGER,
            context       TEXT               -- the exact JSON the model saw
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_scan_bar ON scan_cycles(bar_close);
        CREATE TABLE IF NOT EXISTS scan_picks (
            cycle_id   INTEGER NOT NULL REFERENCES scan_cycles(id),
            coin       TEXT NOT NULL,
            side       TEXT,
            confidence INTEGER,
            reason     TEXT,
            close      REAL                  -- the coin's close at bar_close
        );
        CREATE INDEX IF NOT EXISTS idx_scan_picks_cycle ON scan_picks(cycle_id);
    """)
    return con


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()


def run_once(ask_fn=None, get=_get, db_path=DB_PATH, now_ms=None, usage_path=USAGE_PATH):
    """Fetch, ask, store. Returns the cycle id, or None when this bar was already scanned
    or no coin could be fetched. Never raises on a model failure: that is an error row."""
    from agents.llm import cost_usd
    snap = snapshot(now_ms, get)
    if snap["bar_close_ms"] is None:
        logger.error("no coin could be fetched; nothing asked")
        return None
    bar = _iso(snap["bar_close_ms"])
    con = connect(db_path)
    try:
        if con.execute("SELECT 1 FROM scan_cycles WHERE bar_close = ?", (bar,)).fetchone():
            logger.info("bar %s already scanned", bar)
            return None
        ctx = build_context(snap)
        prompt = "Scanner input (JSON):\n" + json.dumps(ctx, indent=1)
        rec = {"model": None, "error": None, "input_tokens": None, "output_tokens": None,
               "cost_usd": None}
        picks, t0 = {}, time.time()
        try:
            if ask_fn is None:
                from agents.llm import ask as ask_fn
            reply = ask_fn(prompt, system=SYSTEM, provider="anthropic",
                           model=os.getenv("SCANNER_MODEL") or DEFAULT_MODEL, max_tokens=16000)
            rec.update(model=reply.model, input_tokens=reply.input_tokens,
                       output_tokens=reply.output_tokens,
                       cost_usd=cost_usd(reply.model, reply.input_tokens, reply.output_tokens))
            _record_usage(usage_path, reply)
            picks = parse_picks(reply.text, [c for c, f in snap["coins"].items() if f])
        except Exception as exc:  # noqa: BLE001 - a model failure is data
            rec["error"] = f"{type(exc).__name__}: {exc}"[:300]
        rec["latency_ms"] = int((time.time() - t0) * 1000)
        cur = con.execute(
            "INSERT INTO scan_cycles (timestamp, bar_close, model, error, latency_ms, "
            "input_tokens, output_tokens, cost_usd, n_coins, context) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (datetime.now(UTC).isoformat(), bar, rec["model"], rec["error"], rec["latency_ms"],
             rec["input_tokens"], rec["output_tokens"], rec["cost_usd"],
             sum(1 for f in snap["coins"].values() if f), json.dumps(ctx)))
        cid = cur.lastrowid
        con.executemany(
            "INSERT INTO scan_picks (cycle_id, coin, side, confidence, reason, close) "
            "VALUES (?,?,?,?,?,?)",
            [(cid, c, p["side"], p["confidence"], p["reason"], snap["coins"][c]["close"])
             for c, p in picks.items()])
        con.commit()
        return cid
    finally:
        con.close()


def _record_usage(path, reply):
    """Into the ops report's cost ledger, so the monthly budget line counts the scanner."""
    try:
        from agents.ops_report import record_usage
        record_usage(path, reply, "scanner")
    except Exception as exc:  # noqa: BLE001 - losing a cost line must not lose the cycle
        logger.warning("scanner usage not recorded: %s", exc)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="fetch and print the prompt only")
    ap.add_argument("--db", default=str(DB_PATH))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    if args.dry_run:
        snap = snapshot()
        print(json.dumps(build_context(snap), indent=1) if snap["bar_close_ms"] else "no data")
        return 0
    cid = run_once(db_path=args.db)
    logger.info("scan cycle %s", cid)
    return 0


if __name__ == "__main__":
    sys.exit(main())

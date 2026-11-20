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
RANDOM_DB_PATH = ROOT / "data" / "scanner_random.db"       # count-matched random baseline, no model
HYBRID_DB_PATH = ROOT / "data" / "scanner_hybrid.db"       # H-SC2 arm, same schema
USAGE_PATH = ROOT / "data" / "llm_usage.jsonl"

PROMPT_VERSION = "v2"   # 2026-11-18: table input, picks-only reply (H-SC addendum); v1 = JSON both ways
SYSTEM = (
    "You are a crypto futures scanner. Once an hour you receive numbers only for a fixed "
    "set of Binance USD-M perpetuals (all quoted in USDT), measured on the 1h bar that has "
    "just closed, as a CSV table with one row per coin. For EACH coin decide LONG, SHORT or "
    "NONE for a trade held about 24 hours. NONE is a normal answer; pick only where you "
    "expect the coin to beat a random coin taken in the same direction. Columns: ret_*_pct "
    "are close-to-close returns; close_vs_ema*_pct is (close - EMA) / EMA x 100; "
    "range_pos_24h is 0 at the 24h low and 1 at the 24h high; vol_ratio is the last bar's "
    "volume over its 24h mean; funding_pct is the last funding rate in percent; basis_pct is "
    "(mark - index) / index x 100; oi_change_24h_pct is the open-interest change; ls_ratio "
    "is the global long/short account ratio; taker_ratio is taker buy over sell volume; "
    "ret_24h_vs_universe_pct is the coin's 24h return minus the mean of all coins. null is a "
    "value that could not be fetched; a coin missing from the table could not be fetched at "
    "all and must not be picked. Reply with ONLY a JSON object holding just the coins you "
    'pick LONG or SHORT: {"ETH": {"side": "LONG" or "SHORT", "confidence": integer 0-100, '
    '"reason": "at most 25 words, in Indonesian"}, ...}. Every coin you leave out is NONE; '
    "{} means NONE for all of them."
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
    f["_bars"] = [(k[0], k[2], k[3], k[4]) for k in bars[-72:]]   # (open_ms, high, low, close)
    f["_k1h"] = bars                                           # for the hybrid arm's engine score
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

# ── Hybrid arm (H-SC2): the engine's own score per coin, added to the same input ──────

SYSTEM_HYBRID = SYSTEM + (
    " Each row also carries engine_buy_score, engine_sell_score, engine_threshold and "
    "engine_signal (BUY/SELL/HOLD): the score of a rule-based indicator system on the same "
    "closed 1h bar; it fires when a score passes its threshold. That system's record on "
    "altcoins is poor, so treat it as one more input, not as authority."
)


def engine_score(coin, k1h, get=_get):
    """The SpotSignal engine's futures-mode score on the coin's last closed 1h bar, as the
    prereg fixes it: last 499 closed perp bars, live S/R, 4h/1d HTF from the coin's own perp
    klines (as live fetches them), no market-structure inputs (the live fetchers are BTC-only)."""
    import pandas as pd
    from backtest import LIVE_BASE_BARS, _live_indicators
    from config import SIGNAL_THRESHOLD
    from signals.engine import generate_signals
    from signals.htf import _htf_aligned, _htf_indicators
    from signals.indicators import detect_support_resistance
    df = pd.DataFrame(k1h[-LIVE_BASE_BARS:], columns=["timestamp", "open", "high", "low", "close", "volume"])
    df.index = pd.to_datetime(df.pop("timestamp"), unit="ms")
    window = _live_indicators(df, 24)
    htf = {"4h": "NEUTRAL", "1d": "NEUTRAL", "aligned": False, "4h_indicators": {}, "1d_indicators": {}}
    for tf in ("4h", "1d"):
        rows = get("/fapi/v1/klines", {"symbol": coin + "USDT", "interval": tf, "limit": 250})
        h = pd.DataFrame([[float(x) for x in r[:6]] for r in rows],
                         columns=["timestamp", "open", "high", "low", "close", "volume"])
        ind = _htf_indicators(h)
        htf[tf], htf[f"{tf}_indicators"] = ind["trend"], ind
    htf["aligned"] = (htf["4h"] != "NEUTRAL" and htf["4h"] == htf["1d"]
                      and _htf_aligned(htf["4h_indicators"], htf["1d_indicators"], htf["1d"]))
    sig = generate_signals(window, htf=htf, market_structure=None,
                           sr=detect_support_resistance(window), mode="futures",
                           threshold_override=SIGNAL_THRESHOLD)
    return {"engine_buy_score": _num(sig.get("buy_score"), 2),
            "engine_sell_score": _num(sig.get("sell_score"), 2),
            "engine_threshold": _num(sig.get("_threshold"), 2), "engine_signal": sig.get("type")}


def add_engine_scores(snap, get=_get, score=engine_score):
    """Attach `_engine` to every fetched coin; a coin whose score fails gets nulls."""
    for c, f in snap["coins"].items():
        if not f:
            continue
        try:
            f["_engine"] = score(c, f["_k1h"], get)
        except Exception as exc:  # noqa: BLE001 - a failed score is data, not a dead coin
            logger.warning("%s engine score: %s", c, exc)
            f["_engine"] = {"engine_buy_score": None, "engine_sell_score": None,
                            "engine_threshold": None, "engine_signal": None}
    return snap


def random_picks(pure, coins, bar):
    """Count-matched random baseline for the books: this bar's pure LONG/SHORT picks (same
    number, sides and confidences) dealt to coins drawn uniformly from `coins`. Seeded by
    the bar, so any cycle can be reproduced. A design decision for reading the books."""
    import random
    acts = [(p["side"], p["confidence"]) for p in pure.values() if p["side"] in ("LONG", "SHORT")]
    rng = random.Random(f"scanner-random:{bar}")
    chosen = rng.sample(list(coins), min(len(acts), len(coins)))
    rng.shuffle(acts)
    return {c: {"side": sd, "confidence": cf, "reason": "(acak)"} for c, (sd, cf) in zip(chosen, acts)}


def pure_picks(path, bar):
    """The pure arm's picks for `bar`. Raises when that cycle is missing or errored, so the
    random arm records the same failure as an error row."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT id, error FROM scan_cycles WHERE bar_close = ?", (bar,)).fetchone()
        if row is None:
            raise LookupError(f"pure arm has no cycle for {bar}")
        if row[1]:
            raise RuntimeError(f"pure arm errored: {row[1]}"[:300])
        return {c: {"side": sd, "confidence": cf} for c, sd, cf in con.execute(
            "SELECT coin, side, confidence FROM scan_picks WHERE cycle_id = ?", (row[0],))}
    finally:
        con.close()


DECIMALS = {"funding_pct": 4, "basis_pct": 3}       # every other number: 2 decimals


def _r(key, v):
    return round(v, DECIMALS.get(key, 2)) if isinstance(v, float) else v


def build_context(snap, engine=False):
    """The numbers the model sees, rounded as it sees them (`render_table`). Stored as the
    cycle's `context`, so a stored row is exactly what was asked about."""
    live = [f for f in snap["coins"].values() if f and f.get("ret_24h_pct") is not None]
    mean24 = float(np.mean([f["ret_24h_pct"] for f in live])) if live else None
    coins = {}
    for c, f in snap["coins"].items():
        if not f:
            coins[c] = None
            continue
        d = {k: v for k, v in f.items() if k not in ("bar_open_ms", "close") and not k.startswith("_")}
        if engine:
            d.update(f.get("_engine") or {})
        d["ret_24h_vs_universe_pct"] = (_num(f["ret_24h_pct"] - mean24)
                                        if mean24 is not None and f["ret_24h_pct"] is not None
                                        else None)
        coins[c] = {k: _r(k, v) for k, v in d.items()}
    bar = datetime.fromtimestamp(snap["bar_close_ms"] / 1000, UTC).strftime("%Y-%m-%d %H:%M")
    return {"bar_closed_utc": bar, "prompt": PROMPT_VERSION, "coins": coins}


def render_table(ctx):
    """CSV with the column names once: the same numbers as JSON at under half the tokens."""
    live = {c: d for c, d in ctx["coins"].items() if d}
    if not live:
        return f"Bar closed {ctx['bar_closed_utc']} UTC. No coin could be fetched."
    cols = list(next(iter(live.values())))
    cell = lambda v: "null" if v is None else str(v)
    rows = [",".join(["coin"] + cols)] + [",".join([c] + [cell(d.get(k)) for k in cols])
                                          for c, d in live.items()]
    return f"Bar closed {ctx['bar_closed_utc']} UTC, one row per coin:\n" + "\n".join(rows)


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
            out[c] = {"side": "NONE", "confidence": None, "reason": ""}   # v2: left out = NONE
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
        -- Paper book driven by the picks (design decision for data, not part of H-SC).
        CREATE TABLE IF NOT EXISTS scan_positions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            book        TEXT NOT NULL DEFAULT 'futures',   -- futures / spot (BOOKS)
            coin        TEXT NOT NULL,
            side        TEXT NOT NULL,           -- LONG / SHORT
            cycle_id    INTEGER REFERENCES scan_cycles(id),
            confidence  INTEGER,
            opened_bar  TEXT NOT NULL,           -- bar close the entry was priced at (UTC)
            entry       REAL NOT NULL,
            stop        REAL NOT NULL,
            target      REAL NOT NULL,
            atr_pct     REAL,
            closed_bar  TEXT,                    -- close of the bar the exit happened in
            exit        REAL,
            outcome     TEXT,                    -- STOP / TARGET / TIME
            pnl_pct     REAL,                    -- after the book's round-trip cost
            size_usd    REAL,                    -- BOOK_FRACTION of realised equity at entry
            pnl_usd     REAL
        );
        CREATE INDEX IF NOT EXISTS idx_scan_pos_open ON scan_positions(book, coin, closed_bar);
    """)
    try:   # 2026-11-19: when the entry was priced (NULL = at the bar close, the old rule)
        con.execute("ALTER TABLE scan_positions ADD COLUMN entry_at TEXT")
    except sqlite3.OperationalError:
        pass
    return con


# ── Paper book ──────────────────────────────────────────────────────────────────────
# Design decision for data yield (owner 2026-11-16), fixed before any result: every
# LONG/SHORT pick opens one position per coin at the bar close; stop 1.5 x ATR(1h), target
# 3 x ATR, closed at the close of the bar 24h after entry; a bar touching both levels counts
# as the stop. Amended 2026-11-19 (owner): the ATR is ATR(1h) scaled to the 24h hold
# (x sqrt 24), since a 1.5 x ATR(1h) stop sat inside one day's noise and closed most
# positions within hours of a pick made for 24h. Positions opened before keep their levels. A 1,000 USD demo account: each position is 10% of realised equity (it
# compounds), opened only if the cash not already in positions covers it. The model never
# sees these books, so H-SC's picks are unaffected by them.
# Two independent books on the same picks: futures takes LONG and SHORT; spot (owner, same
# day) takes LONG only, since spot cannot short, at spot costs. Each has its own 1,000 USD,
# its own one-position-per-coin slots and its own exits.

BOOK_STOP_ATR, BOOK_TARGET_ATR, BOOK_HOLD_H = 1.5, 3.0, 24
BOOK_ATR_SCALE = BOOK_HOLD_H ** 0.5    # ATR(1h) -> the hold horizon (random-walk scaling)
BOOK_START_USD = 1000.0              # owner, 2026-11-16, per book
BOOK_FRACTION = 0.10                 # of realised equity per position; 9 coins fit in 90%
BOOKS = {   # round trip in pp: (fee + 0.05% slippage) x 2; sides the book can take
    "futures": {"cost_pp": 0.20, "sides": ("LONG", "SHORT")},   # 0.05% taker
    "spot":    {"cost_pp": 0.30, "sides": ("LONG",)},           # 0.10% taker
}
BOOK_COST_PP = BOOKS["futures"]["cost_pp"]

# Reset 2026-11-20 (owner, with run 5): every arm's books restart at BOOK_START_USD and
# H-SC / H-SC2 / H-SCD count only cycles from this bar close on. A position opened before it
# is carried: it runs out on its own levels (<= BOOK_HOLD_H) and holds its coin's slot, but
# no balance counts it. Compared as text, the format every scan_* timestamp is stored in.
WINDOW_FROM = "2026-11-21T00:00:00+00:00"


def book_account(con, book="futures"):
    """(realised equity, USD tied up in open positions) of one book, from WINDOW_FROM."""
    real = con.execute("SELECT COALESCE(SUM(pnl_usd), 0) FROM scan_positions WHERE book = ? "
                       "AND closed_bar IS NOT NULL AND opened_bar >= ?", (book, WINDOW_FROM)).fetchone()[0]
    used = con.execute("SELECT COALESCE(SUM(size_usd), 0) FROM scan_positions WHERE book = ? "
                       "AND closed_bar IS NULL AND opened_bar >= ?", (book, WINDOW_FROM)).fetchone()[0]
    return BOOK_START_USD + real, used


# Amended 2026-11-19 (owner): the answer arrives ~5 minutes after the bar close, so a book
# entry priced at the close was 5 minutes stale. A position now enters at the perp price
# fetched once the picks are in, and its first hour is judged on the 1m bars after that
# moment. A failed fetch falls back to the bar close and the 1h bar, as before.

def fill_prices(get=_get):
    """({coin: last perp price}, the moment fetched in ms), or ({}, None) on failure."""
    try:
        rows = get("/fapi/v1/ticker/price")
        at = int(time.time() * 1000)
        px = {r["symbol"][:-4]: float(r["price"]) for r in rows if r.get("symbol", "").endswith("USDT")}
        return {c: px[c] for c in UNIVERSE if c in px}, at
    except Exception as exc:  # noqa: BLE001 - the book falls back to the bar close
        logger.warning("fill prices: %s", exc)
        return {}, None


def _first_hour(get, coin, entry_ms, bar_end_ms):
    """(high, low) of the 1m perp bars from the first whole minute after `entry_ms` to the end
    of the entry bar, or None when they cannot be fetched."""
    start = -(-entry_ms // 60_000) * 60_000
    try:
        rows = get("/fapi/v1/klines", {"symbol": coin + "USDT", "interval": "1m",
                                       "startTime": start, "endTime": bar_end_ms - 1, "limit": 60})
        rows = [r for r in rows if start <= int(r[0]) < bar_end_ms]
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s first-hour 1m bars: %s", coin, exc)
        return None
    if not rows:
        return None
    return max(float(r[2]) for r in rows), min(float(r[3]) for r in rows)


def book_levels(side, entry, atr_pct):
    d = 1 if side == "LONG" else -1
    a = entry * atr_pct / 100 * BOOK_ATR_SCALE
    return entry - d * BOOK_STOP_ATR * a, entry + d * BOOK_TARGET_ATR * a


def _close_pos(con, pid, book, side, entry, size, bar_close_ms, exit_px, outcome):
    d = 1 if side == "LONG" else -1
    pct = d * (exit_px / entry - 1) * 100 - BOOKS[book]["cost_pp"]
    con.execute("UPDATE scan_positions SET closed_bar=?, exit=?, outcome=?, pnl_pct=?, pnl_usd=? "
                "WHERE id=?", (_iso(bar_close_ms), exit_px, outcome, round(pct, 4),
                               round(pct / 100 * size, 4), pid))


def manage_book(con, snap, get=_get):
    """Walk every open position through the closed bars after its entry. Returns closes."""
    closed = 0
    for pid, book, coin, side, opened, entry, stop, target, size, entry_at in con.execute(
            "SELECT id, book, coin, side, opened_bar, entry, stop, target, size_usd, entry_at "
            "FROM scan_positions WHERE closed_bar IS NULL").fetchall():
        f = snap["coins"].get(coin)
        if not f:
            continue                         # missing this hour; the next fetch has the bars
        t0 = int(datetime.fromisoformat(opened).timestamp() * 1000)
        deadline = t0 + BOOK_HOLD_H * BAR_MS
        for open_ms, hi, lo, cl in f["_bars"]:
            end = open_ms + BAR_MS
            if open_ms < t0:
                continue
            if open_ms == t0 and entry_at:
                # The entry bar: only the minutes after the fill count.
                fh = _first_hour(get, coin, int(datetime.fromisoformat(entry_at).timestamp() * 1000), end)
                if fh is not None:
                    hi, lo = fh
            hit_stop = lo <= stop if side == "LONG" else hi >= stop
            hit_tgt = hi >= target if side == "LONG" else lo <= target
            if hit_stop or hit_tgt:
                _close_pos(con, pid, book, side, entry, size, end, stop if hit_stop else target,
                           "STOP" if hit_stop else "TARGET")
                closed += 1
                break
            if end >= deadline:
                _close_pos(con, pid, book, side, entry, size, end, cl, "TIME")
                closed += 1
                break
    return closed


def open_book(con, cid, picks, snap, bar_close_ms, book="futures", fills=None, fill_ms=None):
    """One position per coin and book: a pick for a coin the book already holds is ignored,
    one the book cannot take (SHORT on spot) or the free cash cannot cover is skipped.
    Size = BOOK_FRACTION of the book's realised equity."""
    held = {r[0] for r in con.execute("SELECT coin FROM scan_positions WHERE book = ? AND "
                                      "closed_bar IS NULL", (book,))}
    equity, used = book_account(con, book)
    size = round(equity * BOOK_FRACTION, 2)
    opened = 0
    for coin, p in picks.items():
        f = snap["coins"].get(coin)
        if p["side"] not in BOOKS[book]["sides"] or coin in held or not f or not f.get("atr_pct"):
            continue
        if size <= 0 or used + size > equity:
            logger.info("book: no cash for %s (%.2f used of %.2f)", coin, used, equity)
            continue
        fill = (fills or {}).get(coin) if fill_ms else None
        entry = fill or f["close"]
        stop, target = book_levels(p["side"], entry, f["atr_pct"])
        con.execute("INSERT INTO scan_positions (book, coin, side, cycle_id, confidence, opened_bar, "
                    "entry, stop, target, atr_pct, size_usd, entry_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (book, coin, p["side"], cid, p["confidence"], _iso(bar_close_ms), entry,
                     stop, target, f["atr_pct"], size, _iso(fill_ms) if fill else None))
        used += size
        opened += 1
    return opened


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()


def run_once(ask_fn=None, get=_get, db_path=DB_PATH, now_ms=None, usage_path=USAGE_PATH,
             snap=None, arm="pure", pure_db=DB_PATH):
    """Fetch (unless `snap` is given), ask, store. Returns the cycle id, or None when this bar
    was already scanned or no coin could be fetched. Never raises on a model failure: that is
    an error row. `arm="hybrid"` (H-SC2) adds the engine fields and their sentence;
    `arm="random"` asks no model and deals the pure arm's picks of this bar at random."""
    snap = snap or snapshot(now_ms, get)
    if snap["bar_close_ms"] is None:
        logger.error("no coin could be fetched; nothing asked")
        return None
    bar = _iso(snap["bar_close_ms"])
    con = connect(db_path)
    try:
        if con.execute("SELECT 1 FROM scan_cycles WHERE bar_close = ?", (bar,)).fetchone():
            logger.info("bar %s already scanned", bar)
            return None
        try:
            manage_book(con, snap, get)      # exits first, on the bars closed since last cycle
        except Exception as exc:  # noqa: BLE001 - the book must never cost a scan
            logger.warning("paper book not managed: %s", exc)
        ctx = build_context(snap, engine=(arm == "hybrid"))
        prompt = render_table(ctx)
        rec = {"model": None, "error": None, "input_tokens": None, "output_tokens": None,
               "cost_usd": None}
        picks, t0 = {}, time.time()
        try:
            if arm == "random":
                rec["model"] = "random"
                picks = random_picks(pure_picks(pure_db, bar),
                                     [c for c in UNIVERSE if snap["coins"].get(c)], bar)
            else:
                picks = _ask(ask_fn, prompt, arm, rec, usage_path, snap)
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
        try:
            fills, fill_ms = (fill_prices(get) if any(p["side"] in ("LONG", "SHORT") for p in picks.values())
                              else ({}, None))
            for book in BOOKS:
                open_book(con, cid, picks, snap, snap["bar_close_ms"], book, fills, fill_ms)
        except Exception as exc:  # noqa: BLE001
            logger.warning("paper book not opened: %s", exc)
        con.commit()
        return cid
    finally:
        con.close()


def _ask(ask_fn, prompt, arm, rec, usage_path, snap):
    """One model call; fills `rec` with model, tokens and cost, returns the parsed picks."""
    from agents.llm import cost_usd
    if ask_fn is None:
        from agents.llm import ask as ask_fn
    reply = ask_fn(prompt, system=SYSTEM_HYBRID if arm == "hybrid" else SYSTEM, provider="anthropic",
                   model=os.getenv("SCANNER_MODEL") or DEFAULT_MODEL, max_tokens=16000)
    rec.update(model=reply.model, input_tokens=reply.input_tokens,
               output_tokens=reply.output_tokens,
               cost_usd=cost_usd(reply.model, reply.input_tokens, reply.output_tokens))
    _record_usage(usage_path, reply, "scanner" if arm == "pure" else "scanner_hybrid")
    return parse_picks(reply.text, [c for c, f in snap["coins"].items() if f])


# ── Hourly Telegram digest (owner, 2026-11-18): only when a position opened or closed ─

ARM_DBS = (("Claude murni", "murni", DB_PATH), ("Claude + engine", "engine", HYBRID_DB_PATH),
           ("Acak", "acak", RANDOM_DB_PATH))
_OUT_WORD = {"STOP": "kena stop", "TARGET": "kena target", "TIME": "batas 24 jam"}


def book_snapshot(path):
    """{position id: row} of one arm's books, read-only; {} without a database."""
    if not Path(path).exists():
        return {}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        return {r["id"]: dict(r) for r in con.execute(
            "SELECT id, book, coin, side, closed_bar, outcome, pnl_usd FROM scan_positions")}
    except sqlite3.Error:
        return {}
    finally:
        con.close()


def digest(bar_iso, before, after, balances):
    """The hour's opens and closes per arm, then every book's realised balance. None when
    nothing opened or closed: a quiet hour sends nothing."""
    from notifier.common import _esc, _usd
    tag = lambda p: f"{p['side']} {_esc(p['coin'])}" + (" spot" if p["book"] == "spot" else "")
    opened, closed = [], []
    for name, _, _ in ARM_DBS:
        b, a = before.get(name, {}), after.get(name, {})
        new = [a[i] for i in sorted(a) if i not in b]
        done = [a[i] for i in sorted(a) if a[i]["closed_bar"] and not (b.get(i) or {}).get("closed_bar")]
        if new:
            opened.append(f"• {name}: " + ", ".join(tag(p) for p in new))
        for p in done:
            closed.append(f"• {name}: {tag(p)} {_OUT_WORD.get(p['outcome'], _esc(p['outcome'] or ''))} "
                          f"{_usd(p['pnl_usd'] or 0, sign=True)}")
    if not opened and not closed:
        return None
    bar = datetime.fromisoformat(bar_iso).strftime("%H:%M")
    lines = [f"🧭 <b>Scanner</b> · bar {bar} UTC"]
    if opened:
        lines += ["<b>Dibuka</b>"] + opened
    if closed:
        lines += ["<b>Ditutup</b>"] + closed
    rows = [f"{'':8}{'futures':>11}{'spot':>11}"]
    for name, short, _ in ARM_DBS:
        fut, spot = balances.get(name, (None, None))
        cell = lambda v: (_usd(v) if v is not None else "–").rjust(11)
        rows.append(f"{short:8}{cell(fut)}{cell(spot)}")
    lines.append("<pre>" + "\n".join(rows) + "</pre>")
    return "\n".join(lines)


def balances_of(path):
    """(futures, spot) realised balance of one arm, or (None, None) without a database."""
    if not Path(path).exists():
        return (None, None)
    con = connect(path)
    try:
        return tuple(book_account(con, b)[0] for b in ("futures", "spot"))
    finally:
        con.close()


def send_digest(bar_iso, before, paths=None, send=None):
    paths = paths or {name: path for name, _, path in ARM_DBS}
    after = {name: book_snapshot(path) for name, path in paths.items()}
    text = digest(bar_iso, before, after, {name: balances_of(path) for name, path in paths.items()})
    if not text:
        return False
    if send is None:
        from notifier.common import _send_telegram_message
        send = lambda t: _send_telegram_message(t, "scanner")
    return bool(send(text))


def _record_usage(path, reply, purpose="scanner"):
    """Into the ops report's cost ledger, so the monthly budget line counts the scanner."""
    try:
        from agents.ops_report import record_usage
        record_usage(path, reply, purpose)
    except Exception as exc:  # noqa: BLE001 - losing a cost line must not lose the cycle
        logger.warning("scanner usage not recorded: %s", exc)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="fetch and print the prompt only")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--hybrid-db", default=str(HYBRID_DB_PATH))
    ap.add_argument("--no-hybrid", action="store_true", help="skip the hybrid arm")
    ap.add_argument("--random-db", default=str(RANDOM_DB_PATH))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    if args.dry_run:
        snap = snapshot()
        print(render_table(build_context(snap)) if snap["bar_close_ms"] else "no data")
        return 0
    snap = snapshot()
    if snap["bar_close_ms"] is None:
        logger.error("no coin could be fetched; nothing asked")
        return 0
    paths = {"Claude murni": args.db, "Claude + engine": args.hybrid_db, "Acak": args.random_db}
    before = {name: book_snapshot(path) for name, path in paths.items()}
    cid = run_once(db_path=args.db, snap=snap)              # pure arm first, unchanged
    logger.info("scan cycle %s", cid)
    if not args.no_hybrid:
        try:
            hid = run_once(db_path=args.hybrid_db, snap=add_engine_scores(snap), arm="hybrid")
            logger.info("hybrid scan cycle %s", hid)
        except Exception as exc:  # noqa: BLE001 - the hybrid arm never costs the pure one
            logger.error("hybrid arm failed: %s", exc)
    try:                                                    # after pure: it deals pure's picks
        rid = run_once(db_path=args.random_db, snap=snap, arm="random", pure_db=args.db)
        logger.info("random scan cycle %s", rid)
    except Exception as exc:  # noqa: BLE001 - the baseline never costs either arm
        logger.error("random arm failed: %s", exc)
    try:
        send_digest(_iso(snap["bar_close_ms"]), before, paths)
    except Exception as exc:  # noqa: BLE001 - the digest never costs a cycle
        logger.warning("scanner digest not sent: %s", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())

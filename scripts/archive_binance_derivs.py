#!/usr/bin/env python3
"""Archive Binance USDⓈ-M derivatives statistics before they expire.

Binance serves long/short ratios, open-interest history and taker ratios for the last
30 days only (`/futures/data/*`; an older startTime is rejected with -1130). Unlike basis
and funding, these can never be fetched historically, so they are only testable if they
are kept. This appends every hourly row to `data/derivs/<SYMBOL>_<stat>.csv`, resuming after
the newest row already on disk. The first run backfills the full 29 days available.

Read-only public endpoints, no API key. It touches nothing the bot reads, so it can
run during a paper run. Daily from cron on the VPS (Binance is DNS-blocked elsewhere):

    40 3 * * * cd $HOME/playground/CrySignal-BTC && ./venv/bin/python scripts/archive_binance_derivs.py >> data/derivs/archive.log 2>&1

Pre-registered use: `docs/superpowers/specs/2026-10-10-derivs-archive-prereg.md` (H-D).
"""
from __future__ import annotations

import argparse
import csv
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

BASE = "https://fapi.binance.com/futures/data/"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT")
STATS = {                               # file stem -> endpoint
    "ls_global": "globalLongShortAccountRatio",
    "ls_top_account": "topLongShortAccountRatio",
    "ls_top_position": "topLongShortPositionRatio",
    "oi": "openInterestHist",
    "taker": "takerlongshortRatio",
}
HOUR_MS = 3_600_000
PAGE = 499                              # hours per request (limit 500)
LOOKBACK_MS = 29 * 24 * HOUR_MS         # stay inside Binance's 30-day horizon
PAUSE_S = 0.25
OUT = Path(__file__).resolve().parents[1] / "data" / "derivs"


def _get(session, endpoint, params):
    for attempt in range(5):
        r = session.get(BASE + endpoint, params=params, timeout=30,
                        headers={"User-Agent": "curl/8.4"})
        if r.status_code == 200:
            return r.json()
        if r.status_code in (418, 429) or r.status_code >= 500:
            time.sleep(10 * (attempt + 1))
            continue
        r.raise_for_status()
    raise RuntimeError(f"{endpoint} failed after retries: {r.status_code} {r.text[:200]}")


def read_existing(path):
    """(header, {timestamp: row}) of an archive file, or (None, {})."""
    if not path.exists():
        return None, {}
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    return (list(rows[0].keys()) if rows else None), {int(r["timestamp"]): r for r in rows}


def windows(start_ms, now_ms):
    """[start, end] pages of at most PAGE hours covering start_ms → now_ms."""
    out, s = [], start_ms
    while s <= now_ms:
        e = min(s + PAGE * HOUR_MS, now_ms)
        out.append((s, e))
        s = e + 1
    return out


def merge(existing, fetched):
    """Existing rows win on a timestamp collision: the archive never rewrites history."""
    rows = dict(existing)
    added = 0
    for r in fetched:
        ts = int(r["timestamp"])
        if ts not in rows:
            rows[ts] = {k: str(v) for k, v in r.items()}
            added += 1
    return rows, added


def write(path, header, rows):
    keys = header or sorted({k for r in rows.values() for k in r})
    if "timestamp" in keys:
        keys = ["timestamp"] + [k for k in keys if k != "timestamp"]
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for ts in sorted(rows):
            w.writerow(rows[ts])
    tmp.replace(path)


def archive(session, symbol, stem, endpoint, out, now_ms):
    path = out / f"{symbol}_{stem}.csv"
    header, existing = read_existing(path)
    start = now_ms - LOOKBACK_MS
    if existing:
        start = max(start, max(existing) + 1)
    fetched = []
    for s, e in windows(start, now_ms):
        fetched += _get(session, endpoint, {"symbol": symbol, "period": "1h", "limit": 500,
                                            "startTime": s, "endTime": e})
        time.sleep(PAUSE_S)
    rows, added = merge(existing, fetched)
    if added:
        write(path, header, rows)
    gap = ""
    if existing and fetched and min(int(r["timestamp"]) for r in fetched) - max(existing) > HOUR_MS:
        gap = "  GAP before this fetch"
    return f"{symbol} {stem}: +{added} rows, {len(rows)} total{gap}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    now_ms = int(time.time() * 1000)
    print(f"== {datetime.now(UTC):%Y-%m-%d %H:%M}Z archive_binance_derivs", flush=True)
    failed = 0
    with requests.Session() as session:
        for symbol in args.symbols.split(","):
            for stem, endpoint in STATS.items():
                try:
                    print("  " + archive(session, symbol, stem, endpoint, args.out, now_ms), flush=True)
                except Exception as e:                       # one stat failing must not stop the rest
                    failed += 1
                    print(f"  {symbol} {stem}: FAILED {e}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

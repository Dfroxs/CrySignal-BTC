#!/usr/bin/env python3
"""Download BTCUSDT perpetual history for the H-F test
(`docs/superpowers/specs/2026-10-09-hist-futures-ic-prereg.md`).

Writes four CSVs: mark and index 1h klines, perpetual 1h klines (with taker-buy volume),
and settled funding. Read-only public endpoints, paced well under Binance's weight
limits. Binance is unreachable from some networks, so this runs anywhere `requests`
does (the VPS, in practice) and needs nothing else from the repo:

    python3 scripts/fetch_futures_history.py --out /tmp/hf
"""
from __future__ import annotations

import argparse
import csv
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

BASE = "https://fapi.binance.com"
START = datetime(2020, 1, 1, tzinfo=UTC)
END = datetime(2026, 8, 29, 23, 0, tzinfo=UTC)        # last hour inside the window
HOUR_MS = 3_600_000
PAUSE_S = 0.35


def _ms(dt):
    return int(dt.timestamp() * 1000)


def _get(session, path, params):
    for attempt in range(5):
        r = session.get(BASE + path, params=params, timeout=30)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (418, 429) or r.status_code >= 500:
            time.sleep(10 * (attempt + 1))
            continue
        r.raise_for_status()
    raise RuntimeError(f"{path} failed after retries: {r.status_code} {r.text[:200]}")


def klines(session, path, key, header, out):
    rows, start, end = [], _ms(START), _ms(END)
    while start <= end:
        batch = _get(session, path, {key: "BTCUSDT", "interval": "1h", "startTime": start,
                                     "endTime": end, "limit": 1500})
        if not batch:
            break
        rows += [r[:len(header)] for r in batch]
        start = int(batch[-1][0]) + HOUR_MS
        time.sleep(PAUSE_S)
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    return len(rows)


def funding(session, out):
    rows, start, end = [], _ms(START) - 8 * HOUR_MS, _ms(END)
    while start <= end:
        batch = _get(session, "/fapi/v1/fundingRate", {"symbol": "BTCUSDT", "startTime": start,
                                                       "endTime": end, "limit": 1000})
        if not batch:
            break
        rows += [(b["fundingTime"], b["fundingRate"]) for b in batch]
        start = int(batch[-1]["fundingTime"]) + 1
        time.sleep(PAUSE_S)
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["funding_time", "funding_rate"])
        w.writerows(rows)
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    out = Path(ap.parse_args().out)
    out.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers["User-Agent"] = "curl/8.4"
    ohlc = ["open_time", "open", "high", "low", "close"]
    print("mark", klines(s, "/fapi/v1/markPriceKlines", "symbol", ohlc, out / "mark_1h.csv"))
    print("index", klines(s, "/fapi/v1/indexPriceKlines", "pair", ohlc, out / "index_1h.csv"))
    print("perp", klines(s, "/fapi/v1/klines", "symbol",
                         ohlc + ["volume", "close_time", "quote_volume", "trades",
                                 "taker_buy_volume"], out / "perp_1h.csv"))
    print("funding", funding(s, out / "funding.csv"))


if __name__ == "__main__":
    main()

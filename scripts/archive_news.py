#!/usr/bin/env python3
"""Archive the news the bot reads, before the next cycle overwrites it.

`news_scraper.py` rewrites `data/crypto_news_sentiment.csv` (about 50 latest headlines) and
`data/macro_events.csv` (the week's calendar) every cycle, so a headline the bot scored an
hour ago is gone. `cycle_log` keeps only the resulting label and Fear & Greed. Testing the
news overlay later needs the headlines themselves, and they cannot be fetched again. This
appends every new row to a monthly file:

    data/news/news-YYYY-MM.csv     headline rows + archived_at, unique on (link, title)
    data/news/macro-YYYY-MM.csv    calendar rows + archived_at, unique on the whole row,
                                   so an event whose `actual` gets filled in is kept again

Read-only towards the bot: it never touches the files it reads from. Hourly from cron, two
minutes after the bot's :01 cycle (no bot restart needed):

    3 * * * * cd $HOME/playground/CrySignal-BTC && ./venv/bin/python scripts/archive_news.py >> data/news/archive.log 2>&1
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (("news", "crypto_news_sentiment.csv", ("link", "title")),
           ("macro", "macro_events.csv", None))          # None = the whole row is the key


def _key(row, cols, fields):
    return tuple(row.get(c, "") for c in (fields if cols is None else cols))


def archive(data: Path, now: datetime) -> dict:
    """Append new rows of each source to this month's file. Returns {name: rows added}."""
    out_dir = data / "news"
    out_dir.mkdir(parents=True, exist_ok=True)
    added = {}
    for name, src_name, cols in SOURCES:
        src = data / src_name
        if not src.exists():
            added[name] = None
            continue
        with src.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            fields = list(reader.fieldnames or [])
            rows = list(reader)
        # Seen keys from this month and the last, so a headline that spans a month edge is
        # not archived twice.
        seen = set()
        for month in (now, now.replace(day=1) - timedelta(days=1)):
            old = out_dir / f"{name}-{month:%Y-%m}.csv"
            if old.exists():
                with old.open(newline="", encoding="utf-8") as fh:
                    seen |= {_key(r, cols, fields) for r in csv.DictReader(fh)}
        new = []
        for r in rows:
            k = _key(r, cols, fields)
            if k not in seen:
                seen.add(k)
                new.append(r)
        dst = out_dir / f"{name}-{now:%Y-%m}.csv"
        if new:
            first = not dst.exists()
            with dst.open("a", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=fields + ["archived_at"], extrasaction="ignore")
                if first:
                    w.writeheader()
                stamp = now.isoformat(timespec="seconds")
                w.writerows(dict(r, archived_at=stamp) for r in new)
        added[name] = len(new)
    return added


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    args = ap.parse_args()
    now = datetime.now(UTC)
    added = archive(args.data, now)
    print(f"{now:%Y-%m-%d %H:%M}Z archive_news {added}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

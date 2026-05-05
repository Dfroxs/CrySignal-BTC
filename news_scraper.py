"""Phase 1 — News & macro event scraper.

Fetches from free RSS / API sources, deduplicates, scores sentiment,
and exports CSV files consumed by core_analysis.py.
"""

import logging
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import pandas as pd

from config import HTTP_SESSION, MACRO_CSV, NEWS_CSV

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36"
    ),
}


# ---------------------------------------------------------------------------
# RSS / API fetchers
# ---------------------------------------------------------------------------

def fetch_financialjuice():
    """FinancialJuice RSS feed."""
    news = []
    try:
        resp = HTTP_SESSION.get(
            "https://www.financialjuice.com/feed.ashx?xy=rss",
            headers=_HEADERS, timeout=10,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for item in root.findall("./channel/item"):
            title = item.findtext("title") or ""
            title = title.replace("FinancialJuice: ", "", 1)
            news.append({
                "timestamp": item.findtext("pubDate") or "",
                "source":    "FinancialJuice",
                "title":     title.strip(),
                "link":      item.findtext("link") or "",
            })
    except Exception as e:
        logger.warning("FinancialJuice fetch failed: %s", e)
    return news


def fetch_cointelegraph():
    """CoinTelegraph RSS — no key required."""
    news = []
    try:
        resp = HTTP_SESSION.get(
            "https://cointelegraph.com/rss",
            headers=_HEADERS, timeout=10,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for item in root.findall("./channel/item"):
            news.append({
                "timestamp": item.findtext("pubDate") or "",
                "source":    "CoinTelegraph",
                "title":     (item.findtext("title") or "").strip(),
                "link":      item.findtext("link") or "",
            })
    except Exception as e:
        logger.warning("CoinTelegraph fetch failed: %s", e)
    return news


def fetch_decrypt():
    """Decrypt RSS — no key required."""
    news = []
    try:
        resp = HTTP_SESSION.get(
            "https://decrypt.co/feed",
            headers=_HEADERS, timeout=10,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for item in root.findall("./channel/item"):
            news.append({
                "timestamp": item.findtext("pubDate") or "",
                "source":    "Decrypt",
                "title":     (item.findtext("title") or "").strip(),
                "link":      item.findtext("link") or "",
            })
    except Exception as e:
        logger.warning("Decrypt fetch failed: %s", e)
    return news


def fetch_macro_events():
    """ForexFactory USD macro calendar via XML — no key required.
    Filters to USD High/Medium impact events only."""
    events = []
    try:
        resp = HTTP_SESSION.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.xml",
            headers=_HEADERS, timeout=10,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for item in root.findall("./event"):
            country = item.findtext("country") or ""
            impact = item.findtext("impact") or ""
            if country != "USD" or impact not in ("High", "Medium"):
                continue
            date_str = item.findtext("date") or ""
            time_str = item.findtext("time") or ""
            actual = item.findtext("actual") or "N/A"
            events.append({
                "timestamp": f"{date_str} {time_str}".strip(),
                "event":     (item.findtext("title") or "").strip(),
                "impact":    impact,
                "actual":    actual if actual else "N/A",
                "forecast":  item.findtext("forecast") or "N/A",
                "previous":  item.findtext("previous") or "N/A",
            })
    except Exception as e:
        logger.warning("Macro events fetch failed: %s", e)
    return events


def fetch_coingecko_trending():
    """CoinGecko trending coins — free, no key.
    BTC in top trending = retail FOMO signal."""
    articles = []
    try:
        resp = HTTP_SESSION.get(
            "https://api.coingecko.com/api/v3/search/trending",
            headers=_HEADERS, timeout=10,
        )
        resp.raise_for_status()
        coins = resp.json().get("coins", [])

        rank = None
        for i, c in enumerate(coins[:15], 1):
            if c.get("item", {}).get("symbol", "").lower() == "btc":
                rank = i
                break

        articles.append({
            "timestamp": datetime.now(UTC).strftime(
                "%a, %d %b %Y %H:%M:%S +0000"),
            "source":          "CoinGecko",
            "title":           (f"BTC ranked #{rank} on CoinGecko trending"
                                if rank else "BTC not in CoinGecko top trending"),
            "link":            "https://www.coingecko.com/en/trending",
            "sentiment_label": "BULLISH" if rank else "NEUTRAL",
            "sentiment_score": 1 if rank else 0,
            "category":        "crypto",
        })
    except Exception as e:
        logger.warning("CoinGecko trending fetch failed: %s", e)
    return articles


def fetch_reddit_sentiment():
    """Reddit hot posts from r/Bitcoin and r/CryptoCurrency — free, no key.
    Scores titles with the same keyword sentiment engine and returns
    pre-scored article dicts."""
    articles = []
    subreddits = [("Bitcoin", "r/Bitcoin"), ("CryptoCurrency", "r/CryptoCurrency")]
    for _, sub_name in subreddits:
        try:
            resp = HTTP_SESSION.get(
                f"https://www.reddit.com/{sub_name}/hot.json?limit=10",
                headers={**_HEADERS, "Accept": "application/json"},
                timeout=10,
            )
            resp.raise_for_status()
            children = resp.json().get("data", {}).get("children", [])
            for post in children:
                data = post.get("data", {})
                title = data.get("title", "")
                label, score = analyze_sentiment(title)
                # Only include posts with non-neutral sentiment
                if label == "NEUTRAL":
                    continue
                articles.append({
                    "timestamp": datetime.now(UTC).strftime(
                        "%a, %d %b %Y %H:%M:%S +0000"),
                    "source":    f"Reddit/{sub_name}",
                    "title":     title,
                    "link":      f"https://www.reddit.com{data.get('permalink', '')}",
                    "sentiment_label": label,
                    "sentiment_score":  score,
                    "category":  "crypto",
                })
        except Exception as e:
            logger.warning("Reddit %s fetch failed: %s", sub_name, e)
    return articles


# ---------------------------------------------------------------------------
# Sentiment & classification
# ---------------------------------------------------------------------------

# Whole-word tokens (avoid false positives on short words)
_POSITIVE_WORDS = {
    "bull", "bulls", "gain", "gains", "rise", "rises",
    "buy", "bought", "soar", "soars",
}
_NEGATIVE_WORDS = {
    "bear", "bears", "loss", "losses", "sell", "sells",
    "ban", "bans", "drop", "drops", "dump",
}
# Longer unambiguous substrings
_POSITIVE_SUB = (
    "surge", "rally", "pump", "bullish", "profit", "approval",
    "breakout", "adoption", "upgrade", "accumulat",
)
_NEGATIVE_SUB = (
    "crash", "bearish", "decline", "hack", "exploit", "lawsuit",
    "crackdown", "downturn", "reject", "liquidat",
)


def analyze_sentiment(text):
    """Crypto-specific sentiment via keyword counting.

    Uses whole-word matching for short/ambiguous tokens and substring
    matching for longer unambiguous ones.  ``risk`` intentionally excluded
    — too contextual (risk-on is bullish for BTC).

    Word tokens are only counted when NOT already captured by a substring
    match to avoid double-counting (e.g. "bull" inside "bullish")."""
    text_lower = text.lower()
    words = {re.sub(r"[^a-z0-9]", "", w) for w in text_lower.split()}

    pos_sub_hits = sum(1 for s in _POSITIVE_SUB if s in text_lower)
    neg_sub_hits = sum(1 for s in _NEGATIVE_SUB if s in text_lower)

    pos_sub_covered = {w for w in _POSITIVE_WORDS if any(s.startswith(w) and s in text_lower for s in _POSITIVE_SUB)}
    neg_sub_covered = {w for w in _NEGATIVE_WORDS if any(s.startswith(w) and s in text_lower for s in _NEGATIVE_SUB)}

    score = (
        sum(1 for w in _POSITIVE_WORDS if w in words and w not in pos_sub_covered)
        + pos_sub_hits
        - sum(1 for w in _NEGATIVE_WORDS if w in words and w not in neg_sub_covered)
        - neg_sub_hits
    )
    if score > 0:
        return "BULLISH", score
    if score < 0:
        return "BEARISH", score
    return "NEUTRAL", 0


def analyze_geopolitical_impact(text):
    """Score geopolitical news for BTC safe-haven / risk-off behaviour.

    * **Financial system threats** → BULLISH (SVB '23: BTC +26% in 1 week)
    * **Military conflicts**     → BEARISH short-term (Ukraine '22: -15%)
    * **Stability / resolution** → BEARISH (reduces safe-haven demand)
    """
    _financial_risk = (
        "bank run", "bank failure", "bank collapse", "banking crisis",
        "currency crisis", "sovereign default", "debt default",
        "devaluation", "hyperinflation", "sanction", "swift", "collapse",
        "tariff", "trade war", "inflation",
    )
    _military_risk = (
        "war", "warfare", "invasion", "military", "missile", "nuclear",
        "airstrike", "bombing", "troops", "deployed", "armed forces",
        "coup", "conflict", "tension", "escalat", "attack",
    )
    _stability = (
        "ceasefire", "peace deal", "peace talks", "de-escalat",
        "trade deal", "agreement reached", "stabiliz", "recovery",
    )
    t = text.lower()
    fin = sum(1 for w in _financial_risk if w in t)
    mil = sum(1 for w in _military_risk if w in t)
    stab = sum(1 for w in _stability if w in t)
    net = fin - mil - stab
    if net > 0:
        return "BULLISH", net
    if net < 0:
        return "BEARISH", abs(net)
    return "NEUTRAL", 0


def is_crypto_related(text):
    """True if *text* mentions crypto topics.

    Short tokens use whole-word matching to avoid false positives
    like ``security`` → ``sec`` or ``ether`` → ``eth``."""
    _word_tokens = {"btc", "eth", "sec", "etf", "nft", "defi", "xrp"}
    _substrings = (
        "bitcoin", "crypto", "cryptocurrency", "ethereum", "binance",
        "coinbase", "blockchain", "altcoin", "stablecoin", "solana",
        "ripple",
    )
    t = text.lower()
    words = {re.sub(r"[^a-z0-9]", "", w) for w in t.split()}
    return bool(words & _word_tokens) or any(s in t for s in _substrings)


def is_geopolitical(text):
    """True if *text* describes a geopolitical / macro event that may
    impact BTC as a safe-haven asset."""
    _word_tokens = {"war", "coup", "tariff", "army", "troops",
                    "bombing", "collapse"}
    _substrings = (
        "invasion", "military", "missile", "nuclear", "sanction",
        "conflict", "ceasefire", "airstrike", "armed forces",
        "bank run", "bank failure", "bank collapse", "banking crisis",
        "currency crisis", "sovereign default", "debt default",
        "devaluation", "trade war", "hyperinflation", "de-escalat",
        "geopolit",
    )
    t = text.lower()
    words = {re.sub(r"[^a-z0-9]", "", w) for w in t.split()}
    return bool(words & _word_tokens) or any(s in t for s in _substrings)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def scrape_and_export():
    """Fetch, deduplicate, score, and export all news + macro data."""
    logger.info("Scraping news from sources ...")
    raw = []
    raw.extend(fetch_financialjuice())
    raw.extend(fetch_cointelegraph())
    raw.extend(fetch_decrypt())

    # Deduplicate by title
    seen, unique = set(), []
    for item in raw:
        key = item["title"].strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(item)

    filtered = []
    for item in unique:
        if is_crypto_related(item["title"]):
            label, score = analyze_sentiment(item["title"])
            item["sentiment_label"] = label
            item["sentiment_score"]  = score
            item["category"]         = "crypto"
            filtered.append(item)
        elif is_geopolitical(item["title"]):
            label, score = analyze_geopolitical_impact(item["title"])
            item["sentiment_label"] = label
            item["sentiment_score"]  = score
            item["category"]         = "geopolitical"
            filtered.append(item)

    filtered.extend(fetch_coingecko_trending())
    filtered.extend(fetch_reddit_sentiment())

    if filtered:
        crypto_count = sum(1 for a in filtered if a["category"] == "crypto")
        geo_count    = sum(1 for a in filtered
                           if a.get("category") == "geopolitical")
        pd.DataFrame(filtered).to_csv(NEWS_CSV, index=False)
        logger.info("Exported %d articles (%d crypto, %d geopolitical) → %s",
                     len(filtered), crypto_count, geo_count, NEWS_CSV)
    else:
        logger.warning("No relevant news found — CSV not updated.")

    logger.info("Scraping macro events ...")
    events = fetch_macro_events()
    if events:
        pd.DataFrame(events).to_csv(MACRO_CSV, index=False)
        logger.info("Exported %d macro events → %s", len(events), MACRO_CSV)
    else:
        logger.warning("No macro events found — CSV not updated.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    scrape_and_export()

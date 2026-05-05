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


def fetch_beincrypto():
    """BeInCrypto RSS — no key required."""
    news = []
    try:
        resp = HTTP_SESSION.get(
            "https://beincrypto.com/feed/",
            headers=_HEADERS, timeout=10,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for item in root.findall("./channel/item"):
            news.append({
                "timestamp": item.findtext("pubDate") or "",
                "source":    "BeInCrypto",
                "title":     (item.findtext("title") or "").strip(),
                "link":      item.findtext("link") or "",
            })
    except Exception as e:
        logger.warning("BeInCrypto fetch failed: %s", e)
    return news


def fetch_coindesk():
    """CoinDesk RSS — no key required."""
    news = []
    try:
        resp = HTTP_SESSION.get(
            "https://www.coindesk.com/arc/outboundfeeds/rss/",
            headers=_HEADERS, timeout=10,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for item in root.findall("./channel/item"):
            news.append({
                "timestamp": item.findtext("pubDate") or "",
                "source":    "CoinDesk",
                "title":     (item.findtext("title") or "").strip(),
                "link":      item.findtext("link") or "",
            })
    except Exception as e:
        logger.warning("CoinDesk fetch failed: %s", e)
    return news


def fetch_bitcoinist():
    """Bitcoinist RSS — no key required."""
    news = []
    try:
        resp = HTTP_SESSION.get(
            "https://bitcoinist.com/feed/",
            headers=_HEADERS, timeout=10,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for item in root.findall("./channel/item"):
            news.append({
                "timestamp": item.findtext("pubDate") or "",
                "source":    "Bitcoinist",
                "title":     (item.findtext("title") or "").strip(),
                "link":      item.findtext("link") or "",
            })
    except Exception as e:
        logger.warning("Bitcoinist fetch failed: %s", e)
    return news


# ---------------------------------------------------------------------------
# Sentiment & classification
# ---------------------------------------------------------------------------

# Whole-word tokens (avoid false positives on short words)
_POSITIVE_WORDS = {
    "bull", "bulls", "gain", "gains", "rise", "rises",
    "buy", "bought", "soar", "soars", "long", "win",
}
_NEGATIVE_WORDS = {
    "bear", "bears", "loss", "losses", "sell", "sells",
    "ban", "bans", "drop", "drops", "dump",
    # "short" intentionally excluded — ambiguous (short squeeze = bullish)
}
# Longer unambiguous substrings
_POSITIVE_SUB = (
    "surge", "rally", "pump", "bullish", "profit", "approval",
    "breakout", "adoption", "upgrade", "accumulat",
    # 2025+ ETF / institutional drivers
    "etf inflow", "record inflow", "whale accumulat",
    "institutional buy", "institutional demand",
    "halving", "supply shock", "short squeeze", "short liquidat",
    "regulatory clarity", "pro-bitcoin", "strategic reserve",
    "all-time high", "new high", "outperform",
)
_NEGATIVE_SUB = (
    "crash", "bearish", "decline", "hack", "exploit", "lawsuit",
    "crackdown", "downturn", "reject", "liquidat",
    # 2025+ ETF / institutional drivers
    "etf outflow", "record outflow", "whale dump",
    "whale sell", "institutional sell",
    "miner capitulation", "hash rate drop",
    "sec lawsuit", "doj", "regulatory crackdown",
    "delist", "suspension", "underperform",
)

# Negation prefixes — when these appear before a sentiment word, flip or skip
_NEGATION_PATTERNS = (
    "not ", "no ", "isn't ", "aren't ", "wasn't ", "weren't ",
    "doesn't ", "don't ", "didn't ", "won't ", "wouldn't ",
    "avoid ", "avoids ", "avoiding ", "despite ",
    "signs of ", "fears of ", "concerns of ",
)


def _strip_negations(text_lower):
    """Adjust sentiment for negation patterns.
    \"avoids sell-off\" → sell-off is negative, flip to positive.
    \"not bullish\" → bullish is positive, flip to negative.
    Returns (cleaned_text, adjustment) where adjustment is added to final score."""
    cleaned = text_lower
    adjustment = 0

    for neg in _NEGATION_PATTERNS:
        idx = cleaned.find(neg)
        if idx < 0:
            continue

        start = idx + len(neg)
        window = cleaned[start:start + 40]

        # Check substrings first (longer, more specific)
        for sub in sorted(_POSITIVE_SUB, key=len, reverse=True):
            if sub in window:
                cleaned = cleaned.replace(sub, "___", 1)
                adjustment -= 1  # negated positive → flip to negative
                break
        else:
            for sub in sorted(_NEGATIVE_SUB, key=len, reverse=True):
                if sub in window:
                    cleaned = cleaned.replace(sub, "___", 1)
                    adjustment += 1  # negated negative → flip to positive
                    break
            else:
                # Check word tokens
                window_words = window.split()[:3]
                for word in _POSITIVE_WORDS:
                    if word in window_words:
                        pos = cleaned.find(word, start)
                        if 0 <= pos <= start + 40:
                            cleaned = cleaned[:pos] + "___" + cleaned[pos+len(word):]
                            adjustment -= 1
                            break
                else:
                    for word in _NEGATIVE_WORDS:
                        if word in window_words:
                            pos = cleaned.find(word, start)
                            if 0 <= pos <= start + 40:
                                cleaned = cleaned[:pos] + "___" + cleaned[pos+len(word):]
                                adjustment += 1
                                break

    return cleaned, adjustment


def analyze_sentiment(text):
    """Crypto-specific sentiment via keyword counting with negation handling.

    Negation patterns (\"not bullish\", \"avoids crash\") are stripped before
    counting to prevent false signals.  ``risk`` intentionally excluded
    — too contextual (risk-on is bullish for BTC).

    Word tokens are only counted when NOT already captured by a substring
    match to avoid double-counting (e.g. \"bull\" inside \"bullish\")."""
    text_lower, neg_adj = _strip_negations(text.lower())
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
        + neg_adj  # flip sentiment for negated phrases
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
    raw.extend(fetch_beincrypto())
    raw.extend(fetch_coindesk())
    raw.extend(fetch_bitcoinist())

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

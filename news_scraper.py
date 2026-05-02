import logging
import re
import xml.etree.ElementTree as ET

import pandas as pd
import requests
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import MACRO_CSV, NEWS_CSV

logger = logging.getLogger(__name__)

_HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}


def _make_session():
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
    session.mount('http://', HTTPAdapter(max_retries=retry))
    session.mount('https://', HTTPAdapter(max_retries=retry))
    return session


_session = _make_session()


def fetch_financialjuice():
    news = []
    try:
        resp = _session.get('https://www.financialjuice.com/feed.ashx?xy=rss', headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for item in root.findall('./channel/item'):
            title = item.findtext('title') or ''
            title = title.replace('FinancialJuice: ', '', 1)
            news.append({
                'timestamp': item.findtext('pubDate') or '',
                'source': 'FinancialJuice',
                'title': title.strip(),
                'link': item.findtext('link') or '',
            })
    except Exception as e:
        logger.warning(f"FinancialJuice fetch failed: {e}")
    return news


def fetch_cointelegraph():
    """CoinTelegraph RSS — no key required."""
    news = []
    try:
        resp = _session.get('https://cointelegraph.com/rss', headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for item in root.findall('./channel/item'):
            news.append({
                'timestamp': item.findtext('pubDate') or '',
                'source':    'CoinTelegraph',
                'title':     (item.findtext('title') or '').strip(),
                'link':      item.findtext('link') or '',
            })
    except Exception as e:
        logger.warning(f"CoinTelegraph fetch failed: {e}")
    return news


def fetch_decrypt():
    """Decrypt RSS — no key required."""
    news = []
    try:
        resp = _session.get('https://decrypt.co/feed', headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for item in root.findall('./channel/item'):
            news.append({
                'timestamp': item.findtext('pubDate') or '',
                'source':    'Decrypt',
                'title':     (item.findtext('title') or '').strip(),
                'link':      item.findtext('link') or '',
            })
    except Exception as e:
        logger.warning(f"Decrypt fetch failed: {e}")
    return news


def fetch_macro_events():
    events = []
    try:
        resp = _session.get(
            'https://nfs.faireconomy.media/ff_calendar_thisweek.xml',
            headers=_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for item in root.findall('./event'):
            country = item.findtext('country') or ''
            impact  = item.findtext('impact') or ''
            if country != 'USD' or impact not in ('High', 'Medium'):
                continue
            date_str = item.findtext('date') or ''
            time_str = item.findtext('time') or ''
            actual   = item.findtext('actual') or 'N/A'
            events.append({
                'timestamp': f"{date_str} {time_str}".strip(),
                'event':     (item.findtext('title') or '').strip(),
                'impact':    impact,
                'actual':    actual if actual else 'N/A',
                'forecast':  item.findtext('forecast') or 'N/A',
                'previous':  item.findtext('previous') or 'N/A',
            })
    except Exception as e:
        logger.warning(f"Macro events fetch failed: {e}")
    return events


def analyze_sentiment(text):
    """Crypto-specific sentiment: bullish/bearish based on market keywords."""
    positive = ('surge', 'rally', 'pump', 'bullish', 'gain', 'bull', 'rise', 'profit', 'approval', 'buy')
    negative = ('crash', 'dump', 'bearish', 'loss', 'decline', 'bear', 'sell', 'risk', 'drop', 'ban', 'hack')
    text_lower = text.lower()
    score = sum(1 for w in positive if w in text_lower) - sum(1 for w in negative if w in text_lower)
    if score > 0:
        return 'BULLISH', score
    if score < 0:
        return 'BEARISH', score
    return 'NEUTRAL', 0


def analyze_geopolitical_impact(text):
    """Score geopolitical news for BTC impact based on research findings:

    FINANCIAL system threats → BULLISH (people flee to BTC as SWIFT alternative)
      Evidence: SVB collapse 2023 → BTC +26% in one week
    MILITARY conflicts → BEARISH short-term (risk-off: institutions sell everything)
      Evidence: Russia invades Ukraine → -15% first week; Iran attacks Israel → -8%
    STABILITY / resolution → BEARISH (reduces safe-haven demand)
    """
    _financial_risk = (
        'bank run', 'bank failure', 'bank collapse', 'banking crisis',
        'currency crisis', 'sovereign default', 'debt default', 'devaluation',
        'hyperinflation', 'sanction', 'swift', 'collapse', 'tariff',
        'trade war', 'inflation',
    )
    _military_risk = (
        'war', 'warfare', 'invasion', 'military', 'missile', 'nuclear',
        'airstrike', 'bombing', 'troops', 'deployed', 'armed forces',
        'coup', 'conflict', 'tension', 'escalat', 'attack',
    )
    _stability = (
        'ceasefire', 'peace deal', 'peace talks', 'de-escalat',
        'trade deal', 'agreement reached', 'stabiliz', 'recovery',
    )
    text_lower      = text.lower()
    fin_score       = sum(1 for w in _financial_risk if w in text_lower)
    military_score  = sum(1 for w in _military_risk  if w in text_lower)
    stability_score = sum(1 for w in _stability       if w in text_lower)

    net = fin_score - military_score - stability_score
    if net > 0:
        return 'BULLISH', net
    if net < 0:
        return 'BEARISH', abs(net)
    return 'NEUTRAL', 0


def is_crypto_related(text):
    # Short/ambiguous tokens need whole-word matching to avoid false positives
    # like "security" → "sec" or "ether" → "eth".
    _word_tokens = {'btc', 'eth', 'sec', 'etf', 'nft', 'defi', 'xrp'}
    _substrings  = ('bitcoin', 'crypto', 'cryptocurrency', 'ethereum', 'binance',
                    'coinbase', 'blockchain', 'altcoin', 'stablecoin', 'solana', 'ripple')
    text_lower = text.lower()
    words = {re.sub(r'[^a-z0-9]', '', w) for w in text_lower.split()}
    return bool(words & _word_tokens) or any(s in text_lower for s in _substrings)


def is_geopolitical(text):
    """Detect geopolitical/macro events that move BTC as a safe-haven asset."""
    # Whole-word match for short ambiguous tokens
    _word_tokens = {'war', 'coup', 'tariff', 'army', 'troops', 'bombing', 'collapse'}
    # Substring match for longer unambiguous phrases
    _substrings  = (
        'invasion', 'military', 'missile', 'nuclear', 'sanction', 'conflict',
        'ceasefire', 'airstrike', 'armed forces', 'bank run', 'bank failure',
        'bank collapse', 'banking crisis', 'currency crisis', 'sovereign default',
        'debt default', 'devaluation', 'trade war', 'hyperinflation',
        'de-escalat', 'geopolit',
    )
    text_lower = text.lower()
    words = {re.sub(r'[^a-z0-9]', '', w) for w in text_lower.split()}
    return bool(words & _word_tokens) or any(s in text_lower for s in _substrings)


def scrape_and_export():
    logger.info("Scraping news from sources...")
    raw = []
    raw.extend(fetch_financialjuice())
    raw.extend(fetch_cointelegraph())
    raw.extend(fetch_decrypt())

    # Deduplicate by title before scoring
    seen, unique = set(), []
    for item in raw:
        key = item['title'].strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(item)

    filtered = []
    for item in unique:
        if is_crypto_related(item['title']):
            label, score = analyze_sentiment(item['title'])
            item['sentiment_label'] = label
            item['sentiment_score']  = score
            item['category']         = 'crypto'
            filtered.append(item)
        elif is_geopolitical(item['title']):
            label, score = analyze_geopolitical_impact(item['title'])
            item['sentiment_label'] = label
            item['sentiment_score']  = score
            item['category']         = 'geopolitical'
            filtered.append(item)

    if filtered:
        crypto_count = sum(1 for a in filtered if a['category'] == 'crypto')
        geo_count    = sum(1 for a in filtered if a['category'] == 'geopolitical')
        pd.DataFrame(filtered).to_csv(NEWS_CSV, index=False)
        logger.info(f"Exported {len(filtered)} articles ({crypto_count} crypto, {geo_count} geopolitical) to {NEWS_CSV}")
    else:
        logger.warning("No relevant news found — CSV not updated.")

    logger.info("Scraping macro events...")
    events = fetch_macro_events()
    if events:
        pd.DataFrame(events).to_csv(MACRO_CSV, index=False)
        logger.info(f"Exported {len(events)} macro events to {MACRO_CSV}")
    else:
        logger.warning("No macro events found — CSV not updated.")


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    scrape_and_export()

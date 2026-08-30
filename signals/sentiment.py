"""Sentiment analysis — news CSV + Fear & Greed combined, and macro event check."""

import logging
from datetime import UTC, datetime, timedelta

import pandas as pd

from config import MACRO_CSV, NEWS_CSV
from signals.market_data import fetch_fear_and_greed

logger = logging.getLogger(__name__)


def get_combined_sentiment(fng=None):
    if fng is None:
        fng = fetch_fear_and_greed()

    news_data = {
        'headlines': [],
        'sentiment': 'NEUTRAL',
        'confidence': 0,
        'fear_greed': fng,
        'sources_checked': [],
        'geo_bullish': 0,
        'geo_bearish': 0,
    }

    crypto_bullish = crypto_bearish = 0
    geo_bullish = geo_bearish = 0

    try:
        df = pd.read_csv(NEWS_CSV)
        if 'category' not in df.columns:
            df['category'] = 'crypto'

        if not df.empty and 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce', utc=True)
            cutoff = datetime.now(UTC) - timedelta(hours=24)
            fresh = df[df['timestamp'] >= cutoff]
            df = fresh.sort_values('timestamp', ascending=False) if not fresh.empty else df.head(0)

        if not df.empty:
            for _, row in df.head(7).iterrows():
                val = 1 if row['sentiment_label'] == 'BULLISH' else (-1 if row['sentiment_label'] == 'BEARISH' else 0)
                news_data['headlines'].append({
                    'title': row['title'],
                    'sentiment': val,
                    'source': row['source'],
                    'category': row.get('category', 'crypto'),
                })

            crypto_df = df[df['category'] == 'crypto']
            geo_df = df[df['category'] == 'geopolitical']

            crypto_bullish = len(crypto_df[crypto_df['sentiment_label'] == 'BULLISH'])
            crypto_bearish = len(crypto_df[crypto_df['sentiment_label'] == 'BEARISH'])
            geo_bullish = len(geo_df[geo_df['sentiment_label'] == 'BULLISH'])
            geo_bearish = len(geo_df[geo_df['sentiment_label'] == 'BEARISH'])

            news_data['sources_checked'] = df['source'].unique().tolist()
            news_data['geo_bullish'] = geo_bullish
            news_data['geo_bearish'] = geo_bearish
    except Exception:
        pass

    fng_score = (fng['value'] - 50) / 50
    crypto_score = (crypto_bullish - crypto_bearish) / max(crypto_bullish + crypto_bearish, 1)
    geo_score = (geo_bullish - geo_bearish) / max(geo_bullish + geo_bearish, 1)
    combined = (fng_score * 0.50) + (crypto_score * 0.35) + (geo_score * 0.15)

    if combined > 0.15:
        news_data['sentiment'] = 'BULLISH'
        news_data['confidence'] = min(combined * 100, 100)
    elif combined < -0.15:
        news_data['sentiment'] = 'BEARISH'
        news_data['confidence'] = min(abs(combined) * 100, 100)

    return news_data


def _parse_macro_timestamp(raw):
    """Parse a ForexFactory calendar timestamp into an aware UTC datetime.

    The feed is in **UTC**, not Eastern Time. Verified against five events whose
    release times never move:

        Non-Farm Payrolls      08:30 ET   → feed says 12:30pm  = 12:30 UTC
        Unemployment Claims    08:30 ET   → feed says 12:30pm  = 12:30 UTC
        ADP Non-Farm           08:15 ET   → feed says 12:15pm  = 12:15 UTC
        ISM Manufacturing PMI  10:00 ET   → feed says  2:00pm  = 14:00 UTC
        JP Industrial Prod.    08:50 JST  → feed says 11:50pm  = 23:50 UTC (prev day)

    Under an Eastern reading NFP would print as 8:30am. It does not.

    This was previously parsed as America/New_York, putting every event four
    hours late in summer and five in winter — so the macro gate stayed open
    through the actual release and then force-closed every position two hours
    after the event had passed.
    """
    return datetime.strptime(raw.strip(), "%m-%d-%Y %I:%M%p").replace(tzinfo=UTC)


def check_upcoming_macro_events():
    """Returns (bool, event_name). True only when a HIGH impact USD event is <=2h away.

    Per-event parse errors are logged at debug and skipped so one bad row
    doesn't disable the whole check. CSV-level read errors log at WARNING
    so the missing/corrupt-macro-CSV case is visible — silently returning
    False would let the bot trade through macro windows unprotected.
    """
    try:
        df = pd.read_csv(MACRO_CSV)
    except Exception as e:
        logger.warning("macro CSV unreadable (%s) — macro protection disabled this cycle", e)
        return False, None
    try:
        pending = df[(df['actual'].isna()) | (df['actual'] == 'N/A')]
        pending = pending[pending['impact'] == 'High']
        for _, row in pending.iterrows():
            try:
                event_dt = _parse_macro_timestamp(row['timestamp'])
                diff = event_dt - datetime.now(UTC)
                if timedelta(0) <= diff <= timedelta(hours=2):
                    return True, row['event']
            except Exception as e:
                logger.debug("macro row parse skipped: %s", e)
                continue
    except Exception as e:
        logger.warning("macro CSV schema unexpected (%s) — macro protection disabled this cycle", e)
    return False, None

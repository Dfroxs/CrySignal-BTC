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


def check_upcoming_macro_events():
    """Returns (bool, event_name). True only when a HIGH impact USD event is <=2h away."""
    try:
        df = pd.read_csv(MACRO_CSV)
        pending = df[(df['actual'].isna()) | (df['actual'] == 'N/A')]
        pending = pending[pending['impact'] == 'High']
        for _, row in pending.iterrows():
            try:
                import zoneinfo
                _ET = zoneinfo.ZoneInfo("America/New_York")
                event_dt = datetime.strptime(row['timestamp'].strip(), "%m-%d-%Y %I:%M%p").replace(tzinfo=_ET).astimezone(UTC)
                diff = event_dt - datetime.now(UTC)
                if timedelta(0) <= diff <= timedelta(hours=2):
                    return True, row['event']
            except Exception:
                continue
    except Exception:
        pass
    return False, None

"""Market structure data fetchers — funding, L/S, DXY, S&P, stablecoins,
BTC dominance, open interest, Fear & Greed, cache helpers, adaptive threshold.

All functions that make external API calls live here.
"""

import json
import logging
import os
import time
from datetime import UTC, datetime, timedelta

import ccxt

from config import (
    ADAPTIVE_MAX_SIGNALS,
    ADAPTIVE_WINDOW_HOURS,
    BTC_DOM_CACHE_FILE,
    HTTP_SESSION,
    OI_CACHE_FILE,
    SIGNAL_THRESHOLD,
    SPOT_THRESHOLD,
    SPOT_THRESHOLD_MAX,
    SPOT_THRESHOLD_MIN,
    SPOT_THRESHOLD_STATE_FILE,
    STABLECOIN_CACHE_FILE,
    THRESHOLD_MAX,
    THRESHOLD_MIN,
    THRESHOLD_STATE_FILE,
    load_cache,
    save_cache,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared exchange instance — primary, with a public-data-mirror fallback
# ---------------------------------------------------------------------------

_MIRROR_HOST = "data-api.binance.vision"
_MIRROR_RETRY_AFTER_S = 1800  # re-probe the primary after 30 min on the mirror

# Reads the mirror can serve. Everything else stays on the primary — the mirror
# publishes spot market data only.
_MIRRORABLE = ("fetch_ohlcv", "fetch_ticker", "fetch_tickers", "fetch_order_book")


def _new_binance(mirror=False):
    """Build a rate-limited ccxt binance client, optionally pointed at the mirror.

    ``fetchMarkets=['spot']`` keeps ``load_markets()`` off fapi.binance.com,
    which the mirror does not serve and which is blocked wherever the primary is.
    """
    opts = {"options": {"fetchMarkets": ["spot"]}} if mirror else {}
    ex = ccxt.binance(opts)
    ex.enableRateLimit = True
    if mirror:
        for key in ("public", "v1"):
            if key in ex.urls["api"]:
                ex.urls["api"][key] = ex.urls["api"][key].replace(
                    "api.binance.com", _MIRROR_HOST
                )
    return ex


class _BinanceWithMirror:
    """ccxt.binance() that falls back to Binance's public data mirror for spot reads.

    api.binance.com is geo-restricted from some jurisdictions and answers 403 at
    the Cloudflare edge. ccxt surfaces that as a NetworkError on the first call,
    which took out Phase 2 and Phase 3 of a whole cycle on 2026-08-29.
    data-api.binance.vision serves the same public spot endpoints unrestricted,
    so OHLCV keeps flowing through the outage.

    Only the reads in ``_MIRRORABLE`` are retried. The mirror has no futures
    endpoints, so ``fetch_funding_rate`` / ``fetch_open_interest`` /
    ``fetch_long_short_ratio`` and the basis fetcher stay on fapi.binance.com and
    degrade to NEUTRAL on their own when it is blocked — the signal still scores,
    minus those conditions.

    The fallback is sticky so a tripped cycle does not pay the timeout twice, but
    expires after ``_MIRROR_RETRY_AFTER_S`` so a long ``--loop`` run returns to
    the primary (and its futures data) once the block lifts.
    """

    def __init__(self):
        object.__setattr__(self, "_primary", _new_binance(mirror=False))
        object.__setattr__(self, "_mirror", _new_binance(mirror=True))
        object.__setattr__(self, "_mirror_since", None)

    def _on_mirror(self):
        since = self._mirror_since
        if since is None:
            return False
        if time.monotonic() - since < _MIRROR_RETRY_AFTER_S:
            return True
        logger.info("Mirror cooldown elapsed — re-probing api.binance.com")
        object.__setattr__(self, "_mirror_since", None)
        return False

    def __getattr__(self, name):
        # Only reached when normal lookup fails, so the attributes set in
        # __init__ never land here. Non-mirrorable attributes — futures fetchers,
        # markets, config — always come from the primary.
        if name not in _MIRRORABLE:
            return getattr(self._primary, name)

        def call(*args, **kwargs):
            if not self._on_mirror():
                try:
                    return getattr(self._primary, name)(*args, **kwargs)
                except (ccxt.NetworkError, ccxt.PermissionDenied) as exc:
                    # A 403 whose body is a Binance JSON error becomes
                    # PermissionDenied (an ExchangeError), not NetworkError —
                    # the geo-block this wrapper exists for can arrive either way.
                    logger.warning(
                        "binance %s failed (%s) — falling back to %s for %ds",
                        name, exc, _MIRROR_HOST, _MIRROR_RETRY_AFTER_S,
                    )
                    object.__setattr__(self, "_mirror_since", time.monotonic())
            return getattr(self._mirror, name)(*args, **kwargs)

        return call

    def __setattr__(self, name, value):
        # Keep both clients configured identically (e.g. enableRateLimit).
        setattr(self._primary, name, value)
        setattr(self._mirror, name, value)


exchange = _BinanceWithMirror()

_CACHE_MAX_AGE_HOURS = 6


# ---------------------------------------------------------------------------
# Cache helper
# ---------------------------------------------------------------------------

def _cache_fresh(cache_dict, max_age_hours=_CACHE_MAX_AGE_HOURS):
    """Return True if *cache_dict* has a 'ts' key within *max_age_hours*."""
    ts_str = cache_dict.get("ts")
    if not ts_str:
        return False
    try:
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return datetime.now(UTC) - ts < timedelta(hours=max_age_hours)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Binance Futures data
# ---------------------------------------------------------------------------

def fetch_funding_rate():
    """Fetch funding rate + futures basis (mark vs index price spread)."""
    result = {
        'rate': 0.0, 'label': 'NEUTRAL', 'bias': 'NEUTRAL', 'rate_pct': 0.0,
        'basis_pct': 0.0, 'basis_bias': 'NEUTRAL',
    }
    try:
        resp = HTTP_SESSION.get(
            'https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT',
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        rate = float(data.get('lastFundingRate', 0))
        result['rate'] = rate
        result['rate_pct'] = rate * 100

        if rate > 0.05 / 100:
            result['label'] = 'VERY HIGH'
            result['bias'] = 'BEARISH'
        elif rate > 0.01 / 100:
            result['label'] = 'HIGH'
            result['bias'] = 'SLIGHTLY_BEARISH'
        elif rate < -0.05 / 100:
            result['label'] = 'VERY NEGATIVE'
            result['bias'] = 'BULLISH'
        elif rate < -0.01 / 100:
            result['label'] = 'NEGATIVE'
            result['bias'] = 'BULLISH'

        mark = float(data.get('markPrice', 0))
        index = float(data.get('indexPrice', 1))
        if index > 0:
            basis = ((mark - index) / index) * 100
            result['basis_pct'] = round(basis, 4)
            if basis > 0.10:
                result['basis_bias'] = 'BULLISH'
            elif basis < -0.10:
                result['basis_bias'] = 'BEARISH'

    except Exception as e:
        logger.warning("Funding Rate fetch failed: %s", e)
    return result


def fetch_long_short_ratio():
    result = {'ratio': 1.0, 'long_pct': 50.0, 'short_pct': 50.0, 'bias': 'NEUTRAL'}
    try:
        resp = HTTP_SESSION.get(
            'https://fapi.binance.com/futures/data/globalLongShortAccountRatio',
            params={'symbol': 'BTCUSDT', 'period': '1h', 'limit': 1},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()[0]
        ratio = float(data.get('longShortRatio', 1.0))
        result.update({
            'ratio': ratio,
            'long_pct': float(data.get('longAccount', 0.5)) * 100,
            'short_pct': float(data.get('shortAccount', 0.5)) * 100,
        })
        if ratio < 0.8:
            result['bias'] = 'BULLISH'
        elif ratio > 2.0:
            result['bias'] = 'BEARISH'
    except Exception as e:
        logger.warning("Long/Short Ratio fetch failed: %s", e)
    return result


def fetch_taker_buy_sell_ratio():
    """Taker buy/sell volume ratio — measures aggressive order flow dominance.
    >1.2 = buyers overwhelming sellers (bullish), <0.8 = sellers overwhelming buyers (bearish)."""
    result = {'ratio': 1.0, 'bias': 'NEUTRAL'}
    try:
        resp = HTTP_SESSION.get(
            'https://fapi.binance.com/futures/data/takerlongshortRatio',
            params={'symbol': 'BTCUSDT', 'period': '1h', 'limit': 1},
            headers={'User-Agent': 'curl/8.4'},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()[0]
        ratio = float(data.get('buySellRatio', 1.0))
        result['ratio'] = round(ratio, 3)
        if ratio >= 1.2:
            result['bias'] = 'BULLISH'
        elif ratio <= 0.8:
            result['bias'] = 'BEARISH'
    except Exception as e:
        logger.warning("Taker buy/sell ratio fetch failed: %s", e)
    return result


def fetch_open_interest():
    """Open Interest via ccxt, with HTTP fallback."""
    result = {'notional': 0.0, 'change_pct': 0.0, 'trend': 'NEUTRAL', 'bias': 'NEUTRAL'}
    oi = None
    try:
        raw = exchange.fetch_open_interest('BTC/USDT')
        oi = float(raw.get('openInterestAmount', 0) or raw.get('info', {}).get('openInterest', 0))
    except Exception as e1:
        logger.debug("ccxt OI fetch failed (%s), trying direct HTTP", e1)
        try:
            resp = HTTP_SESSION.get(
                'https://fapi.binance.com/fapi/v1/openInterest',
                params={'symbol': 'BTCUSDT'},
                headers={'User-Agent': 'curl/8.4'},
                timeout=5,
            )
            resp.raise_for_status()
            oi = float(resp.json().get('openInterest', 0))
        except Exception as e2:
            logger.warning("Open Interest fetch failed: %s", e2)
            return result

    if oi is None:
        return result

    # oi is in BTC — convert to USD notional
    try:
        price_resp = HTTP_SESSION.get(
            'https://fapi.binance.com/fapi/v1/ticker/price',
            params={'symbol': 'BTCUSDT'},
            headers={'User-Agent': 'curl/8.4'},
            timeout=5,
        )
        btc_price = float(price_resp.json().get('price', 0))
        if btc_price > 0:
            oi = oi * btc_price
    except Exception:
        pass  # keep raw BTC value; display will still work

    result['notional'] = round(oi, 2)

    prev_oi = None
    if os.path.exists(OI_CACHE_FILE):
        with open(OI_CACHE_FILE) as f:
            cached = json.load(f)
        if _cache_fresh(cached):
            prev_oi = cached.get('oi')
        else:
            logger.warning("Open Interest cache stale — skipping comparison")

    save_cache(OI_CACHE_FILE, {'oi': oi})

    if prev_oi and prev_oi > 0:
        change_pct = ((oi - prev_oi) / prev_oi) * 100
        result['change_pct'] = round(change_pct, 3)
        if change_pct > 1.0:
            result['trend'] = 'RISING'
            result['bias'] = 'BULLISH'
        elif change_pct < -1.0:
            result['trend'] = 'FALLING'
            result['bias'] = 'BEARISH'
    return result


# ---------------------------------------------------------------------------
# Macro market data (Yahoo Finance)
# ---------------------------------------------------------------------------

def fetch_dxy_trend():
    result = {'current': 0.0, 'change_pct': 0.0, 'trend': 'NEUTRAL', 'bias': 'NEUTRAL'}
    try:
        resp = HTTP_SESSION.get(
            'https://query2.finance.yahoo.com/v8/finance/chart/DX-Y.NYB',
            params={'interval': '1d', 'range': '5d'},
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=8,
        )
        resp.raise_for_status()
        closes = resp.json()['chart']['result'][0]['indicators']['quote'][0]['close']
        closes = [c for c in closes if c is not None]
        if len(closes) >= 2:
            current, prev = closes[-1], closes[-2]
            change_pct = ((current - prev) / prev) * 100
            result.update({'current': round(current, 3), 'change_pct': round(change_pct, 3)})
            if change_pct > 0.3:
                result['trend'] = 'RISING'
                result['bias'] = 'BEARISH'
            elif change_pct < -0.3:
                result['trend'] = 'FALLING'
                result['bias'] = 'BULLISH'
            else:
                result['trend'] = 'FLAT'
    except Exception as e:
        logger.warning("DXY fetch failed: %s", e)
    return result


def fetch_sp500_trend():
    """S&P 500 daily trend via Yahoo Finance."""
    result = {'current': 0.0, 'change_pct': 0.0, 'trend': 'NEUTRAL', 'bias': 'NEUTRAL'}
    try:
        resp = HTTP_SESSION.get(
            'https://query2.finance.yahoo.com/v8/finance/chart/%5EGSPC',
            params={'interval': '1d', 'range': '5d'},
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=8,
        )
        resp.raise_for_status()
        closes = resp.json()['chart']['result'][0]['indicators']['quote'][0]['close']
        closes = [c for c in closes if c is not None]
        if len(closes) >= 2:
            current, prev = closes[-1], closes[-2]
            change_pct = ((current - prev) / prev) * 100
            result.update({'current': round(current, 2), 'change_pct': round(change_pct, 3)})
            if change_pct > 0.5:
                result['trend'] = 'RISING'
                result['bias'] = 'BULLISH'
            elif change_pct < -0.5:
                result['trend'] = 'FALLING'
                result['bias'] = 'BEARISH'
            else:
                result['trend'] = 'FLAT'
    except Exception as e:
        logger.warning("S&P500 fetch failed: %s", e)
    return result


# ---------------------------------------------------------------------------
# On-chain / CoinGecko data
# ---------------------------------------------------------------------------

def fetch_stablecoin_supply():
    """USDT + USDC combined market cap trend via CoinGecko."""
    result = {'total_b': 0.0, 'change_pct': 0.0, 'trend': 'NEUTRAL', 'bias': 'NEUTRAL'}
    try:
        resp = HTTP_SESSION.get(
            'https://api.coingecko.com/api/v3/simple/price',
            params={
                'ids': 'tether,usd-coin',
                'vs_currencies': 'usd',
                'include_market_cap': 'true',
            },
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        usdt_cap = data.get('tether', {}).get('usd_market_cap', 0)
        usdc_cap = data.get('usd-coin', {}).get('usd_market_cap', 0)
        total_now = (usdt_cap + usdc_cap) / 1e9
        result['total_b'] = round(total_now, 1)

        prev_total = None
        if os.path.exists(STABLECOIN_CACHE_FILE):
            with open(STABLECOIN_CACHE_FILE) as f:
                cached = json.load(f)
            if _cache_fresh(cached):
                prev_total = cached.get('total_b')
            else:
                logger.warning("Stablecoin cache stale — skipping comparison")

        save_cache(STABLECOIN_CACHE_FILE, {'total_b': total_now})

        if prev_total and prev_total > 0:
            change_pct = ((total_now - prev_total) / prev_total) * 100
            result['change_pct'] = round(change_pct, 3)
            if change_pct > 0.5:
                result['trend'] = 'RISING'
                result['bias'] = 'BULLISH'
            elif change_pct < -0.5:
                result['trend'] = 'FALLING'
                result['bias'] = 'BEARISH'
    except Exception as e:
        logger.warning("Stablecoin supply fetch failed: %s", e)
    return result


def fetch_btc_dominance():
    """BTC dominance via CoinGecko /global."""
    result = {'current': 0.0, 'change_pct': 0.0, 'trend': 'NEUTRAL', 'bias': 'NEUTRAL'}
    try:
        resp = HTTP_SESSION.get('https://api.coingecko.com/api/v3/global',
                                headers={'User-Agent': 'Mozilla/5.0'}, timeout=8)
        resp.raise_for_status()
        data = resp.json()['data']
        btc_dom = data.get('market_cap_percentage', {}).get('btc', 0)
        result['current'] = round(btc_dom, 1)

        prev_btc_dom = None
        if os.path.exists(BTC_DOM_CACHE_FILE):
            with open(BTC_DOM_CACHE_FILE) as f:
                cached = json.load(f)
            if _cache_fresh(cached):
                prev_btc_dom = cached.get('btc_dom')
            else:
                logger.warning("BTC dominance cache stale — skipping comparison")

        save_cache(BTC_DOM_CACHE_FILE, {'btc_dom': btc_dom})

        if prev_btc_dom and prev_btc_dom > 0:
            change_pct = btc_dom - prev_btc_dom
            result['change_pct'] = round(change_pct, 2)
            if change_pct > 0.5:
                result['trend'] = 'RISING'
                result['bias'] = 'BULLISH'
            elif change_pct < -0.5:
                result['trend'] = 'FALLING'
                result['bias'] = 'BEARISH'
    except Exception as e:
        logger.warning("BTC dominance fetch failed: %s", e)
    return result


# ---------------------------------------------------------------------------
# Fear & Greed
# ---------------------------------------------------------------------------

def fetch_fear_and_greed():
    result = {'value': 50, 'label': 'NEUTRAL', 'sentiment': 'NEUTRAL'}
    try:
        resp = HTTP_SESSION.get('https://api.alternative.me/fng/?limit=1', timeout=5)
        resp.raise_for_status()
        data = resp.json()['data'][0]
        val = int(data['value'])
        result['value'] = val
        result['label'] = data['value_classification']
        if val >= 60:
            result['sentiment'] = 'BULLISH'
        elif val <= 40:
            result['sentiment'] = 'BEARISH'
    except Exception as e:
        logger.warning("Fear & Greed fetch failed: %s", e)
    return result


# ---------------------------------------------------------------------------
# Gold & VIX — macro correlation
# ---------------------------------------------------------------------------

def fetch_gold_price():
    """Fetch gold futures price via Yahoo Finance (GC=F)."""
    try:
        resp = HTTP_SESSION.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/GC%3DF?interval=1d&range=2d",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        result = resp.json()["chart"]["result"][0]
        closes = [c for c in result["indicators"]["quote"][0]["close"] if c is not None]
        if len(closes) < 2:
            return {"current": 0, "change_pct": 0}
        current, prev = closes[-1], closes[-2]
        return {"current": round(current, 2), "change_pct": round((current - prev) / prev * 100, 2)}
    except Exception:
        logger.debug("Gold fetch failed")
        return {"current": 0, "change_pct": 0}


def fetch_vix():
    """Fetch CBOE VIX via Yahoo Finance (^VIX)."""
    try:
        resp = HTTP_SESSION.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1d&range=2d",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        result = resp.json()["chart"]["result"][0]
        closes = [c for c in result["indicators"]["quote"][0]["close"] if c is not None]
        if len(closes) < 2:
            return {"current": 0, "change_pct": 0}
        current, prev = closes[-1], closes[-2]
        return {"current": round(current, 2), "change_pct": round((current - prev) / prev * 100, 2)}
    except Exception:
        logger.debug("VIX fetch failed")
        return {"current": 0, "change_pct": 0}


# ---------------------------------------------------------------------------
# Signal confidence
# ---------------------------------------------------------------------------

def get_signal_confidence(strength, threshold, htf=None, signal_type=None):
    """Return 'STRONG', 'NORMAL', or 'WEAK'.

    STRONG now requires HTF agreement, not just raw score (audit #9). Backtest
    showed STRONG WR 12.5% vs NORMAL 40% — high-score signals fire when *every*
    condition aligns, which historically happens at local TOPS in a bull leg.
    Requiring the 1D HTF to agree with the direction filters those out.
    """
    if strength >= threshold * 1.5:
        if htf and signal_type:
            d1 = htf.get('1d')
            if (signal_type == 'BUY' and d1 == 'BULLISH') or \
               (signal_type == 'SELL' and d1 == 'BEARISH'):
                return "STRONG"
            # Score is in STRONG zone but HTF disagrees → downgrade.
            return "NORMAL"
        return "STRONG"
    if strength >= threshold * 1.2:
        return "NORMAL"
    return "WEAK"


# ---------------------------------------------------------------------------
# Adaptive threshold
# ---------------------------------------------------------------------------

_MIN_WR_SAMPLE = 5  # raised from 3 — 3 trades = ±33% noise, not meaningful


def _get_recent_win_rate(mode, hours=72):
    """Return win rate (0-1) for positions closed within the window, or None
    when fewer than _MIN_WR_SAMPLE resolved trades exist.

    Denominator only includes resolved WIN/LOSS outcomes — VOL_EXIT, TIME_EXIT,
    FUNDING_EXIT, MACRO_CLOSE, BREAKER_CLOSE, FLIP are excluded so they don't
    artificially deflate the rate (and trigger aggressive threshold raises).
    """
    try:
        from trading.history import _conn
        c = _conn()
        cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        rows = c.execute(
            """SELECT outcome FROM paper_positions
               WHERE outcome IN ('WIN','LOSS') AND mode = ? AND closed_at >= ?""",
            (mode, cutoff),
        ).fetchall()
        if len(rows) < _MIN_WR_SAMPLE:
            return None
        wins = sum(1 for r in rows if r["outcome"] == "WIN")
        return wins / len(rows)
    except Exception:
        return None


def _get_adaptive_threshold(base, t_min, t_max, state_file, env_var):
    """Shared adaptive threshold logic with multi-window signal-quality awareness.

    Two windows:
    - 24h fast window: rapid response to a losing streak — raises threshold aggressively
    - 72h standard window: steady-state frequency control
    """
    override = float(os.getenv(env_var, 0))
    if override > 0:
        return override

    state = load_cache(state_file)
    now = datetime.now(UTC)
    all_ts = state.get("signals", [])

    mode = "spot" if "spot" in state_file else "futures"
    wr_72h = _get_recent_win_rate(mode, hours=72)
    wr_24h = _get_recent_win_rate(mode, hours=24)

    # 24h fast window: 4+ signals with poor win rate → raise aggressively
    cutoff_24h = (now - timedelta(hours=24)).isoformat()
    recent_24h = [ts for ts in all_ts if ts > cutoff_24h]
    if len(recent_24h) >= 4 and wr_24h is not None and wr_24h < 0.30:
        base = min(base + 1.0, t_max)
        return base

    # 72h standard window: frequency + quality control
    cutoff_72h = (now - timedelta(hours=ADAPTIVE_WINDOW_HOURS)).isoformat()
    recent_72h = [ts for ts in all_ts if ts > cutoff_72h]

    if len(recent_72h) > ADAPTIVE_MAX_SIGNALS:
        # Many signals: raise threshold. Raise more if win rate is poor.
        step = 0.75 if (wr_72h is not None and wr_72h < 0.35) else 0.5
        base = min(base + step, t_max)
    elif len(recent_72h) == 0 and len(all_ts) > 0:
        # No recent signals: lower threshold. Lower more if win rate is good.
        step = 0.5 if (wr_72h is not None and wr_72h >= 0.6) else 0.25
        base = max(base - step, t_min)
    return base


def _update_threshold_state(signal_type, state_file):
    """Shared adaptive threshold state update."""
    if signal_type == "HOLD":
        return
    state = load_cache(state_file)
    signals = state.get("signals", [])
    signals.append(datetime.now(UTC).isoformat())
    cutoff = (datetime.now(UTC) - timedelta(hours=ADAPTIVE_WINDOW_HOURS * 2)).isoformat()
    save_cache(state_file, {"signals": [ts for ts in signals if ts > cutoff]})


def get_adaptive_threshold():
    return _get_adaptive_threshold(
        SIGNAL_THRESHOLD, THRESHOLD_MIN, THRESHOLD_MAX,
        THRESHOLD_STATE_FILE, "SIGNAL_THRESHOLD",
    )


def get_spot_adaptive_threshold():
    return _get_adaptive_threshold(
        SPOT_THRESHOLD, SPOT_THRESHOLD_MIN, SPOT_THRESHOLD_MAX,
        SPOT_THRESHOLD_STATE_FILE, "SPOT_THRESHOLD",
    )


def update_threshold_state(signal_type):
    _update_threshold_state(signal_type, THRESHOLD_STATE_FILE)


def update_spot_threshold_state(signal_type):
    _update_threshold_state(signal_type, SPOT_THRESHOLD_STATE_FILE)

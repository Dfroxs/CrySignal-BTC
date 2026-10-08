"""Score variants: the SAME engine on rewritten market-structure biases, logged, never traded.

Why this exists. In paper run 2 the futures-only conditions almost never scored:
funding 0 of 92 cycles, L/S 0, basis 0, OI 4. Their absolute bands (basis ±0.10%,
L/S < 0.8 or > 2.0, funding ±0.01%) sit outside everything the market printed in 40
days, so ~7 of the 26.5-point futures ceiling could never be earned while the
threshold is still derived from all 26.5. Meanwhile basis and L/S varied *inside*
those bands in a way that tracked forward returns
(docs/superpowers/specs/2026-10-09-no-positions-diagnosis.md, live IC scan).

A variant reads each value against its own trailing week instead (a z-score) and
hands the engine the resulting bias. The engine itself is untouched, so a variant
cannot drift from the real scoring path. Variants are recorded in `cycle_log.variants`
and turned into paper books offline (scripts/variant_books.py). They never open a
position.

Variant parameters are fixed here before any variant figure exists. See the
pre-registration in docs/superpowers/specs/ before changing one.
"""
from __future__ import annotations

import copy
import logging
import math

logger = logging.getLogger(__name__)

WINDOW_HOURS = 168      # trailing week of hourly futures cycles
MIN_HISTORY = 48        # fewer than two days of history scores nothing
Z_BAND = 1.0

# Direction per field: +1 means "higher than usual is bullish".
VARIANTS = {
    # Relative bands, the engine's own semantics: funding and L/S contrarian
    # (high = longs crowded), basis momentum (premium = long demand).
    "rel_engine_dir": {"dirs": {"funding": -1, "ls": -1, "basis": +1}},
    # Relative bands, direction from the sign of the 40-day live IC: all momentum.
    "rel_ic_dir": {"dirs": {"funding": +1, "ls": +1, "basis": +1}},
}

# What a fetch failure leaves behind. A z-score of a placeholder is not a reading.
_PLACEHOLDER = {"funding": 0.0, "ls": 1.0, "basis": 0.0}

_KEEP = ("type", "strength", "buy_score", "sell_score", "confidence", "entry_price",
         "stop_loss", "take_profit", "tp2", "atr", "_threshold")


def relative_bias(value, history, direction, z=Z_BAND, min_n=MIN_HISTORY):
    """'BULLISH' / 'BEARISH' / 'NEUTRAL' for `value` against `history`, signed by
    `direction`. Thin history, a missing value or a flat history scores NEUTRAL."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "NEUTRAL"
    h = [float(x) for x in history if x is not None and not math.isnan(float(x))]
    if len(h) < min_n:
        return "NEUTRAL"
    mean = sum(h) / len(h)
    sd = math.sqrt(sum((x - mean) ** 2 for x in h) / (len(h) - 1))
    if sd == 0:
        return "NEUTRAL"
    score = (float(value) - mean) / sd * direction
    if score > z:
        return "BULLISH"
    if score < -z:
        return "BEARISH"
    return "NEUTRAL"


def _bias(field, value, history, spec):
    if value is None or value == _PLACEHOLDER[field]:
        return "NEUTRAL"
    return relative_bias(value, history, spec["dirs"][field], spec.get("z", Z_BAND))


def apply_variant(market_structure, history, spec):
    """A deep copy of `market_structure` with funding, L/S and basis biases re-derived
    from `history` ({'funding_rate': [...], 'ls_ratio': [...], 'basis_pct': [...]})."""
    if not market_structure:
        return market_structure
    ms = copy.deepcopy(market_structure)
    funding = ms.setdefault("funding", {})
    ls = ms.setdefault("long_short", {})
    funding["bias"] = _bias("funding", funding.get("rate_pct"),
                            history.get("funding_rate", []), spec)
    funding["basis_bias"] = _bias("basis", funding.get("basis_pct"),
                                  history.get("basis_pct", []), spec)
    ls["bias"] = _bias("ls", ls.get("ratio"), history.get("ls_ratio", []), spec)
    return ms


def compact(signal):
    """The fields a replay needs to gate and simulate a signal, JSON-safe."""
    out = {k: _plain(signal.get(k)) for k in _KEEP if k in signal}
    reg = signal.get("_regime") or {}
    out["_regime"] = {"regime": reg.get("regime"), "trend_dir": reg.get("trend_dir")}
    return out


def _plain(v):
    try:
        return v.item()            # numpy scalar → Python
    except AttributeError:
        return v


def score_variants(df, htf, market_structure, sr, mode, threshold, news_data, history,
                   base=None):
    """{name: compact signal} for every variant, plus 'base' when given.

    Each variant goes through the same news overlay the real signal went through, so
    the macro gate and Fear & Greed treat every variant identically. A variant that
    raises is logged and skipped: nothing here may cost the real cycle.
    """
    from signals.engine import generate_signals, integrate_news_with_signal
    out = {}
    if base is not None:
        out["base"] = compact(base)
    for name, spec in VARIANTS.items():
        try:
            ms = apply_variant(market_structure, history, spec)
            sig = generate_signals(df, htf, ms, sr, mode=mode, threshold_override=threshold)
            if news_data:
                sig = integrate_news_with_signal(sig, news_data, htf)
            out[name] = compact(sig)
        except Exception as exc:                      # never cost the real cycle
            logger.warning("variant %s failed: %s", name, exc)
    return out


def attach_variants(signal, df, htf, market_structure, sr, mode, threshold, news_data,
                    history_fn=None):
    """Score every variant and store them on `signal['_variants']` for log_cycle.

    Wrapped whole: a failing history read or variant leaves the real signal exactly as
    it was and records nothing. `history_fn` is injectable for tests; live reads
    `trading.history.get_market_history`.
    """
    try:
        if history_fn is None:
            from trading.history import get_market_history as history_fn
        history = history_fn(hours=WINDOW_HOURS)
        signal["_variants"] = score_variants(df, htf, market_structure, sr, mode,
                                             threshold, news_data, history, base=signal)
    except Exception as exc:
        logger.warning("variants skipped this cycle: %s", exc)

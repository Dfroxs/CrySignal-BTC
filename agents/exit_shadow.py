"""Exit shadow: Claude and DeepSeek judge each OPEN position every hour, CLOSE or HOLD.
Logged, never acted on.

`agents/shadow.py` only ever judged entries. This asks the other half of the trading
decision: for each position still open after a full cycle, would closing it NOW end
better than letting the bot's own exits (stop, targets, trailing stop, 72h cap) run?
The answer goes to `shadow_exit_opinions` and nothing else: no position is closed
because of it, and a failing or slow provider costs the cycle nothing (it runs in a
background thread, like the entry shadow).

What the models see is numbers and fixed labels only: the position as the bot holds it
(P&L if closed now by the same `_calc_pnl` the bot books with, time held and left, the
current trailing stop and targets measured from the current price), the market now, and
the mode's exit rules. Scored by `scripts/exit_shadow_eval.py`, per the run-3
pre-registration addendum H-X.

`agents/shadow.py` is frozen for H-S, so its helpers are imported, never edited.
"""
from __future__ import annotations

import copy
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import UTC, datetime

from agents.shadow import TIMEOUT_S, _label, _num, _providers, build_context, exit_terms

logger = logging.getLogger(__name__)
_threads = []

EXIT_SYSTEM = (
    "You review an OPEN position held by a rule-based BTC/USDT paper-trading bot. You "
    "receive only numbers. If you say HOLD, the bot keeps managing the position by "
    "`exit_rules`: its trailing stop, its targets (half closes at the first), and a forced "
    "close at `max_hold_hours`. If you say CLOSE, the whole position is closed now at "
    "`price`, for `pnl_if_closed_now_pct`. Choose CLOSE only if you expect closing now to "
    "end with a better net result than letting the rules run. Reply with ONLY a JSON "
    'object: {"verdict": "CLOSE" or "HOLD", "confidence": integer 0-100, '
    '"reason": "at most 25 words, in Indonesian"}.'
)


def _hours_since(iso):
    try:
        t = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    return (datetime.now(UTC) - t).total_seconds() / 3600


def build_exit_context(pos, price, signal):
    """Facts for one open position at `price`, plus the market from `signal` (the
    current cycle's signal for that mode, or the other mode's when it has none)."""
    from trading.paper import _calc_pnl
    mode = pos.get("mode", "futures")
    _, rules = exit_terms({"mode": mode})
    held = _hours_since(pos.get("opened_at"))
    from_price = lambda lvl: _num((lvl - price) / price * 100) if price and _num(lvl) else None
    stop = pos.get("trailing_stop") or pos.get("stop_loss")
    ctx = {"time_utc": datetime.now(UTC).strftime("%Y-%m-%d %H:00"),
           "position": {"mode": _label(mode), "side": _label(pos.get("type")),
                        "entry_price": _num(pos.get("entry_price")), "price": _num(price),
                        "pnl_if_closed_now_pct": _num(_calc_pnl(pos, price)),
                        "first_target_already_hit": bool(pos.get("partial_closed")),
                        "hours_held": _num(held),
                        "hours_left_before_cap": _num(rules["max_hold_hours"] - held)
                        if held is not None else None,
                        "stop_pct_from_price": from_price(stop),
                        "first_target_pct_from_price": from_price(pos.get("tp1") or pos.get("take_profit")),
                        "second_target_pct_from_price": from_price(pos.get("tp2")),
                        "atr_pct_at_entry": _num(pos["atr"] / pos["entry_price"] * 100)
                        if _num(pos.get("atr")) and _num(pos.get("entry_price")) else None},
           "exit_rules": rules}
    if signal:
        now = build_context(signal)
        ctx.update(price_now=now["price"], htf=now["htf"], market=now["market"],
                   engine_now={k: now["signal"][k] for k in
                               ("type", "strength", "threshold", "buy_score", "sell_score")})
    return ctx


def parse_exit_opinion(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("no JSON object in reply")
    o = json.loads(m.group(0))
    verdict, conf, reason = o.get("verdict"), o.get("confidence"), o.get("reason")
    if verdict not in ("CLOSE", "HOLD"):
        raise ValueError(f"bad verdict {verdict!r}")
    if not isinstance(conf, int) or isinstance(conf, bool) or not 0 <= conf <= 100:
        raise ValueError(f"bad confidence {conf!r}")
    return {"verdict": verdict, "confidence": conf, "reason": str(reason or "")[:300]}


def _base(pos, price, ctx, provider):
    p = ctx["position"]
    return {"timestamp": datetime.now(UTC).isoformat(), "position_id": pos.get("id"),
            "mode": pos.get("mode"), "side": pos.get("type"),
            "entry_price": _num(pos.get("entry_price")), "price": _num(price),
            "pnl_if_closed_pct": p["pnl_if_closed_now_pct"], "hours_held": p["hours_held"],
            "provider": provider, "model": None, "verdict": None, "opinion_confidence": None,
            "reason": None, "error": None, "latency_ms": None,
            "input_tokens": None, "output_tokens": None}


def _one(pos, price, ctx, provider, prompt, ask_fn):
    rec, t0 = _base(pos, price, ctx, provider), time.time()
    try:
        reply = ask_fn(prompt, system=EXIT_SYSTEM, provider=provider, max_tokens=16000)
        rec.update(model=reply.model, input_tokens=reply.input_tokens,
                   output_tokens=reply.output_tokens)
        o = parse_exit_opinion(reply.text)
        rec.update(verdict=o["verdict"], opinion_confidence=o["confidence"], reason=o["reason"])
    except Exception as exc:          # a shadow failure is data, never an exception upward
        rec["error"] = f"{type(exc).__name__}: {exc}"[:300]
    rec["latency_ms"] = int((time.time() - t0) * 1000)
    return rec


def exit_opinions_for(pos, price, signal, providers, ask_fn, timeout=TIMEOUT_S):
    ctx = build_exit_context(pos, price, signal)
    prompt = "Open position facts (JSON):\n" + json.dumps(ctx, indent=1)
    pool = ThreadPoolExecutor(max_workers=max(len(providers), 1))
    futs = {pool.submit(_one, pos, price, ctx, p, prompt, ask_fn): p for p in providers}
    done, _ = wait(futs, timeout=timeout)
    pool.shutdown(wait=False, cancel_futures=True)
    out = []
    for f, p in futs.items():
        if f in done:
            out.append(f.result())
        else:
            rec = _base(pos, price, ctx, p)
            rec.update(error="timeout", latency_ms=int(timeout * 1000))
            out.append(rec)
    return out


def run_exit_shadow(positions, price, spot_signal, futures_signal, providers=None,
                    ask_fn=None, log_fn=None, background=False):
    """Entry point for run_bot, once per full cycle. Returns how many positions were put
    to the agents. Inputs are deep-copied before a background thread sees them."""
    providers = providers if providers is not None else _providers()
    if not positions or not price or not providers:
        return 0
    if ask_fn is None:
        from agents.llm import ask as ask_fn
    jobs = [(copy.deepcopy(p), copy.deepcopy(
        (spot_signal if p.get("mode") == "spot" and spot_signal else futures_signal) or spot_signal))
        for p in positions]

    def work():
        conn, log = None, log_fn
        if log is None:
            import sqlite3
            from trading import history
            history._conn()
            conn = sqlite3.connect(history.SIGNAL_HISTORY_DB, timeout=30)
            log = lambda rec: history.log_exit_opinion(rec, conn=conn)
        try:
            for pos, sig in jobs:
                for rec in exit_opinions_for(pos, price, sig, providers, ask_fn):
                    try:
                        log(rec)
                    except Exception as exc:
                        logger.warning("exit opinion not stored: %s", exc)
        finally:
            if conn is not None:
                conn.close()

    if background:
        t = threading.Thread(target=work, name="exit-shadow", daemon=False)
        t.start()
        _threads[:] = [x for x in _threads if x.is_alive()] + [t]
    else:
        work()
    return len(jobs)

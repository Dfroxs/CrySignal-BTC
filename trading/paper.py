"""Paper trading — trailing stop + partial take-profit position management.

Each cycle:
  1. Force-close all positions if HIGH impact macro event is <2h away.
  2. Advance trailing stop if price moved in our favour.
  3. First half exits at TP1 → trailing stop moves to breakeven.
  4. Second half exits at TP2 or when trailing stop is hit.
"""

import logging
from datetime import UTC, datetime, timedelta

import trading.history as sh
from config import EXECUTION_CONFIG, FUTURES_CONFIG, RISK_CONFIG

logger = logging.getLogger(__name__)


def _trail_factor(mode):
    """Trailing stop ATR multiplier — tighter for futures (leverage amplifies noise)."""
    if mode == "futures":
        return FUTURES_CONFIG.get("trailing_atr_factor", 0.7)
    return RISK_CONFIG.get("trailing_atr_factor", 1.0)

# Slippage threshold — warn when fill price is this far past the trigger
_SLIPPAGE_WARN_PCT = 0.01  # 1%


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _calc_pnl(pos, price, with_fees=True):
    """Calculate P&L % for a position at exit price, including fees + slippage."""
    entry   = pos["entry_price"]
    mode    = pos.get("mode", "futures")
    partial = pos.get("partial_closed", 0)

    if pos["type"] == "BUY":
        gross_pnl = (price - entry) / entry * 100
    else:
        gross_pnl = (entry - price) / entry * 100

    if with_fees:
        ec = EXECUTION_CONFIG
        fee_pct = ec["futures_fee_pct"] if mode == "futures" else ec["spot_fee_pct"]
        slip = ec.get("slippage_pct", 0.05)
        # 2 sides per trade (open + close), single side for partial close (already paid entry)
        sides = 1 if partial else 2
        costs = (fee_pct + slip) * sides
        gross_pnl -= costs

    return gross_pnl

    if partial:
        partial_pnl = pos.get("partial_pnl") or 0
        return partial_pnl * 0.5 + exit_pnl * 0.5
    return exit_pnl


def _check_slippage(trigger_price, fill_price, pos_id):
    """Log a warning when the fill price is far past the trigger (trailing stop / TP)."""
    if trigger_price and trigger_price > 0:
        slip = abs(fill_price - trigger_price) / trigger_price
        if slip > _SLIPPAGE_WARN_PCT:
            logger.warning(
                "Slippage %.2f%% on %s — trigger $%.2f, filled $%.2f (%.0f min gap)",
                slip * 100, pos_id, trigger_price, fill_price, slip * 100,
            )


# ---------------------------------------------------------------------------
# Position management
# ---------------------------------------------------------------------------

def check_and_close_positions(current_price, mode=None, current_atr=0, funding_rate=0):
    """Check open paper positions against *current_price*, optionally filtered by mode.

    If a HIGH impact USD macro event is within 2 hours, all open positions
    are force-closed at *current_price* regardless of mode — macro risk
    trumps technical setups.

    Returns a list of close-event dicts: {type, entry, exit, pnl, outcome, mode}
    so the caller can send notifications.
    """
    from signals.sentiment import check_upcoming_macro_events
    closed = []

    # ── Macro gate — force-close ALL positions ──────────────────────
    has_macro, event_name = check_upcoming_macro_events()
    if has_macro:
        all_open = sh.get_open_positions(None)  # all modes
        if all_open:
            print(
                f"\n⚠️  MACRO RISK: {event_name} in <2h"
                f" — force closing {len(all_open)} position(s) at market"
            )
            logger.warning(
                "⚠️  MACRO: %s in <2h — force closing %d position(s) at market",
                event_name, len(all_open),
            )
            for pos in all_open:
                exit_pnl = _calc_pnl(pos, current_price)
                sh.close_paper_position(pos["id"], "MACRO_CLOSE", exit_pnl)
                closed.append({
                    "type": pos["type"], "entry": pos["entry_price"],
                    "exit": current_price, "pnl": exit_pnl,
                    "outcome": "MACRO_CLOSE", "mode": pos.get("mode", "futures"),
                })
                logger.info(
                    "Paper %s %s → MACRO_CLOSE at $%.2f (%.2f%%)",
                    pos["type"], pos["id"], current_price, exit_pnl,
                )
        return closed

    # ── Normal position management ──────────────────────────────────
    open_positions = sh.get_open_positions(mode)
    if not open_positions:
        return

    for pos in open_positions:
        pos_id  = pos["id"]
        entry   = pos["entry_price"]
        atr     = pos.get("atr") or 0
        trail   = pos.get("trailing_stop") or pos["stop_loss"]
        tp1     = pos.get("tp1") or pos["take_profit"]
        tp2     = pos.get("tp2")
        partial = pos.get("partial_closed", 0)

        # ── Exit 0a: time-based stale position ──
        max_hours = RISK_CONFIG.get("max_position_hours", 72)
        opened_str = pos.get("opened_at", "")
        if opened_str:
            try:
                opened_dt = datetime.fromisoformat(opened_str)
                age_hours = (datetime.now(UTC) - opened_dt).total_seconds() / 3600
                if age_hours > max_hours:
                    exit_pnl = _calc_pnl(pos, current_price)
                    sh.close_paper_position(pos_id, "TIME_EXIT", exit_pnl, closed_at=current_price)
                    closed.append({
                        "type": pos["type"], "entry": entry,
                        "exit": current_price, "pnl": exit_pnl,
                        "outcome": "TIME_EXIT", "mode": pos.get("mode", "futures"),
                    })
                    logger.info(
                        "Paper %s %s → TIME_EXIT after %.0fh (%.2f%%)",
                        pos["type"], pos_id, age_hours, exit_pnl,
                    )
                    continue  # skip remaining checks for this position
            except (ValueError, TypeError):
                pass

        # ── Exit 0b: volatility expansion ──
        entry_atr = pos.get("atr") or 0
        vol_mult = RISK_CONFIG.get("vol_expansion_exit_mult", 2.0)
        if entry_atr > 0 and current_atr > 0 and current_atr > entry_atr * vol_mult:
            exit_pnl = _calc_pnl(pos, current_price)
            sh.close_paper_position(pos_id, "VOL_EXIT", exit_pnl, closed_at=current_price)
            closed.append({
                "type": pos["type"], "entry": entry,
                "exit": current_price, "pnl": exit_pnl,
                "outcome": "VOL_EXIT", "mode": pos.get("mode", "futures"),
            })
            logger.info(
                "Paper %s %s → VOL_EXIT (ATR %.0f > %.0f ×%.1f, %.2f%%)",
                pos["type"], pos_id, current_atr, entry_atr, vol_mult, exit_pnl,
            )
            continue

        # ── Exit 0c: funding-rate exit (futures only) ──
        if mode == "futures" and funding_rate != 0:
            from config import FUTURES_CONFIG
            fe_cfg = FUTURES_CONFIG.get("funding_exit", {})
            close_long_rate = fe_cfg.get("close_long_rate", 0.10)
            close_short_rate = fe_cfg.get("close_short_rate", -0.05)
            if pos["type"] == "BUY" and funding_rate > close_long_rate:
                exit_pnl = _calc_pnl(pos, current_price)
                sh.close_paper_position(pos_id, "FUNDING_EXIT", exit_pnl, closed_at=current_price)
                closed.append({
                    "type": pos["type"], "entry": entry,
                    "exit": current_price, "pnl": exit_pnl,
                    "outcome": "FUNDING_EXIT", "mode": pos.get("mode", "futures"),
                })
                logger.info(
                    "Paper %s %s → FUNDING_EXIT (%.4f%% > %.2f%%, %.2f%%)",
                    pos["type"], pos_id, funding_rate, close_long_rate, exit_pnl,
                )
                continue
            elif pos["type"] == "SELL" and funding_rate < close_short_rate:
                exit_pnl = _calc_pnl(pos, current_price)
                sh.close_paper_position(pos_id, "FUNDING_EXIT", exit_pnl, closed_at=current_price)
                closed.append({
                    "type": pos["type"], "entry": entry,
                    "exit": current_price, "pnl": exit_pnl,
                    "outcome": "FUNDING_EXIT", "mode": pos.get("mode", "futures"),
                })
                logger.info(
                    "Paper %s %s → FUNDING_EXIT (%.4f%% < %.2f%%, %.2f%%)",
                    pos["type"], pos_id, funding_rate, close_short_rate, exit_pnl,
                )
                continue

        if pos["type"] == "BUY":
            # 1 — advance trailing stop
            if atr:
                new_trail = current_price - atr * _trail_factor(pos.get("mode", "futures"))
                if new_trail > trail:
                    trail = new_trail
                    sh.update_trailing_stop(pos_id, trail)

            # 2 — partial exit at TP1 (first half)
            if not partial and current_price >= tp1:
                pnl = (tp1 - entry) / entry * 100
                sh.partial_close_position(pos_id, pnl, new_sl=entry)
                trail = entry  # breakeven
                closed.append({
                    "type": pos["type"], "entry": entry,
                    "exit": tp1, "pnl": pnl,
                    "outcome": "TP1", "mode": pos.get("mode", "futures"),
                })
                logger.info(
                    "Paper BUY %s → PARTIAL WIN at TP1 $%.2f (+%.2f%%) — trailing to breakeven",
                    pos_id, tp1, pnl,
                )
                partial = 1

            # 3 — full exit: TP2 hit (after partial) or trailing stop hit
            # P&L is blended: each half contributes 50% of total return
            if partial and tp2 and current_price >= tp2:
                partial_pnl = pos.get("partial_pnl") or 0
                remaining_pnl = (tp2 - entry) / entry * 100
                combined_pnl = partial_pnl * 0.5 + remaining_pnl * 0.5
                sh.close_paper_position(pos_id, "WIN", combined_pnl)
                closed.append({
                    "type": pos["type"], "entry": entry,
                    "exit": tp2, "pnl": combined_pnl,
                    "outcome": "TP2", "mode": pos.get("mode", "futures"),
                })
                logger.info(
                    "Paper BUY %s → WIN at TP2 $%.2f (combined +%.2f%%)",
                    pos_id, tp2, combined_pnl,
                )
            elif current_price <= trail:
                _check_slippage(trail, current_price, pos_id)
                exit_pnl = (trail - entry) / entry * 100
                if partial:
                    partial_pnl = pos.get("partial_pnl") or 0
                    exit_pnl = partial_pnl * 0.5 + exit_pnl * 0.5
                outcome = "WIN" if trail >= entry else "LOSS"
                sh.close_paper_position(pos_id, outcome, exit_pnl)
                closed.append({
                    "type": pos["type"], "entry": entry,
                    "exit": current_price, "pnl": exit_pnl,
                    "outcome": "SL" if outcome == "LOSS" else "Trail",
                    "mode": pos.get("mode", "futures"),
                })
                logger.info(
                    "Paper BUY %s → %s at trailing $%.2f (filled $%.2f, %.2f%%)",
                    pos_id, outcome, trail, current_price, exit_pnl,
                )

        elif pos["type"] == "SELL":
            # 1 — advance trailing stop (moves down for shorts)
            if atr:
                new_trail = current_price + atr * _trail_factor(pos.get("mode", "futures"))
                if new_trail < trail:
                    trail = new_trail
                    sh.update_trailing_stop(pos_id, trail)

            # 2 — partial exit at TP1
            if not partial and current_price <= tp1:
                pnl = (entry - tp1) / entry * 100
                sh.partial_close_position(pos_id, pnl, new_sl=entry)
                trail = entry
                closed.append({
                    "type": pos["type"], "entry": entry,
                    "exit": tp1, "pnl": pnl,
                    "outcome": "TP1", "mode": pos.get("mode", "futures"),
                })
                logger.info(
                    "Paper SELL %s → PARTIAL WIN at TP1 $%.2f (+%.2f%%) — trailing to breakeven",
                    pos_id, tp1, pnl,
                )
                partial = 1

            # 3 — full exit: TP2 hit or trailing stop hit
            if partial and tp2 and current_price <= tp2:
                partial_pnl = pos.get("partial_pnl") or 0
                remaining_pnl = (entry - tp2) / entry * 100
                combined_pnl = partial_pnl * 0.5 + remaining_pnl * 0.5
                sh.close_paper_position(pos_id, "WIN", combined_pnl)
                closed.append({
                    "type": pos["type"], "entry": entry,
                    "exit": tp2, "pnl": combined_pnl,
                    "outcome": "TP2", "mode": pos.get("mode", "futures"),
                })
                logger.info(
                    "Paper SELL %s → WIN at TP2 $%.2f (combined +%.2f%%)",
                    pos_id, tp2, combined_pnl,
                )
            elif current_price >= trail:
                _check_slippage(trail, current_price, pos_id)
                exit_pnl = (entry - trail) / entry * 100
                if partial:
                    partial_pnl = pos.get("partial_pnl") or 0
                    exit_pnl = partial_pnl * 0.5 + exit_pnl * 0.5
                outcome = "WIN" if trail <= entry else "LOSS"
                sh.close_paper_position(pos_id, outcome, exit_pnl)
                closed.append({
                    "type": pos["type"], "entry": entry,
                    "exit": current_price, "pnl": exit_pnl,
                    "outcome": "SL" if outcome == "LOSS" else "Trail",
                    "mode": pos.get("mode", "futures"),
                })
                logger.info(
                    "Paper SELL %s → %s at trailing $%.2f (filled $%.2f, %.2f%%)",
                    pos_id, outcome, trail, current_price, exit_pnl,
                )

    return closed


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_open_status(mode=None):
    """Print a summary of currently open paper positions."""
    positions = sh.get_open_positions(mode)
    if not positions:
        return

    mode_str = f" ({mode.upper()})" if mode else ""
    print(f"\n  {'─' * 40}")
    print(f"  OPEN POSITIONS{mode_str}")
    print(f"  {'─' * 40}")
    for pos in positions:
        icon    = "🟢" if pos["type"] == "BUY" else "🔴"
        pid     = pos["id"]
        entry   = pos["entry_price"]
        sl      = pos["stop_loss"]
        trail   = pos.get("trailing_stop") or pos["stop_loss"]
        tp1     = pos.get("tp1") or pos["take_profit"]
        tp2     = pos.get("tp2")
        partial = pos.get("partial_closed", 0)
        tag     = "  [½ taken]" if partial else ""
        pyramid = pos.get("pyramid_entry")
        if pyramid:
            sf = pos.get("size_factor", 1.0)
            tag += f" [pyramid #{pyramid} ×{sf:.0%}]"
        opened  = pos.get("opened_at", "")[:16]

        print(f"  {icon} {pos['type']}{tag}  #{pid}  @ ${entry:,.0f}")
        parts = [f"SL ${sl:,.0f}", f"TP1 ${tp1:,.0f}"]
        if tp2:
            parts.append(f"TP2 ${tp2:,.0f}")
        parts.append(f"Trail ${trail:,.0f}")
        parts.append(f"Opened {opened}")
        print(f"     " + "  ·  ".join(parts))

    total, count, avg = sh.get_closed_pnl(mode)
    if count > 0:
        bd = sh.get_outcome_breakdown(mode)
        w = bd.get("WIN", 0)
        l = bd.get("LOSS", 0)
        m = bd.get("MACRO_CLOSE", 0)
        be = bd.get("BREAKEVEN", 0)
        parts = []
        if w: parts.append(f"{w}W")
        if l: parts.append(f"{l}L")
        if m: parts.append(f"{m}MC")
        if be: parts.append(f"{be}BE")
        outcome_str = "  ".join(parts) if parts else ""
        wr_str = f"  WR {w/(w+l)*100:.0f}%" if (w + l) > 0 else ""
        pnl_col = "+" if total >= 0 else ""
        print(f"  Closed: {count} trades  {outcome_str}{wr_str}  |  P&L {pnl_col}{total:.2f}%  Avg {avg:+.2f}%")


def print_paper_summary(mode=None):
    """Print paper trading performance summary with outcome breakdown."""
    total, count, avg = sh.get_closed_pnl(mode)
    if count == 0:
        return

    mode_str = f" [ {mode.upper()} ]" if mode else ""
    print(f"\n📊 PAPER TRADING PERFORMANCE{mode_str}:")
    print(f"   Total Trades:     {count}")
    print(f"   Net P&L:          {total:+.2f}%")
    print(f"   Avg per Trade:    {avg:+.2f}%")

    # Outcome breakdown
    bd = sh.get_outcome_breakdown(mode)
    if bd:
        w  = bd.get("WIN", 0)
        l  = bd.get("LOSS", 0)
        mc = bd.get("MACRO_CLOSE", 0)
        be = bd.get("BREAKEVEN", 0)
        parts = [f"{w} Wins", f"{l} Losses"]
        if mc:
            parts.append(f"{mc} Macro")
        if be:
            parts.append(f"{be} BE")
        print(f"   Outcomes:         {' · '.join(parts)}")

    wr = sh.get_win_rate(mode)
    if wr is not None:
        print(f"   Win Rate:         {wr:.1%}")

    pf = sh.get_profit_factor(mode)
    if pf is not None:
        pf_str = "∞" if pf == float("inf") else f"{pf:.2f}"
        print(f"   Profit Factor:    {pf_str}")

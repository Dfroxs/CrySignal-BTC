"""Paper trading — trailing stop + partial take-profit position management.

Each cycle:
  1. Force-close all positions if HIGH impact macro event is <2h away.
  2. Advance trailing stop if price moved in our favour.
  3. First half exits at TP1 → trailing stop moves to breakeven.
  4. Second half exits at TP2 or when trailing stop is hit.
"""

import logging

import trading.history as sh
from config import RISK_CONFIG

logger = logging.getLogger(__name__)

_TRAIL = RISK_CONFIG["trailing_atr_factor"]

# Slippage threshold — warn when fill price is this far past the trigger
_SLIPPAGE_WARN_PCT = 0.01  # 1%


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _calc_pnl(pos, price):
    """Calculate blended P&L percentage for a position at the given exit price."""
    entry    = pos["entry_price"]
    partial  = pos.get("partial_closed", 0)

    if pos["type"] == "BUY":
        exit_pnl = (price - entry) / entry * 100
    else:
        exit_pnl = (entry - price) / entry * 100

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

def check_and_close_positions(current_price, mode=None):
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

        if pos["type"] == "BUY":
            # 1 — advance trailing stop
            if atr:
                new_trail = current_price - atr * _TRAIL
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
                new_trail = current_price + atr * _TRAIL
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

    mode_str = f" [{mode.upper()}]" if mode else ""
    print(f"\n📝 PAPER POSITIONS (open{mode_str}):")
    for pos in positions:
        icon    = "\U0001f7e2" if pos["type"] == "BUY" else "\U0001f534"
        entry   = pos["entry_price"]
        trail   = pos.get("trailing_stop") or pos["stop_loss"]
        tp1     = pos.get("tp1") or pos["take_profit"]
        tp2     = pos.get("tp2")
        partial = pos.get("partial_closed", 0)
        tag     = " [½ taken]" if partial else ""
        opened  = pos.get("opened_at", "")[:16]

        tp2_str = f" | TP2: ${tp2:,.2f}" if tp2 else ""
        print(
            f"   {icon} {pos['type']}{tag} | Entry: ${entry:,.2f} "
            f"| Trail: ${trail:,.2f} | TP1: ${tp1:,.2f}{tp2_str} "
            f"| Opened: {opened}"
        )

    total, count, avg = sh.get_closed_pnl(mode)
    if count > 0:
        bd = sh.get_outcome_breakdown()
        # Filter to this mode (outcome_breakdown is global — approximate)
        w = bd.get("WIN", 0)
        l = bd.get("LOSS", 0)
        m = bd.get("MACRO_CLOSE", 0)
        be = bd.get("BREAKEVEN", 0)
        parts = []
        if w: parts.append(f"{w}W")
        if l: parts.append(f"{l}L")
        if m: parts.append(f"{m}MC")
        if be: parts.append(f"{be}BE")
        outcome_str = " · ".join(parts) if parts else ""
        wr_str = f"  WR: {w/(w+l)*100:.0f}%" if (w + l) > 0 else ""
        print(
            f"   Closed: {count} trades ({outcome_str}{wr_str}) | "
            f"Net P&L: {total:+.2f}% | Avg: {avg:+.2f}%"
        )


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
    bd = sh.get_outcome_breakdown()
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

    wr = sh.get_win_rate()
    if wr is not None:
        print(f"   Win Rate:         {wr:.1%}")

    pf = sh.get_profit_factor()
    if pf is not None:
        pf_str = "∞" if pf == float("inf") else f"{pf:.2f}"
        print(f"   Profit Factor:    {pf_str}")

"""Paper trading — trailing stop + partial take-profit position management.

Each cycle:
  1. Advance trailing stop if price moved in our favour.
  2. First half exits at TP1 → trailing stop moves to breakeven.
  3. Second half exits at TP2 or when trailing stop is hit.
"""

import logging

import signal_history as sh
from config import RISK_CONFIG

logger = logging.getLogger(__name__)

_TRAIL = RISK_CONFIG["trailing_atr_factor"]


# ---------------------------------------------------------------------------
# Position management
# ---------------------------------------------------------------------------

def check_and_close_positions(current_price, mode=None):
    """Check open paper positions against *current_price*, optionally filtered by mode."""
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
                logger.info(
                    "Paper BUY %s → WIN at TP2 $%.2f (combined +%.2f%%)",
                    pos_id, tp2, combined_pnl,
                )
            elif current_price <= trail:
                exit_pnl = (trail - entry) / entry * 100
                if partial:
                    partial_pnl = pos.get("partial_pnl") or 0
                    exit_pnl = partial_pnl * 0.5 + exit_pnl * 0.5
                outcome = "WIN" if trail >= entry else "LOSS"
                sh.close_paper_position(pos_id, outcome, exit_pnl)
                logger.info(
                    "Paper BUY %s → %s at trailing $%.2f (%.2f%%)",
                    pos_id, outcome, trail, exit_pnl,
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
                logger.info(
                    "Paper SELL %s → WIN at TP2 $%.2f (combined +%.2f%%)",
                    pos_id, tp2, combined_pnl,
                )
            elif current_price >= trail:
                exit_pnl = (entry - trail) / entry * 100
                if partial:
                    partial_pnl = pos.get("partial_pnl") or 0
                    exit_pnl = partial_pnl * 0.5 + exit_pnl * 0.5
                outcome = "WIN" if trail <= entry else "LOSS"
                sh.close_paper_position(pos_id, outcome, exit_pnl)
                logger.info(
                    "Paper SELL %s → %s at trailing $%.2f (%.2f%%)",
                    pos_id, outcome, trail, exit_pnl,
                )


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_open_status(mode=None):
    """Print a summary of currently open paper positions."""
    positions = sh.get_open_positions(mode)
    if not positions:
        return

    print("\n📝 PAPER POSITIONS (open):")
    for pos in positions:
        icon    = "\U0001f7e2" if pos["type"] == "BUY" else "\U0001f534"
        trail   = pos.get("trailing_stop") or pos["stop_loss"]
        tp1     = pos.get("tp1") or pos["take_profit"]
        tp2     = pos.get("tp2")
        partial = pos.get("partial_closed", 0)
        tag     = " [½ taken]" if partial else ""

        tp2_str = f" | TP2: ${tp2:,.2f}" if tp2 else ""
        print(
            f"   {icon} {pos['type']}{tag} | Entry: ${pos['entry_price']:,.2f} "
            f"| Trail: ${trail:,.2f} | TP1: ${tp1:,.2f}{tp2_str} "
            f"| Opened: {pos['opened_at'][:16]}"
        )

    total, count, avg = sh.get_closed_pnl(mode)
    if count > 0:
        print(
            f"   Closed: {count} trades | "
            f"Net P&L: {total:+.2f}% | Avg: {avg:+.2f}%"
        )


def print_paper_summary(mode=None):
    """Print paper trading performance summary."""
    total, count, avg = sh.get_closed_pnl(mode)
    if count == 0:
        return

    print("\n📊 PAPER TRADING PERFORMANCE:")
    print(f"   Total Trades:     {count}")
    print(f"   Net P&L:          {total:+.2f}%")
    print(f"   Avg per Trade:    {avg:+.2f}%")

    wr = sh.get_win_rate()
    if wr is not None:
        print(f"   Win Rate:         {wr:.1%}")

    pf = sh.get_profit_factor()
    if pf is not None:
        pf_str = "∞" if pf == float("inf") else f"{pf:.2f}"
        print(f"   Profit Factor:    {pf_str}")

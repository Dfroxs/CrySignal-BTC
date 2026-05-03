"""Paper trading — auto-close open positions when TP or SL is hit.

Runs after each cycle, checks all open paper positions against
current price, and marks them WIN / LOSS / BREAKEVEN.
"""

import logging

from config import HTTP_SESSION

import signal_history as sh

logger = logging.getLogger(__name__)


def check_and_close_positions(current_price):
    """Check all open paper positions against *current_price*.

    Closes any position whose TP or SL has been crossed and updates
    the database outcome column.
    """
    open_positions = sh.get_open_positions()
    if not open_positions:
        return

    for pos in open_positions:
        pos_id = pos["id"]
        entry = pos["entry_price"]
        sl = pos["stop_loss"]
        tp = pos["take_profit"]

        if pos["type"] == "BUY":
            if current_price >= tp:
                pnl = (tp - entry) / entry * 100
                sh.close_paper_position(pos_id, "WIN", pnl)
                logger.info(
                    "Paper BUY %s → WIN ($%.2f → $%.2f, +%.2f%%)",
                    pos["id"], entry, tp, pnl,
                )
            elif current_price <= sl:
                pnl = (sl - entry) / entry * 100
                sh.close_paper_position(pos_id, "LOSS", pnl)
                logger.info(
                    "Paper BUY %s → LOSS ($%.2f → $%.2f, %.2f%%)",
                    pos["id"], entry, sl, pnl,
                )
        elif pos["type"] == "SELL":
            if current_price <= tp:
                pnl = (entry - tp) / entry * 100
                sh.close_paper_position(pos_id, "WIN", pnl)
                logger.info(
                    "Paper SELL %s → WIN ($%.2f → $%.2f, +%.2f%%)",
                    pos["id"], entry, tp, pnl,
                )
            elif current_price >= sl:
                pnl = (entry - sl) / entry * 100
                sh.close_paper_position(pos_id, "LOSS", pnl)
                logger.info(
                    "Paper SELL %s → LOSS ($%.2f → $%.2f, %.2f%%)",
                    pos["id"], entry, sl, pnl,
                )


def print_open_status():
    """Print a summary of currently open paper positions."""
    positions = sh.get_open_positions()
    if not positions:
        return

    print("\n📝 PAPER POSITIONS (open):")
    for pos in positions:
        icon = "\U0001f7e2" if pos["type"] == "BUY" else "\U0001f534"
        print(
            f"   {icon} {pos['type']} | Entry: ${pos['entry_price']:,.2f} "
            f"| SL: ${pos['stop_loss']:,.2f} | TP: ${pos['take_profit']:,.2f} "
            f"| Opened: {pos['opened_at'][:16]}"
        )

    total, count, avg = sh.get_closed_pnl()
    if count > 0:
        print(
            f"   Closed: {count} trades | "
            f"Net P&L: {total:+.2f}% | Avg: {avg:+.2f}%"
        )


def print_paper_summary():
    """Print paper trading performance summary."""
    total, count, avg = sh.get_closed_pnl()
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
        print(f"   Profit Factor:    {pf:.2f}")

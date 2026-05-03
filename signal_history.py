"""SQLite-backed signal history with query helpers.

Replaces the flat CSV in ``core_analysis.log_signal()`` while
preserving CSV export as fallback.
"""

import logging
import os
import sqlite3
from datetime import UTC, datetime
from typing import Optional

import pandas as pd

from config import SIGNAL_HISTORY_CSV, SIGNAL_HISTORY_DB

logger = logging.getLogger(__name__)

DB = None  # lazy-init connection


# ---------------------------------------------------------------------------
# Database lifecycle
# ---------------------------------------------------------------------------

def _conn() -> sqlite3.Connection:
    """Return (lazy-initialized) database connection."""
    global DB
    if DB is None:
        DB = sqlite3.connect(SIGNAL_HISTORY_DB)
        DB.row_factory = sqlite3.Row
        _init_tables()
    return DB


def _init_tables():
    """Create tables if they don't exist."""
    c = _conn()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS signals (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp     TEXT    NOT NULL,
            type          TEXT    NOT NULL,
            entry_price   REAL    NOT NULL,
            stop_loss     REAL,
            take_profit   REAL,
            strength      REAL,
            rsi           REAL,
            stoch_k       REAL,
            vwap          REAL,
            htf_4h        TEXT,
            htf_1d        TEXT,
            fear_greed    INTEGER,
            news_sentiment TEXT,
            outcome       TEXT    DEFAULT '',
            closed_at     TEXT
        );

        CREATE TABLE IF NOT EXISTS paper_positions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id     INTEGER REFERENCES signals(id),
            type          TEXT    NOT NULL,
            entry_price   REAL    NOT NULL,
            stop_loss     REAL    NOT NULL,
            take_profit   REAL    NOT NULL,
            opened_at     TEXT    NOT NULL,
            closed_at     TEXT,
            outcome       TEXT,
            pnl_pct       REAL
        );
    """)
    c.commit()


def migrate_from_csv():
    """One-time migration from legacy CSV to SQLite."""
    if not os.path.exists(SIGNAL_HISTORY_CSV):
        return
    try:
        df = pd.read_csv(SIGNAL_HISTORY_CSV)
        if df.empty:
            return
        c = _conn()
        existing = c.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        if existing > 0:
            return
        for _, row in df.iterrows():
            c.execute(
                """INSERT INTO signals
                   (timestamp, type, entry_price, stop_loss, take_profit,
                    strength, rsi, stoch_k, vwap, htf_4h, htf_1d,
                    fear_greed, news_sentiment, outcome)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(row.get("timestamp", "")),
                    str(row.get("type", "HOLD")),
                    float(row.get("entry_price", 0)),
                    _maybe_float(row.get("stop_loss")),
                    _maybe_float(row.get("take_profit")),
                    float(row.get("strength", 0)),
                    float(row.get("rsi", 0)),
                    float(row.get("stoch_k", 0)),
                    float(row.get("vwap", 0)),
                    str(row.get("htf_4h", "")),
                    str(row.get("htf_1d", "")),
                    _maybe_int(row.get("fear_greed")),
                    str(row.get("news_sentiment", "")),
                    str(row.get("outcome", "")),
                ),
            )
        c.commit()
        logger.info("Migrated %d rows from CSV → SQLite", len(df))
    except Exception as e:
        logger.warning("CSV migration failed: %s", e)


def close():
    """Close the database connection (call on shutdown)."""
    global DB
    if DB:
        DB.close()
        DB = None


# ---------------------------------------------------------------------------
# Signal CRUD
# ---------------------------------------------------------------------------

def log_signal(signal, df, htf=None):
    """Append one row to the SQLite signals table.

    Also writes CSV as fallback (preserves legacy behaviour).
    """
    last = df.iloc[-1]
    row = (
        datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
        signal["type"],
        signal["entry_price"],
        signal.get("stop_loss"),
        signal.get("take_profit"),
        round(signal["strength"], 2),
        round(last["RSI_14"], 2),
        round(last.get("StochRSI_K") or 0, 2),
        round(last.get("VWAP_24") or 0, 2),
        htf.get("4h", "") if htf else "",
        htf.get("1d", "") if htf else "",
        signal.get("fear_greed_value"),
        signal.get("news_sentiment", ""),
        "",
    )
    c = _conn()
    c.execute(
        """INSERT INTO signals
           (timestamp, type, entry_price, stop_loss, take_profit,
            strength, rsi, stoch_k, vwap, htf_4h, htf_1d,
            fear_greed, news_sentiment, outcome)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        row,
    )
    c.commit()

    # CSV fallback
    write_header = not os.path.exists(SIGNAL_HISTORY_CSV)
    csv_row = {
        "timestamp":      row[0],
        "type":           row[1],
        "entry_price":    row[2],
        "stop_loss":      row[3] or "",
        "take_profit":    row[4] or "",
        "strength":       row[5],
        "rsi":            row[6],
        "stoch_k":        row[7],
        "vwap":           row[8],
        "htf_4h":         row[9],
        "htf_1d":         row[10],
        "fear_greed":     row[11] or "",
        "news_sentiment": row[12] or "",
        "outcome":        "",
    }
    pd.DataFrame([csv_row]).to_csv(
        SIGNAL_HISTORY_CSV, mode="a", header=write_header, index=False,
    )
    logger.info("Signal logged → %s", SIGNAL_HISTORY_DB)


def update_signal_outcome(signal_id, outcome, closed_at=None):
    """Mark a signal row as WIN / LOSS / BREAKEVEN."""
    c = _conn()
    c.execute(
        "UPDATE signals SET outcome=?, closed_at=? WHERE id=?",
        (outcome, closed_at or datetime.now(UTC).isoformat(), signal_id),
    )
    c.commit()


# ---------------------------------------------------------------------------
# Paper position CRUD
# ---------------------------------------------------------------------------

def open_paper_position(signal):
    """Record a new open paper position from a BUY/SELL signal.

    Returns the position row id.
    """
    c = _conn()
    c.execute(
        """INSERT INTO paper_positions
           (type, entry_price, stop_loss, take_profit, opened_at)
           VALUES (?,?,?,?,?)""",
        (
            signal["type"],
            signal["entry_price"],
            signal["stop_loss"],
            signal["take_profit"],
            datetime.now(UTC).isoformat(),
        ),
    )
    c.commit()
    return c.lastrowid


def get_open_positions():
    """Return list of dicts for all open paper positions."""
    c = _conn()
    rows = c.execute(
        "SELECT * FROM paper_positions WHERE outcome IS NULL"
    ).fetchall()
    return [dict(r) for r in rows]


def close_paper_position(pos_id, outcome, pnl_pct, closed_at=None):
    """Mark a paper position as closed."""
    c = _conn()
    c.execute(
        """UPDATE paper_positions
           SET outcome=?, pnl_pct=?, closed_at=?
           WHERE id=?""",
        (
            outcome, round(pnl_pct, 3),
            closed_at or datetime.now(UTC).isoformat(), pos_id,
        ),
    )
    c.commit()


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def get_recent_signals(limit=50):
    """Return the most recent *limit* signal rows as dicts."""
    rows = _conn().execute(
        "SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def get_win_rate():
    """Win rate (WINS / all resolved signals) as a float, or None."""
    c = _conn()
    total = c.execute(
        "SELECT COUNT(*) FROM signals WHERE outcome IN ('WIN','LOSS','BREAKEVEN')"
    ).fetchone()[0]
    if total == 0:
        return None
    wins = c.execute(
        "SELECT COUNT(*) FROM signals WHERE outcome='WIN'"
    ).fetchone()[0]
    return wins / total


def get_profit_factor():
    """Profit factor (total TP wins / total SL losses).  None if no data."""
    c = _conn()
    wins = c.execute(
        """SELECT COALESCE(SUM(ABS(take_profit - entry_price)), 0)
           FROM signals WHERE outcome='WIN'"""
    ).fetchone()[0]
    losses = c.execute(
        """SELECT COALESCE(SUM(ABS(entry_price - stop_loss)), 0)
           FROM signals WHERE outcome='LOSS'"""
    ).fetchone()[0]
    if losses == 0:
        return None
    return wins / losses


def get_closed_pnl():
    """Return (total_pnl_pct, total_trades, avg_pnl_pct) for paper positions."""
    c = _conn()
    rows = c.execute(
        "SELECT pnl_pct FROM paper_positions WHERE pnl_pct IS NOT NULL"
    ).fetchall()
    if not rows:
        return 0, 0, 0
    pnls = [r["pnl_pct"] for r in rows]
    return sum(pnls), len(pnls), sum(pnls) / len(pnls)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _maybe_float(val) -> Optional[float]:
    if val is None or val == "" or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _maybe_int(val) -> Optional[int]:
    if val is None or val == "" or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


# Auto-migrate on import
migrate_from_csv()

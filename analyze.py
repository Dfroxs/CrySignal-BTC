"""
analyze.py — SpotSignal data analysis + improvement summary.

Usage:
    python3 analyze.py               # full report
    python3 analyze.py --mode spot   # spot only
    python3 analyze.py --mode futures
    python3 analyze.py --json        # machine-readable output
    python3 analyze.py --db PATH     # analyse a database from elsewhere

The paper run lives on a server; analysis runs on a workstation. Pull the file
and point --db at it rather than overwriting the local database, which is the
only copy of whatever it holds:

    scp <host>:~/playground/CrySignal-BTC/data/signal_history.db data/server.db
    python3 analyze.py --db data/server.db
"""

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

DB_PATH  = "data/signal_history.db"
LOG_PATH = "spotsignal.log"

# ── ANSI colours ────────────────────────────────────────────────────────────
G   = "\033[32m"
R   = "\033[31m"
Y   = "\033[33m"
B   = "\033[34m"
DIM = "\033[2m"
BLD = "\033[1m"
RST = "\033[0m"

def _c(text, col): return f"{col}{text}{RST}"
def _h(title):     print(f"\n{BLD}{'─'*60}{RST}\n{BLD}  {title}{RST}\n{'─'*60}")
def _ok(msg):      print(f"  {G}✓{RST}  {msg}")
def _warn(msg):    print(f"  {Y}⚠{RST}  {msg}")
def _bad(msg):     print(f"  {R}✗{RST}  {msg}")
def _info(msg):    print(f"  {DIM}→{RST}  {msg}")


# ── DB helpers ───────────────────────────────────────────────────────────────

def _conn():
    if not Path(DB_PATH).exists():
        print(f"{R}DB not found: {DB_PATH}{RST}")
        sys.exit(1)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _describe_source(conn):
    """Say which database this report came from, and what span it covers.

    With --db in play a report is no longer self-evidently about the local file;
    two runs on two hosts produce reports that are indistinguishable without it.
    """
    try:
        row = conn.execute(
            "SELECT COUNT(*) n, MIN(timestamp) lo, MAX(timestamp) hi FROM cycle_log"
        ).fetchone()
    except sqlite3.Error:
        return
    size = Path(DB_PATH).stat().st_size / 1024
    print(f"{DIM}  source : {DB_PATH}  ({size:,.0f} KB){RST}")
    if row and row["n"]:
        print(f"{DIM}  cycles : {row['n']}  spanning {row['lo']} → {row['hi']}{RST}")
    else:
        print(f"{DIM}  cycles : 0 — nothing logged yet{RST}")


def _rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ── Section 1: Overview ──────────────────────────────────────────────────────

def section_overview(conn, mode_filter):
    _h("OVERVIEW")
    q = "SELECT mode, type, COUNT(*) as n FROM cycle_log"
    if mode_filter:
        q += f" WHERE mode='{mode_filter}'"
    q += " GROUP BY mode, type ORDER BY mode, type"
    rows = _rows(conn, q)

    totals = defaultdict(int)
    for r in rows:
        totals[r['mode']] += r['n']

    for r in rows:
        pct = r['n'] / totals[r['mode']] * 100
        col = G if r['type'] == 'BUY' else (R if r['type'] == 'SELL' else DIM)
        t = f"{r['type']:<5s}"
        print(f"  {r['mode']:8s}  {_c(t, col)}  {r['n']:4d} cycles  ({pct:.0f}%)")

    # Date range
    first = conn.execute("SELECT MIN(timestamp) FROM cycle_log").fetchone()[0]
    last  = conn.execute("SELECT MAX(timestamp) FROM cycle_log").fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM cycle_log").fetchone()[0]
    print(f"\n  Period : {first}  →  {last}")
    print(f"  Total  : {total} cycles in DB")


# ── Section 2: Signal Quality ────────────────────────────────────────────────

def section_signal_quality(conn, mode_filter):
    _h("SIGNAL QUALITY")

    where = f"WHERE mode='{mode_filter}'" if mode_filter else ""

    for mode in (["spot", "futures"] if not mode_filter else [mode_filter]):
        rows = _rows(conn,
            f"SELECT type, strength, threshold, buy_score, sell_score, rsi, fear_greed "
            f"FROM cycle_log WHERE mode=? ORDER BY id", (mode,))
        if not rows: continue

        signals = [r for r in rows if r['type'] != 'HOLD']
        holds   = [r for r in rows if r['type'] == 'HOLD']

        print(f"\n  {BLD}{mode.upper()}{RST}  ({len(rows)} total cycles)")

        if signals:
            avg_str = sum(r['strength'] for r in signals) / len(signals)
            avg_thr = sum(r['threshold'] for r in signals) / len(signals)
            avg_gap = sum(r['strength'] - r['threshold'] for r in signals) / len(signals)
            min_str = min(r['strength'] for r in signals)
            max_str = max(r['strength'] for r in signals)
            print(f"  Signals  : {len(signals)}  (HOLD: {len(holds)}  →  hit rate {len(signals)/len(rows)*100:.0f}%)")
            print(f"  Score    : avg {avg_str:.2f}  min {min_str:.2f}  max {max_str:.2f}")
            print(f"  Threshold: avg {avg_thr:.2f}  margin avg +{avg_gap:.2f}")
        else:
            print(f"  {DIM}No signals fired{RST}")

        # Weak signals (score barely above threshold)
        weak = [r for r in signals if 0 < r['strength'] - r['threshold'] < 0.5]
        if weak:
            _warn(f"{len(weak)} signals fired with margin < 0.5 (borderline quality)")

        # Condition frequency from reasons
        reason_counts = Counter()
        for r in rows:
            if r.get('type') != 'HOLD':
                for part in (r.get('reasons') or '').split(' | '):
                    part = part.strip()
                    if part:
                        # Shorten to key phrase
                        key = re.sub(r'\(.*?\)', '', part).strip()[:50]
                        reason_counts[key] += 1

        if reason_counts:
            print(f"\n  Top triggered conditions ({mode}):")
            for cond, cnt in reason_counts.most_common(8):
                bar = '█' * min(cnt, 20)
                print(f"    {cnt:3d}  {DIM}{bar}{RST}  {cond}")


# ── Section 3: Paper Trading Performance ─────────────────────────────────────

def section_performance(conn, mode_filter):
    _h("PAPER TRADING PERFORMANCE")

    where = "WHERE outcome IS NOT NULL"
    if mode_filter:
        where += f" AND mode='{mode_filter}'"
    positions = _rows(conn, f"SELECT * FROM paper_positions {where} ORDER BY id")

    if not positions:
        _info("No closed positions yet")
        return

    # Per-mode breakdown
    by_mode = defaultdict(list)
    for p in positions:
        by_mode[p['mode']].append(p)

    for mode, trades in sorted(by_mode.items()):
        wins   = [t for t in trades if t['outcome'] == 'WIN']
        losses = [t for t in trades if t['outcome'] == 'LOSS']
        others = [t for t in trades if t['outcome'] not in ('WIN', 'LOSS')]
        total_pnl = sum(t['pnl_pct'] for t in trades)
        avg_pnl   = total_pnl / len(trades)
        wr = len(wins) / (len(wins) + len(losses)) * 100 if (wins or losses) else 0

        print(f"\n  {BLD}{mode.upper()}{RST}  ({len(trades)} trades)")
        col = G if total_pnl >= 0 else R
        print(f"  Net P&L   : {_c(f'{total_pnl:+.2f}%', col)}")
        print(f"  Avg / trade: {avg_pnl:+.2f}%")
        print(f"  Win Rate  : {wr:.0f}%  ({len(wins)}W / {len(losses)}L / {len(others)} other)")

        # Outcome breakdown
        outcome_ctr = Counter(t['outcome'] for t in trades)
        parts = []
        for oc, n in sorted(outcome_ctr.items()):
            col = G if oc == 'WIN' else (R if oc == 'LOSS' else DIM)
            parts.append(_c(f"{oc}×{n}", col))
        print(f"  Outcomes  : {' · '.join(parts)}")

        # Profit factor
        gross_win  = sum(t['pnl_pct'] for t in wins)
        gross_loss = abs(sum(t['pnl_pct'] for t in losses))
        if gross_loss > 0:
            pf = gross_win / gross_loss
            col = G if pf >= 1.5 else (Y if pf >= 1.0 else R)
            print(f"  Prof Factor: {_c(f'{pf:.2f}', col)}")

        # Avg win vs avg loss
        if wins:
            avg_win = sum(t['pnl_pct'] for t in wins) / len(wins)
            print(f"  Avg Win   : {_c(f'+{avg_win:.2f}%', G)}")
        if losses:
            avg_loss = sum(t['pnl_pct'] for t in losses) / len(losses)
            print(f"  Avg Loss  : {_c(f'{avg_loss:.2f}%', R)}")

        # Exit type breakdown
        print(f"\n  Exit breakdown:")
        for oc, n in sorted(outcome_ctr.items()):
            pnl_list = [t['pnl_pct'] for t in trades if t['outcome'] == oc]
            avg = sum(pnl_list) / len(pnl_list)
            col = G if avg >= 0 else R
            print(f"    {oc:15s} ×{n}  avg {_c(f'{avg:+.2f}%', col)}")

        # Hold time
        durations = []
        for t in trades:
            try:
                opened = datetime.fromisoformat(t['opened_at'])
                # closed_at may be a price string for some exits
                closed_raw = t.get('closed_at', '')
                try:
                    closed = datetime.fromisoformat(closed_raw)
                    durations.append((closed - opened).total_seconds() / 3600)
                except (ValueError, TypeError):
                    pass
            except (ValueError, TypeError):
                pass
        if durations:
            avg_h = sum(durations) / len(durations)
            print(f"\n  Avg hold  : {avg_h:.1f}h")

        # TP1 hit rate
        partial = [t for t in trades if t.get('partial_closed') == 1 or t.get('partial_pnl')]
        print(f"  TP1 hit   : {len(partial)}/{len(trades)} ({len(partial)/len(trades)*100:.0f}%)")


# ── Section 4: Condition Analysis ────────────────────────────────────────────

def section_conditions(conn, mode_filter):
    _h("CONDITION EFFECTIVENESS (correlate with position outcomes)")

    # Get signals that led to trades
    trades = _rows(conn, "SELECT * FROM paper_positions WHERE outcome IS NOT NULL")
    if not trades:
        _info("Need closed trades to analyze condition effectiveness")
        return

    for trade in trades:
        sig_id = trade.get('signal_id')
        if not sig_id: continue

        # Find matching cycle_log
        cycle = _rows(conn,
            "SELECT * FROM cycle_log WHERE id <= ? AND mode=? ORDER BY id DESC LIMIT 1",
            (sig_id, trade['mode']))
        if not cycle: continue

        c = cycle[0]
        reasons = (c.get('reasons') or '').split(' | ')
        outcome = trade['outcome']
        pnl     = trade['pnl_pct']
        col     = G if pnl >= 0 else R

        print(f"\n  Trade #{trade['id']} {trade['mode'].upper()} {trade['type']} "
              f"@ ${trade['entry_price']:,.0f}  →  "
              f"{_c(outcome, G if outcome=='WIN' else R)}  "
              f"{_c(f'{pnl:+.2f}%', col)}")
        print(f"  Score: {c['strength']:.2f}  RSI: {c['rsi']:.1f}  "
              f"HTF aligned: {json.loads(c['htf_data'] or '{}').get('aligned', '?')}")
        print(f"  Conditions ({len(reasons)}):")
        for r in reasons:
            r = r.strip()
            if r:
                icon = "✓" if r.startswith("Price above") or "RISING" in r or "oversold" in r.lower() or "accumulation" in r.lower() else "·"
                print(f"    {DIM}{icon}{RST} {r[:70]}")


# ── Section 5: Skip Gate Analysis ────────────────────────────────────────────

def section_skip_gates(conn, mode_filter):
    _h("ENTRY GATE ANALYSIS")

    where, params = "", []
    if mode_filter:
        where, params = " WHERE mode = ?", [mode_filter]

    rows = _rows(conn, f"SELECT mode, gate, signal_type, reason FROM signal_blocks{where}", params)
    if not rows:
        _info("signal_blocks is empty — run_bot.py writes one row per gate rejection")
        return

    # Infrastructure skips are not trading judgements and must not dilute the
    # gates that are. `stale_cache` fires on up to 3 of every 4 cycles with
    # --loop 60 (the replayed 4H spot analysis), so leaving it in tops the
    # histogram, pushes every real gate under the display thresholds, and
    # deflates entry conversion with a counter unrelated to entries.
    INFRASTRUCTURE = {"stale_cache", "positions_cleared", "pyramid_atr_invalid"}
    infra_rows = [r for r in rows if r["gate"] in INFRASTRUCTURE]
    rows = [r for r in rows if r["gate"] not in INFRASTRUCTURE]
    if not rows:
        _info(f"Only infrastructure skips recorded ({len(infra_rows)}) — no trading gate fired")
        return

    counts = Counter(r["gate"] for r in rows)
    total  = sum(counts.values())

    by_mode = Counter(r["mode"] for r in rows)
    split   = "  ".join(f"{m} {n}" for m, n in sorted(by_mode.items()))
    print(f"  Gate blocks : {total}   {DIM}({split}){RST}")

    # One representative reason per gate — the counters alone lose the detail
    # that makes a gate actionable ("distance 0.12% (< 0.5× ATR)").
    sample = {}
    for r in rows:
        sample.setdefault(r["gate"], r["reason"] or "")

    for gate, n in counts.most_common():
        pct = n / total * 100
        bar = '█' * min(int(pct / 3), 30)
        col = Y if pct > 30 else DIM
        print(f"  {n:3d}  {_c(bar, col)}  {gate}  ({pct:.0f}%)")
        if pct > 10:
            print(f"       {DIM}{sample[gate][:70]}{RST}")

    if infra_rows:
        infra = Counter(r["gate"] for r in infra_rows)
        detail = ", ".join(f"{g} {n}" for g, n in infra.most_common())
        print(f"\n   {DIM}Infrastructure skips, excluded above: {detail}{RST}")

    opened = _rows(conn, f"SELECT COUNT(*) AS n FROM paper_positions{where}", params)[0]["n"]
    print(f"\n  Positions opened : {opened}")
    if total + opened > 0:
        hit_rate = opened / (total + opened) * 100
        col = G if hit_rate > 30 else (Y if hit_rate > 10 else R)
        print(f"  Entry conversion : {_c(f'{hit_rate:.0f}%', col)}")


# ── Section 6: Market Context ─────────────────────────────────────────────────

def section_market_context(conn, mode_filter):
    _h("MARKET CONTEXT AT SIGNAL TIME")

    mode = mode_filter or 'futures'
    rows = _rows(conn,
        f"SELECT rsi, stoch_k, fear_greed, funding_rate, ls_ratio, "
        f"dxy_change, sp500_change, btc_dom, htf_data, type "
        f"FROM cycle_log WHERE mode=? AND type != 'HOLD'", (mode,))

    if not rows:
        _info(f"No signal data for {mode}")
        return

    def avg(key): return sum(r[key] for r in rows if r[key] is not None) / len(rows)
    def pct_above(key, val): return sum(1 for r in rows if r[key] and r[key] > val) / len(rows) * 100
    def pct_below(key, val): return sum(1 for r in rows if r[key] and r[key] < val) / len(rows) * 100

    print(f"\n  {BLD}Context when {mode.upper()} signals fire:{RST}  ({len(rows)} signals)")
    print(f"  RSI avg         : {avg('rsi'):.1f}  (>70: {pct_above('rsi',70):.0f}%  <30: {pct_below('rsi',30):.0f}%)")
    print(f"  StochRSI K avg  : {avg('stoch_k'):.1f}")
    print(f"  Fear&Greed avg  : {avg('fear_greed'):.0f}  (>60: {pct_above('fear_greed',60):.0f}%  <40: {100-pct_above('fear_greed',40):.0f}%)")
    if mode == 'futures':
        print(f"  Funding rate avg: {avg('funding_rate'):.4f}%")
        print(f"  L/S ratio avg   : {avg('ls_ratio'):.3f}")

    # HTF alignment at signal time
    aligned = sum(1 for r in rows if json.loads(r['htf_data'] or '{}').get('aligned', False))
    print(f"  HTF aligned     : {aligned}/{len(rows)}  ({aligned/len(rows)*100:.0f}%)")

    # BUY vs SELL breakdown
    buy_rows  = [r for r in rows if r['type'] == 'BUY']
    sell_rows = [r for r in rows if r['type'] == 'SELL']
    if buy_rows:
        print(f"\n  BUY signals ({len(buy_rows)}):  avg RSI {sum(r['rsi'] for r in buy_rows)/len(buy_rows):.1f}  "
              f"avg F&G {sum(r['fear_greed'] for r in buy_rows if r['fear_greed'])/max(1,len(buy_rows)):.0f}")
    if sell_rows:
        print(f"  SELL signals ({len(sell_rows)}):  avg RSI {sum(r['rsi'] for r in sell_rows)/len(sell_rows):.1f}  "
              f"avg F&G {sum(r['fear_greed'] for r in sell_rows if r['fear_greed'])/max(1,len(sell_rows)):.0f}")


# ── Section 7: Threshold Analysis ────────────────────────────────────────────

def section_threshold(conn, mode_filter):
    _h("ADAPTIVE THRESHOLD DRIFT")

    for mode in (["spot", "futures"] if not mode_filter else [mode_filter]):
        rows = _rows(conn,
            "SELECT timestamp, type, threshold, strength FROM cycle_log "
            "WHERE mode=? ORDER BY id", (mode,))
        if not rows: continue

        thresholds = [r['threshold'] for r in rows]
        print(f"\n  {BLD}{mode.upper()}{RST}")
        print(f"  Threshold range: {min(thresholds):.2f} → {max(thresholds):.2f}")
        print(f"  Current        : {thresholds[-1]:.2f}")

        # Count threshold changes
        changes = sum(1 for i in range(1, len(thresholds)) if thresholds[i] != thresholds[i-1])
        print(f"  Changes        : {changes}")

        # Over-threshold periods
        high_thr = [r for r in rows if r['threshold'] >= 6.0]
        if high_thr:
            _warn(f"{len(high_thr)} cycles with threshold ≥ 6.0 (signal suppression)")


# ── Section 8: Improvement Recommendations ───────────────────────────────────

def section_improvements(conn, mode_filter, log_path):
    _h("IMPROVEMENT RECOMMENDATIONS")

    issues = []
    suggestions = []

    # --- Data availability check
    trade_count = conn.execute(
        "SELECT COUNT(*) FROM paper_positions WHERE outcome IS NOT NULL"
    ).fetchone()[0]
    cycle_count = conn.execute("SELECT COUNT(*) FROM cycle_log").fetchone()[0]
    signal_count = conn.execute(
        "SELECT COUNT(*) FROM cycle_log WHERE type != 'HOLD'"
    ).fetchone()[0]

    if trade_count < 20:
        issues.append(f"Only {trade_count} closed trades — statistical conclusions unreliable (need ≥30)")
        suggestions.append("Run bot continuously for 2–4 weeks to accumulate enough trades for meaningful stats")

    # --- Hit rate
    if signal_count > 0 and cycle_count > 0:
        hit_rate = signal_count / cycle_count * 100
        if hit_rate > 50:
            issues.append(f"Signal hit rate {hit_rate:.0f}% — threshold may be too LOW (signals too frequent)")
            suggestions.append("Consider raising SIGNAL_THRESHOLD or SPOT_THRESHOLD by 0.5 to filter noise")
        elif hit_rate < 5:
            issues.append(f"Signal hit rate {hit_rate:.0f}% — threshold may be too HIGH (signals too rare)")
            suggestions.append("Consider lowering threshold or reviewing condition weights")

    # --- HTF alignment
    signals_with_htf = _rows(conn,
        "SELECT htf_data FROM cycle_log WHERE type != 'HOLD'")
    if signals_with_htf:
        aligned_count = sum(1 for r in signals_with_htf
                            if json.loads(r['htf_data'] or '{}').get('aligned', False))
        align_pct = aligned_count / len(signals_with_htf) * 100
        if align_pct < 30:
            issues.append(f"Only {align_pct:.0f}% of signals have HTF alignment — many signals go against trend")
            suggestions.append("Consider adding HTF alignment as a HARD REQUIREMENT (not just score): require aligned=True for BUY/SELL")

    # --- Weak signals
    borderline = _rows(conn,
        "SELECT COUNT(*) as n FROM cycle_log "
        "WHERE type != 'HOLD' AND (strength - threshold) < 0.5")
    if borderline and borderline[0]['n'] > 0:
        n = borderline[0]['n']
        issues.append(f"{n} signals fired with score barely above threshold (margin < 0.5)")
        suggestions.append("Add minimum margin gate: require strength >= threshold + 0.3 to reduce borderline signals")

    # --- Condition frequency (most/least triggered)
    all_reasons = []
    for r in _rows(conn, "SELECT reasons FROM cycle_log WHERE type != 'HOLD'"):
        for part in (r['reasons'] or '').split(' | '):
            part = re.sub(r'\(.*?\)', '', part).strip()
            if part:
                all_reasons.append(part[:50])
    if all_reasons:
        # Normalise VWAP values before counting
        normalised = [re.sub(r'\$[\d,\.]+', '$X', r) for r in all_reasons]
        ctr  = Counter(normalised)
        most  = ctr.most_common(3)
        least = [(c, n) for c, n in ctr.most_common() if n <= 2][:3]
        suggestions.append(f"Most-triggered conditions: {', '.join(c for c,_ in most)} — verify these add signal value")
        if least:
            suggestions.append(f"Rarely-triggered conditions: {', '.join(c for c,_ in least)} — consider if weight/threshold is calibrated")

    # --- Skip gate dominance
    if Path(log_path).exists():
        with open(log_path) as f:
            log_lines = f.readlines()
        reentry_skips = sum(1 for l in log_lines if 'worse than last' in l and 'confidence' in l)
        total_skips   = sum(1 for l in log_lines if ('skipping' in l.lower() or 'blocked' in l.lower()) and '[INFO]' in l)
        if reentry_skips > 0 and total_skips > 0:
            pct = reentry_skips / total_skips * 100
            if pct > 50:
                issues.append(f"Re-entry quality gate blocking {pct:.0f}% of skips — may be too strict")
                suggestions.append("Re-entry gate only fires for repeated same-direction entries; if dominating, "
                                   "check if initial entries are entering at bad prices")

    # --- Missing conditions / improvements
    suggestions += [
        "VOLUME PROFILE: Add volume-at-price analysis — identify high-volume nodes as stronger S/R levels",
        "MULTI-SYMBOL: Expand to ETH/USDT correlation — if ETH leads BTC, use as early signal",
        "TIME FILTER: Avoid entries during low-liquidity hours (00:00–04:00 UTC) — widen spreads, fake breakouts",
        "SEASONALITY: Log day-of-week for each signal — some studies show BTC stronger mid-week",
        "BACKTEST VALIDATION: Run backtest.py weekly to check if live win rate diverges from backtest",
        "SLIPPAGE TRACKING: Parse 'Slippage' from log to measure real vs assumed slippage",
    ]

    # --- P&L trend (if enough trades)
    if trade_count >= 3:
        trades = _rows(conn, "SELECT pnl_pct, outcome FROM paper_positions WHERE outcome IS NOT NULL ORDER BY id")
        running = 0
        losing_streak = 0
        max_losing = 0
        cur_streak  = 0
        for t in trades:
            running += t['pnl_pct']
            if t['pnl_pct'] < 0:
                cur_streak += 1
                max_losing = max(max_losing, cur_streak)
            else:
                cur_streak = 0
        if max_losing >= 3:
            issues.append(f"Max losing streak: {max_losing} — check if consecutive losses share market conditions")
            suggestions.append("Log market regime (TRENDING/RANGING/VOLATILE) per trade — filter out trades in low-quality regimes")

    # ── Print ────────────────────────────────────────────────────────────────
    if issues:
        print(f"\n  {BLD}Issues Found:{RST}")
        for i, issue in enumerate(issues, 1):
            _warn(f"[{i}] {issue}")
    else:
        _ok("No major issues detected")

    print(f"\n  {BLD}Actionable Improvements:{RST}")
    for i, s in enumerate(suggestions, 1):
        # Split label from detail
        parts = s.split(': ', 1)
        if len(parts) == 2:
            print(f"  {_c(f'[{i}]', B)}  {BLD}{parts[0]}{RST}: {DIM}{parts[1]}{RST}")
        else:
            print(f"  {_c(f'[{i}]', B)}  {s}")


# ── Section 9: JSON export ────────────────────────────────────────────────────

def export_json(conn, mode_filter):
    out = {}

    # Cycle stats
    q = "SELECT mode, type, COUNT(*) as n FROM cycle_log"
    if mode_filter:
        q += f" WHERE mode='{mode_filter}'"
    q += " GROUP BY mode, type"
    out['cycle_summary'] = _rows(conn, q)

    # Positions
    q = "SELECT * FROM paper_positions WHERE outcome IS NOT NULL"
    if mode_filter:
        q += f" AND mode='{mode_filter}'"
    out['positions'] = _rows(conn, q)

    # Last 10 cycles
    out['recent_cycles'] = _rows(conn,
        "SELECT timestamp, mode, type, strength, threshold, rsi, fear_greed "
        "FROM cycle_log ORDER BY id DESC LIMIT 10")

    print(json.dumps(out, indent=2, default=str))


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    global DB_PATH
    parser = argparse.ArgumentParser(description="SpotSignal analysis + improvement report")
    parser.add_argument("--mode",  choices=["spot", "futures"], help="Filter by mode")
    parser.add_argument("--json",  action="store_true", help="Output JSON instead of text")
    parser.add_argument("--section", help="Run single section: overview|quality|perf|conditions|gates|context|threshold|improvements")
    parser.add_argument("--db", default=DB_PATH, metavar="PATH",
                        help=f"SQLite file to analyse (default: {DB_PATH}). Point this at a "
                             "copy pulled from the server instead of overwriting the local one.")
    args = parser.parse_args()
    DB_PATH = args.db

    conn = _conn()
    if not args.json:
        _describe_source(conn)

    if args.json:
        export_json(conn, args.mode)
        return

    if not args.section or args.section == "overview":
        section_overview(conn, args.mode)
    if not args.section or args.section == "quality":
        section_signal_quality(conn, args.mode)
    if not args.section or args.section == "perf":
        section_performance(conn, args.mode)
    if not args.section or args.section == "conditions":
        section_conditions(conn, args.mode)
    if not args.section or args.section == "gates":
        section_skip_gates(conn, args.mode)
    if not args.section or args.section == "context":
        section_market_context(conn, args.mode)
    if not args.section or args.section == "threshold":
        section_threshold(conn, args.mode)
    if not args.section or args.section == "improvements":
        section_improvements(conn, args.mode, LOG_PATH)

    print(f"\n{DIM}{'─'*60}{RST}")
    print(f"{DIM}  Run with --mode spot|futures to filter{RST}")
    print(f"{DIM}  Run with --json for machine-readable output{RST}")
    print(f"{DIM}  Run with --section <name> for single section{RST}")
    print()


if __name__ == "__main__":
    main()

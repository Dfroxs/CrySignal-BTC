# H-EA: does the BTC bot's rule set pick direction on the scanner's altcoins? (pre-registration)

**Written 2026-11-21, before any figure exists.** The owner asked for a "BTC bot mode for the
scanner coins". Before building a live book, this asks offline, in hours rather than 90 days,
whether the bot's rules have any directional edge on those coins.

## What is run (fixed now)

`backtest.run_backtest(symbol, "1h", "futures", start, end)`, the bot exactly as configured
today (`develop` at the commit that adds this file's script). That means:
- the engine with `DISABLED_CONDITIONS`;
- the backtest's own entry gates, one position per direction, and the cooldown;
- the futures exit geometry (`apply_futures_exit_geometry`);
- SL, TP1 closing half, TP2, trailing stop, the 72-bar cap, and costs.

Nothing is tuned for altcoins.

**Cells:** ETH, SOL, BNB, XRP, DOGE, ADA, LINK, AVAX × 2024, 2025. That is 16 cells, each a
calendar year, on USDT pairs. BTC is excluded because it has its own bot.

**Baseline, a mirror twin:** for every trade the engine takes, the same entry bar and price
are simulated in the opposite direction through the same simulator.
- Its stop, TP1 and TP2 are reflected about the entry (level′ = 2 × entry − level), and it
  pays the same costs.
- A random direction's expected P&L is the mean of the two directions, so
  `engine − mirror` is twice `engine − random`, with the same sign and no seed noise. This
  is the count-matched random baseline: same coin, same moment, same number of trades, same
  exits, with only direction left to skill.

**Statistic:** `d = pnl_pct(engine) − pnl_pct(mirror)` per trade, net of costs. A trade still
OPEN at the window's end is excluded on both sides.

## H-EA-1 criteria

- **PASS** needs both:
  1. the mean `d` is positive in **≥ 12 of 16** cells (a cell with fewer than 10 closed trades
     counts as not positive);
  2. the pooled mean `d`, with a cluster bootstrap over the 16 cells (5,000 draws, seed
     2026), has a **90% CI lower bound > 0**.
- **FAIL:** anything else. A negative sign that holds is a new hypothesis, never a pass.

**Reported only, never criteria:**
- the engine's own net P&L per trade and win rate per cell (does it make money at all);
- trades per cell;
- the share of trades closed by stop, target and time.

## What each result licenses

- **PASS:** design a live "Engine" book in the scanner process (no new process; it reuses
  the hourly engine score), with this mirror twin, under its own prereg. Its 90 days then
  test the edge on live data.
- **FAIL:** the bot's rules do not pick direction on these altcoins. A live book could still
  be built purely for data yield, as an owner's design decision, never as a profit claim.
  No re-scoring on other coins, years, timeframes or parameters.

Run once, on the Mac, and touch nothing on the VPS.

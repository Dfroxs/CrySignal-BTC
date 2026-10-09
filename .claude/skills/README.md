# Project skills

Claude Code skills scoped to this repo. Each skill directory was copied unchanged from
upstream except for a `## SpotSignal notes` section added at the end of its `SKILL.md`.

- **Source:** https://github.com/tradermonty/claude-trading-skills
- **Commit:** `eab8d5cb97b9982d915944cdaa2df972fa396b22` (cloned 2026-10-09)
- **License:** MIT, Copyright (c) 2026 TraderMonty. Full text in
  `LICENSE-claude-trading-skills`.

All bundled scripts were read in full before installing. Both are Python standard library
only. They make no network calls, need no API keys, place no orders, and write only to the
output paths you pass them. Nothing here is wired into the bot.

## Installed

| Skill | Purpose | Why it fits SpotSignal |
|---|---|---|
| `backtest-expert` | Backtest methodology (stress tests, walk-forward, bias checklist) plus `evaluate_backtest.py`, which scores a result on 5 dimensions and lists red flags | Matches the repo's "try to break it" discipline. The notes cover what it lacks: a count-matched random baseline and pre-registration, the ~12–15 fires/year ceiling, and the threshold-sweep trap |
| `residual-edge-analyzer` | OLS + Newey-West attribution of a dated return series against declared baselines, with rolling stability and fail-closed verdicts | Tests whether paper or backtest returns are more than BTC beta. It requires predeclared baselines and out-of-sample scope, and it refuses to call a thin sample an edge |

## Rejected (notable)

- `crypto-regime-analyzer`: an unvalidated heuristic composite of DMA stacks, funding
  bands and dominance. Upstream's own `VALIDATION.md` says there is no evidence for it.
  STEP 1 found that this kind of composite has no edge here, and the bot already fetches
  funding and BTC.D.
- `edge-strategy-reviewer`, `strategy-pivot-designer`, `edge-candidate-agent`,
  `trade-hypothesis-ideator`: coupled to upstream's US-equity edge pipeline YAML. Their
  review rules are keyword and filter-count heuristics. The repo's own pre-registration
  specs in `docs/superpowers/specs/` already do this job more strictly.
- `signal-postmortem`: written for equity tickers, with optional FMP API-key fetches. It
  suggests weight changes from 20-signal samples, which is the noise-retuning CLAUDE.md
  rules out.
- `trade-performance-coach`, `pre-trade-discipline-gate`, `drawdown-circuit-breaker`,
  `trader-memory-core`, `weekly-performance-digest`: built for a discretionary human
  trader's journal (FOMO, revenge trades, thesis state). They do not fit an unattended
  bot. `trader-memory-core` also ships an FMP price adapter.
- `position-sizer`: long-stock share sizing. `futures-position-sizer`: CME contract
  specs, not Binance USDT-M perps.
- `manifoldbt-backtester`: needs the third-party `manifoldbt` package (Apache 2.0 with
  Commons Clause) and duplicates `backtest.py`.
- The rest (screeners, earnings, options, breadth, dividends, sector, news, macro and
  market-top tools) are US equities only.

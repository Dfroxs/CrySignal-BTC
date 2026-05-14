# Paper-Trade Validation Plan — v2.8.0-rc1

**Goal:** Validate 34 commits across 7 audit rounds (since v2.7.0) on live paper-trading for **2 weeks** before promoting to `v2.8.0`. If gates terlalu ketat atau bug muncul, rollback ke `v2.7.0` (commit `cd1f378`).

**Period:** 2 weeks starting `<start-date>` → evaluate `<end-date>`

---

## 1. Pre-flight checklist (sebelum start)

- [ ] Pastikan venv aktif: `source venv/bin/activate`
- [ ] Backup data folder: `cp -r data data.backup-$(date +%Y%m%d)`
- [ ] Verify migrations applied:
  ```bash
  sqlite3 data/signal_history.db "PRAGMA table_info(paper_positions)" | grep exit_price
  sqlite3 data/signal_history.db ".tables" | grep signal_blocks
  ```
  Expected: `exit_price` column ada, `signal_blocks` tabel ada.
- [ ] Verify `.env` (kalau pakai Telegram): `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` ter-set
- [ ] Sanity smoke test 1 cycle:
  ```bash
  python3 run_bot.py    # no --loop
  ```
  Pastikan Phase 1-4 selesai tanpa exception.

---

## 2. Start the loop

```bash
source venv/bin/activate
nohup python3 run_bot.py --loop 60 > /tmp/spotsignal-rc1.log 2>&1 &
echo $! > /tmp/spotsignal-rc1.pid
```

Atau pakai `tmux`:
```bash
tmux new -s spotsignal
source venv/bin/activate
python3 run_bot.py --loop 60
# Ctrl-B then D to detach
```

**Stop bot:**
```bash
kill $(cat /tmp/spotsignal-rc1.pid)
# atau di tmux: kill-session
```

---

## 3. Daily checks (5 min/hari)

```bash
# Bot masih hidup?
ps aux | grep run_bot | grep -v grep

# Ada error/critical?
grep -E 'ERROR|CRITICAL' spotsignal.log | tail -10

# Posisi aktif sekarang
sqlite3 data/signal_history.db "
SELECT mode, type, entry_price, ROUND((SELECT close FROM cycle_log ORDER BY id DESC LIMIT 1) - entry_price, 0) AS pnl_dollar
FROM paper_positions WHERE outcome IS NULL;"

# Trade yang close hari ini
sqlite3 data/signal_history.db "
SELECT mode, type, outcome, pnl_pct, closed_at
FROM paper_positions
WHERE closed_at >= datetime('now','-1 day')
ORDER BY closed_at DESC;"
```

---

## 4. Weekly analysis (30 min/minggu)

### a. Frequency vs old baseline
```sql
-- Trade per mode per minggu
SELECT mode, type, COUNT(*) FROM cycle_log
WHERE timestamp >= datetime('now','-7 days')
GROUP BY mode, type;
```
**Baseline expectation:** old code (May 9–13 live) menghasilkan ~3-5 trade/minggu. Jika new code <50% baseline → gate terlalu ketat.

### b. Gate-block distribution
```sql
SELECT mode, gate, COUNT(*) AS n FROM signal_blocks
WHERE timestamp >= datetime('now','-7 days')
GROUP BY mode, gate ORDER BY n DESC LIMIT 15;
```
**Red flag:** satu gate > 60% dari total blocks → likely terlalu restrictive untuk regime saat ini.

### c. Win rate & profit factor
```sql
SELECT
  mode,
  SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) AS wins,
  SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END) AS losses,
  ROUND(100.0 * SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) / NULLIF(SUM(CASE WHEN outcome IN ('WIN','LOSS') THEN 1 ELSE 0 END),0), 1) AS wr_pct,
  ROUND(SUM(pnl_pct), 2) AS total_pnl_pct,
  ROUND(AVG(pnl_pct), 3) AS avg_pnl_pct
FROM paper_positions
WHERE closed_at IS NOT NULL
GROUP BY mode;
```
**Targets:** Win rate >35% futures, >45% spot. Profit factor >1.2.

### d. PnL by F&G zone (apakah F&G signal valuable?)
```sql
SELECT
  CASE WHEN s.fear_greed <= 25 THEN 'extreme-fear'
       WHEN s.fear_greed <= 45 THEN 'fear'
       WHEN s.fear_greed <= 55 THEN 'neutral'
       WHEN s.fear_greed <= 75 THEN 'greed'
       ELSE 'extreme-greed' END AS fng_zone,
  COUNT(*) AS n,
  ROUND(AVG(p.pnl_pct), 3) AS avg_pnl
FROM signals s JOIN paper_positions p ON s.id = p.signal_id
WHERE p.outcome IN ('WIN','LOSS')
GROUP BY fng_zone ORDER BY avg_pnl DESC;
```

### e. Daily P&L curve
```sql
SELECT
  date(closed_at) AS day,
  mode,
  ROUND(SUM(pnl_pct), 2) AS pnl_pct,
  COUNT(*) AS trades
FROM paper_positions
WHERE closed_at >= datetime('now','-14 days')
GROUP BY day, mode ORDER BY day, mode;
```

---

## 5. Red flags — stop & investigate

| Indicator | Action |
|---|---|
| Equity < 92% of starting balance | Stop, review losing trades, consider rollback |
| 5+ consecutive losses in 24h | Pause, check if regime mismatch |
| Single gate > 80% of blocks | Tune (lihat #6) |
| 0 trades in 5+ consecutive days | Gates kombinasi terlalu ketat → tune |
| `daily_loss_limit` breaker fires > 1×/minggu | Lower position size atau raise threshold |
| Exception > 3× di `spotsignal.log` | Fix root cause sebelum lanjut |

---

## 6. Tuning levers (kalau perlu adjust mid-period)

Edit `config.py`, restart bot — TIDAK perlu rollback:

| Gejala | Tune |
|---|---|
| Frequency turun > 50% | Lower `min_initial_confidence` ke `"WEAK"` di `RISK_CONFIG["pyramid"]` |
| Futures SHORT tidak pernah open | Cek dominant gate, kalau `trend_confluence` → loosen ke 1/3 (edit `_trend_confluence_for_direction` di `run_bot.py`) |
| Spot pyramid blocked by aggregate risk | Raise `max_aggregate_risk_pct` 5.0 → 6.0 |
| VOL_EXIT terlalu sering | `vol_expansion_exit_mult` 2.0 → 2.5 |
| TIME_EXIT terlalu sering | `max_position_hours_spot/futures` 72 → 96 |

**Setiap tune = catat di `CHANGELOG.md` sebagai amendment ke RC.**

---

## 7. End-of-2-weeks decision matrix

| Hasil | Action |
|---|---|
| WR ≥ target, PF > 1.2, no red flags | **Promote** ke `v2.8.0` |
| Frequency rendah tapi PnL positif | Tune 1 gate, extend 1 minggu |
| Mixed: 1 mode profit, 1 mode rugi | Investigate mode yang rugi, mungkin disable temporarily |
| Multiple red flags atau PnL < -5% | **Rollback** ke `v2.7.0` |
| Bug kritikal | Hotfix di branch terpisah, retest |

---

## 8. Promote to v2.8.0 (jika berhasil)

```bash
git checkout main
git pull origin main
git merge develop --no-ff -m "release: v2.8.0 — validated 2 weeks paper"
git tag -a v2.8.0 -m "Stable: 7 audit rounds + 2 weeks paper validation"
git push origin main v2.8.0
```

---

## 9. Rollback to v2.7.0 (jika gagal)

```bash
# Stop bot dulu
kill $(cat /tmp/spotsignal-rc1.pid)

# Backup data dari periode RC
mv data data.rc1-failed-$(date +%Y%m%d)
cp -r data.backup-<original-date> data

# Checkout state lama
git checkout main
git revert <merge-commit-if-already-merged>
# atau jika belum merge:
git checkout cd1f378   # v2.7.0 state
```

---

## 10. Apa yang TIDAK perlu disiapkan sekarang

Logging news headline **tidak perlu** untuk validasi 2 minggu — semua pertanyaan strategi bisa dijawab dari 4 tabel existing (`cycle_log`, `signals`, `paper_positions`, `signal_blocks`). Jika setelah 2 minggu ternyata sering perlu forensik level "headline apa saat itu", baru implement archive CSV atau structured `news_log` table.

---

## Reference: 34 commits sejak v2.7.0

| Kategori | Jumlah | Risiko |
|---|---|---|
| PURE_FIX (NameError, schema, dead code) | ~22 | Zero |
| BEHAVIOR_CHANGE (gate symmetry, breaker, exits) | ~8 | Net safer + sedikit restrictive |
| SCORE_CHANGE (HTF penalty, F&G de-double-count) | ~2 | Skor sedikit lebih ketat |
| PAPER P&L ACCURACY | ~3 | Tracking lebih jujur |

Detail lengkap: lihat `CHANGELOG.md`.

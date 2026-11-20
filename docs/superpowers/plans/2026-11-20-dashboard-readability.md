# Dashboard Readability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every open position is a card that names its method and states, in plain words, its
status, P&L, SL/TP1/TP2, leverage and time left. Each method has its own tab, opened by a
"Cara kerja" box.

**Architecture:** The existing server-rendered page in `agents/dashboard.py`.
- Open positions from both sources are normalized into one card dict, and a single
  `_card()` renders it.
- The method tabs and the summary list are built from those cards.
- No new data source. Only `partial_pnl` joins the BTC open-position query.

**Tech Stack:** Python 3.14, stdlib `http.server` page, the existing CSS/JS strings. Tests
are in `test_pipelines.py`, run with `./venv/bin/python test_pipelines.py`.

**Spec:** `docs/superpowers/specs/2026-11-20-dashboard-readability-design.md`

## Global Constraints

- Read-only DB access (`_ro`/`mode=ro`). No pandas import in `agents/dashboard.py`.
- Indonesian number format everywhere, through `_n`/`_money`/`_pnl`. No `:.2f` string
  formatting for user-visible money.
- Carried positions (`carried=True`) never count in any balance, total or count of live
  positions.
- All copy is Indonesian. Tab names are exactly `Ringkasan`, `Bot BTC · aturan indikator`,
  `Scanner Claude · AI pilih koin`, `Sistem`.
- The trading path is untouched. Deploy is `git pull` plus a `spotsignal-dash` restart.

## Review Focus

1. **BTC position after TP1 whose `partial_pnl` is NULL** (older rows). The card shows the
   final half as "–", not 0% and not a crash. Covered in Task 1.
2. **Scanner position whose current price is missing** (`move_pct` None). The card shows "–"
   for P&L and stays in the list. Covered in Task 2.
3. **Hours left ≤ 0.** The text is "segera", not a negative number. Covered in Task 2.
4. **A book whose positions are all carried.** It shows only the folded "N posisi bawaan ▸"
   line, and the live count stays 0. Covered in Task 2.
5. **No open positions at all.** Each method tab shows one empty-state line, and Ringkasan
   says "Tidak ada posisi terbuka." Covered in Task 3.

---

### Task 1: BTC open-position facts carry what a card needs

**Files:**
- Modify: `agents/dashboard.py` (`db_facts`, open-position query near line 232)
- Test: `test_pipelines.py`

**Interfaces:**
- Produces: each `f["open"]` row gains:
  - `partial_pnl: float|None`;
  - `tp1: float` (= `take_profit`);
  - `trailing: bool` (`trailing_stop` set and ≠ `stop_loss`);
  - `sizing: dict|None`, with keys `leverage`, `margin_pct`, `liquidation_price`, from
    `signals.sizing.calculate_futures_position`. Futures only, and None on failure.

- [ ] **Step 1: Write the failing test** `test_dashboard_btc_open_rows_carry_partial_tp1_trailing_and_sizing`.
  Use `_temp_history_db()` with one futures SELL row: entry 62778.08, stop_loss 65476.05,
  trailing_stop 60703.85, take_profit 58731.12, tp2 54684.16, partial_closed 1,
  partial_pnl 6.246. Assert:
  - the row has `partial_pnl == 6.246`, `tp1 == 58731.12`, `trailing is True` and
    `sizing["leverage"] == 3`;
  - a second row with `partial_pnl` NULL yields `partial_pnl is None`.
- [ ] **Step 2: Run** `./venv/bin/python -c "import test_pipelines as t; t.test_dashboard_btc_open_rows_carry_partial_tp1_trailing_and_sizing()"`. Expected: KeyError or AssertionError.
- [ ] **Step 3: Implement.** Add `partial_pnl` to the `cols` tuple of the open query and set the
  three derived keys in the existing per-row loop. Move the sizing call out of `_btc_size`
  into a helper `_btc_sizing(p) -> dict|None`; `_btc_size` then formats from it.
- [ ] **Step 4: Run** the test, then the full suite. Expected: PASS, and the suite all pass.
- [ ] **Step 5: Commit** `feat(dashboard): BTC open rows carry partial P&L, TP1, trailing and sizing`.

### Task 2: One card per position

**Files:**
- Modify: `agents/dashboard.py` (new `METHODS`, `_btc_cards`, `_scanner_cards`, `_card`,
  `_cards_block`; CSS for `.pcard`)
- Test: `test_pipelines.py`

**Interfaces:**
- Consumes: Task 1's row keys; scanner rows from `scan_book()["open"]` (`coin`, `side`,
  `opened_bar`, `entry`, `price`, `stop`, `target`, `move_pct`, `upnl`, `size_usd`,
  `carried`).
- Produces:
  - `METHODS = {"btc": ("Bot BTC", "aturan indikator (bukan AI)"), "scanner": ("Scanner Claude murni", "AI memilih koin"), "hybrid": ("Scanner Claude + engine", "AI memilih koin, dibantu skor engine"), "random": ("Scanner Acak", "patokan: koin diundi")}`
  - `_btc_cards(f) -> list[dict]` and `_scanner_cards(f, key) -> list[dict]` return card
    dicts with these keys: `method`, `book`, `coin`, `side`, `opened`, `status`,
    `pnl_final_pct`, `pnl_open_pct`, `pnl_usd`, `size_usd`, `entry`, `price`, `stop`,
    `trailing`, `tp1`, `tp1_hit`, `tp2`, `target`, `leverage`, `hours_left`, `carried`.
  - `_card(c: dict, now) -> str` (HTML for one card).
  - `_cards_block(cards: list[dict], now) -> str`: live cards in full, then one
    `<details>` per book holding the carried ones, summary text `"{n} posisi bawaan
    (tidak dihitung saldo)"`.

- [ ] **Step 1: Write the failing test** `test_dashboard_position_cards_say_method_status_levels_and_leverage`.
  Build `f` by hand (as the existing `_positions` test at about line 5568 does).
  - **BTC row:** the Task 1 example, `move_pct` 6.94, `hours` 26.7.
  - **Scanner rows (`f["scanner"]["book"]["open"]`):**
    - one live AVAX SHORT: entry 7.0, stop 7.49, target 5.30, move_pct 3.14, upnl 3.11,
      size_usd 99.13, opened 2 h before `now`;
    - one carried ETH;
    - one live row with `move_pct` None, opened 25 h before `now`.

  Assert, on the joined HTML:
  - "Bot BTC" and "aturan indikator (bukan AI)" are present.
  - "TP1 tercapai, separuh posisi sudah ditutup" and "+6,25%" (final half) are present.
  - "58.731,12" carries the ✓ mark, and "54.684,16" is present.
  - "ikut bergerak" is next to the stop, and "3x" and "likuidasi" are present.
  - "Scanner Claude murni" and "AI memilih koin" are present.
  - "satu target, tanpa TP2" and "tanpa leverage" are present.
  - "+$3,11" is present.
  - The missing-price row shows "–" and "segera".
  - "1 posisi bawaan (tidak dihitung saldo)" sits inside a `<details>`.
  - Carried ETH does not appear outside that `<details>`.
- [ ] **Step 2: Run** it. Expected: AttributeError (`_btc_cards` missing).
- [ ] **Step 3: Implement** the functions above.
  - Status: `"TP1 tercapai, separuh posisi sudah ditutup"` when `partial_closed`, else
    `"berjalan"`. The header adds "bawaan (tidak dihitung saldo)" when carried.
  - Hours left: BTC cap from `RISK_CONFIG` as `_positions` does today; scanner 24.
  - Price formatting: the existing `px` rule (4 decimals below 10).
  - Old `_positions()` stays for now; Task 3 removes its callers.
- [ ] **Step 4: Run** the test and the full suite. Expected: PASS, all pass.
- [ ] **Step 5: Commit** `feat(dashboard): one card per open position, with method, status, levels and leverage`.

### Task 3: Method tabs with "Cara kerja", and the summary list

**Files:**
- Modify: `agents/dashboard.py` (`TABS`, `render` panels, `_btc_tab` head, `_picks_tab`
  head, new `HOW_BTC`, `HOW_SCANNER`, `_position_lines`; remove `_positions` and its table
  in `_btc_tab`)
- Test: `test_pipelines.py` (new test; update the `_positions` assertions near line 5578)

**Interfaces:**
- Consumes: Task 2's `METHODS`, `_btc_cards`, `_scanner_cards`, `_cards_block`.
- Produces:
  - `TABS = (("ringkasan", "Ringkasan"), ("btc", "Bot BTC · aturan indikator"), ("koin", "Scanner Claude · AI pilih koin"), ("sistem", "Sistem"))`
    The keys stay, so `#koin`/`#btc` links still work.
  - `_position_lines(f) -> str`: one line per live position (method label, coin, side,
    status, P&L), each an `<a data-tab=...>` to its tab. The carried count shows once per
    method.

**"Cara kerja" copy (exact):**
- `HOW_BTC`: the five bullets of the approved mockup:
  - "Robot berbasis aturan, bukan AI: menghitung sekitar 20 indikator (EMA200, RSI, MACD, volume, dan lainnya) di candle BTC."
  - "Posisi dibuka kalau skor melewati ambang dan lolos pemeriksaan keamanan."
  - "Hanya BTC. Futures 1 jam (LONG/SHORT, leverage dihitung per posisi) dan spot 4 jam (LONG saja)."
  - "Keluar posisi: SL, TP1 (tutup separuh), TP2, trailing stop, maksimal 72 jam (futures) atau 48 jam (spot)."
  - "Claude hanya memberi opini (setuju/tidak) dan tidak memutuskan trade."
- `HOW_SCANNER`: the four bullets of the mockup:
  - "Setiap jam Claude (AI) membaca angka 9 koin lalu memilih LONG, SHORT, atau tidak sama sekali."
  - "Ada tiga akun: Claude murni, Claude + skor engine, dan Acak. Acak adalah pembanding: koinnya diundi, jumlah dan arahnya sama dengan pilihan Claude."
  - "Setiap posisi memakai 10% saldo, tanpa leverage, satu SL dan satu target, maksimal 24 jam."
  - "Yang diuji: apakah pilihan Claude lebih baik dari undian?"

- [ ] **Step 1: Write the failing test** `test_dashboard_method_tabs_open_with_how_it_works_and_summary_links`.
  - Render `dsh.render(f)` for an `f` with one BTC and one scanner position (reuse Task 2's
    builder) and `errors=[]`.
  - Assert the four tab labels appear in order.
  - Assert "Cara kerja Bot BTC" is inside `id="tab-btc"` and "Cara kerja Scanner Claude"
    inside `id="tab-koin"`.
  - Assert `tab-ringkasan` holds a `data-tab="btc"` link with "BTC" and "TP1 tercapai".
  - Assert the old "Posisi yang sedang berjalan" table is gone.
  - With `open` empty everywhere, assert "Tidak ada posisi terbuka." in Ringkasan and
    "Tidak ada posisi BTC" in the BTC tab.
- [ ] **Step 2: Run** it. Expected: FAIL on the tab labels.
- [ ] **Step 3: Implement.** Make the tab and panel changes, then:
  - `btc` panel: `HOW_BTC` box, then `_cards_block(_btc_cards(f))`, then the rest of
    `_btc_tab` without its old table, then `_runs(f)`.
  - `koin` panel: `HOW_SCANNER` box, then `_cards_block` for each arm in `ARMS` order,
    then the existing picks content.
  - Ringkasan: replace `_positions(f)` with `_position_lines(f)`.
  - Point the existing assertions near line 5578 at `_cards_block(_btc_cards(f), now)`.
- [ ] **Step 4: Run** the new test and the full suite. Expected: PASS, all pass.
- [ ] **Step 5: Commit** `feat(dashboard): a tab per method opened by 'Cara kerja'; summary lists positions by method`.

### Task 4: One number format, and a fuller glossary

**Files:**
- Modify: `agents/dashboard.py` (the four `:.2f` cost lines, about lines 1206 and 1330–1333;
  the glossary in `render`)
- Test: `test_pipelines.py`

- [ ] **Step 1: Write the failing test** `test_dashboard_money_is_indonesian_everywhere_and_glossary_explains_levels`.
  - `_picks_tab` with `s["usd"]=1.14` contains "$1,14", and no `$1.14`.
  - `_system_tab` cost lines with 0.63 / 3.44 / 9.91 show "$0,63", "$3,44" and "$9,91".
  - `render` output's glossary has `<dt>` entries "TP1", "TP2", "Trailing stop",
    "Leverage", "Likuidasi" and "Bawaan".
- [ ] **Step 2: Run** it. Expected: FAIL on "$1,14".
- [ ] **Step 3: Implement.**
  - Replace the formatted strings with `_money(...)`.
  - Add the glossary entries (one line each):
    - TP1 = "target pertama; separuh posisi ditutup di sini";
    - TP2 = "target kedua untuk separuh sisanya";
    - Trailing stop = "stop yang ikut bergerak mengikuti harga untuk mengunci untung";
    - Leverage = "pengali modal pada futures";
    - Likuidasi = "harga di mana posisi ber-leverage habis";
    - Bawaan = "posisi dari sebelum reset; berjalan sampai tutup, tidak dihitung di saldo sekarang".
- [ ] **Step 4: Run** the test and the full suite. Expected: PASS, all pass.
- [ ] **Step 5: Commit** `fix(dashboard): Indonesian money format everywhere; glossary explains TP1/TP2/trailing/leverage/bawaan`.

### Task 5: Check on live data, document, deploy

**Files:**
- Modify: `CHANGELOG.md`, `agents/qa_knowledge.md` (the tab names, one line)

- [ ] **Step 1: Render against copies of the live databases.** Use `.backup` copies of
  `signal_history.db` and the three scanner DBs, run `dsh.render(dsh.collect(root, db,
  scanner_db))`, and grep the text. Expected:
  - #20's card shows TP1 ✓, "+6,25%", TP2 54.684,16, 3x and likuidasi;
  - the scanner cards show the right methods;
  - the carried positions are folded;
  - no "Bagian tidak terbaca".
- [ ] **Step 2: Write the CHANGELOG entry** and update the Q&A knowledge's tab names. Commit
  `docs: dashboard readability`.
- [ ] **Step 3: Cherry-pick** onto a `deploy/dashboard-readability` branch from
  `origin/main`, run the full suite, push, and open the PR (dashboard restart only).
- [ ] **Step 4: After the owner merges:** `git pull` on the VPS and restart `spotsignal-dash`.
  `curl` the page and check the same strings as Step 1.

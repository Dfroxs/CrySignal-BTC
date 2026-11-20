# Dashboard readability: say which method, and say a position's state plainly

**Owner, 2026-11-20:**
- "Status BTC & scanner posisi berjalan kurang jelas, membuat bingung: ini dari metode yang
  mana."
- "Keterangan untung rugi, SL, TP1, TP2, leverage, dan status kurang jelas."
- "Tab pilih koin & bot BTC bikin bingung: ini metode yang mana."

The trigger: the owner asked whether #20's "target $54.684,16" was right. TP1 had been hit and
half the position closed at +6.25%, but the page said so only as a small "T2".

## Goal, non-goals, anti-goals

- **Goal:** a viewer can tell at a glance which method a position or account belongs to,
  what state it is in, and what it has made or lost.
- **Non-goals:** new data or new metrics. The trading path, the scanner and the scorers are
  untouched; only `agents/dashboard.py` and its tests change.
- **Anti-goals:**
  - a page that reads more cleanly but drops a figure the owner relies on;
  - any number shown that disagrees with the database.

## Approaches (both picked by the owner in the browser mockups)

**Positions**
- **A. One card per position: picked.**
- B, a table per method, was clearer for many rows but kept the "what does this column
  mean" problem.

**Tabs**
- **A. One tab per method, opened by a "Cara kerja" box: picked.**
- B, tabs by question (positions / results / tests), mixes methods on every tab, so each
  method's explanation ends up scattered.

**Build** (my call): edit the existing server-rendered page in place, which is the only
sensible way. A separate frontend, or an artifact, would duplicate the read-only data layer
the dashboard already has.

## The design

**Tabs:** `Ringkasan | Bot BTC · aturan indikator | Scanner Claude · AI pilih koin | Sistem`.

Each method tab opens with a **"Cara kerja"** box (four or five lines):
- who decides (rules, not AI; or Claude);
- which coins;
- futures or spot, and leverage or not;
- how a position closes;
- what the test asks.

The scanner tab's box explains the three accounts, the random one included as the baseline.

**Position card** (both method tabs):
- **Header:** a colored method label and a one-line description. Then coin, side, when it
  opened, and the book (futures or spot).
- **Status, in words:** "berjalan", "TP1 tercapai, separuh ditutup", "bawaan, tidak dihitung
  saldo", plus the time left before the forced close.
- **Untung/rugi in % and $:**
  - for BTC after TP1, the closed half (final, from `partial_pnl`) is shown apart from the
    open half (not final);
  - scanner cards show the position's own $ on its size.
- **Levels:** entry, price now, SL ("ikut bergerak" when trailing), TP1 (✓ once hit) and
  TP2. Scanner cards show one target and say "satu target, tanpa TP2".
- **Leverage:** BTC futures shows the sizing module's leverage, margin and liquidation, as
  `_btc_size` does today. Scanner cards say "tanpa leverage".

**My calls**
- **Carried positions** fold into one line per book, "N posisi bawaan ▸", and open on click.
  Live positions show in full.
- **Ringkasan** keeps the market strip, the account board, the test board, statistics and
  activity. Instead of the full positions table it gets one line per position (method label,
  coin, side, status, P&L), pointing to its method tab.
- **One number format, Indonesian, everywhere:** the API cost lines change from "$1.14" to
  "$1,14".
- **Glossary** gains TP1, TP2, trailing stop, leverage/liquidation and "bawaan".

## Constraints

- **The dashboard stays read-only** (`mode=ro`), keeps its 50 MB footprint (no pandas), and
  keeps the 60-second refresh and the light/dark themes. It must stay readable on a phone.
- **Every figure on a card comes from data the dashboard already reads:** `paper_positions`,
  `scan_positions`, `cycle_log`, and the sizing module. Only `partial_pnl` joins the
  open-position query.
- **Existing guards stay green:** the read-only and broken-panel test, and the carried
  positions kept out of the balances.
- **Deploy:** `git pull` and a `spotsignal-dash` restart. No bot restart, and no test
  window moves.

## Left to the builder

- Exact CSS, card spacing, and how the cards wrap on a narrow screen.
- Whether the BTC spot book (empty today) shows an empty-state line or nothing.

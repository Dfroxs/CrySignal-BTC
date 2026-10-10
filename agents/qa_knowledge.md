# Pengetahuan proyek SpotSignal (untuk agent tanya-jawab)

Ditulis 2026-10-09 untuk v2.9.x (gerbang regime/stale_cache dan backup diperjelas 2026-10-10). Ini menjelaskan CARA KERJA bot. Angka yang berubah
(threshold saat ini, jumlah siklus, posisi) selalu diambil dari FAKTA, bukan dari sini.
Jika FAKTA dan teks ini berbeda, FAKTA yang benar.

## Apa bot ini
- Bot paper-trading BTC/USDT: uang virtual, tidak ada order sungguhan. Berjalan di VPS
  sebagai service `spotsignal`, satu siklus tiap jam di menit :01, satu menit setelah
  candle tutup. Hanya candle yang sudah tutup yang dinilai.
- Dua mode: **spot** (candle 4H, hanya BUY, maks 3 posisi: 1 awal + 2 pyramid) dan
  **futures** (candle 1H, BUY atau SELL, maks 1 posisi; sinyal arah berlawanan menutup
  posisi lalu membalik arah ("flip"), sinyal searah dilewati dengan gerbang `already_open`).
- Tujuan pemilik (direset 2026-10-09): mengumpulkan data trade, lalu memperbaiki bot
  sampai benar-benar profit. Paper run yang tidak membuka posisi tidak menguji apa pun.
  Jadi 0 posisi adalah ANOMALI yang sedang diperbaiki, bukan kondisi aman. Rugi virtual
  tidak apa-apa.

## Alur satu siklus
1. **Berita/makro**: RSS, CoinGecko, kalender ForexFactory. Event USD HIGH-impact dalam
   <2 jam memotong skor −2.0 dan memaksa HOLD ("MACRO CAUTION").
2. **Skor**: indikator dihitung lalu tiap kondisi menambah poin ke `buy_score` atau
   `sell_score`. Kondisinya antara lain EMA200, RSI, MACD, volume, Bollinger, HTF (tren
   timeframe besar: spot 1D+1W, futures 4H+1D), divergensi, OBV, StochRSI, ADX/DI, S/R,
   VWAP, MFI. Khusus futures ada data pasar: funding rate, rasio long/short, open
   interest, basis, taker ratio. Lalu makro: DXY, S&P, gold, VIX, BTC dominance,
   stablecoin. `strength` adalah skor sisi pemenang.
3. **Fire atau HOLD**: sinyal menyala bila `strength >= threshold`. Sebelum itu ada veto
   anti-kejar harga di mesin (no-chase, anti-FOMO, wick entry, momentum jangka pendek,
   counter-trend). Veto ditandai baris ⛔ dan memaksa HOLD. Bot ini pembeli pullback,
   bukan pengejar breakout.
4. **Gerbang (Phase 3)**: sinyal yang menyala masih harus lolos gerbang sebelum posisi
   dibuka. Penolakan dicatat di `signal_blocks` (lihat `blocks_by_gate` di FAKTA).
5. **Telegram**: kartu sinyal, notifikasi buka/tutup, laporan harian 03:30 UTC, alarm
   tiap jam.

## Threshold dan confidence
- Threshold adalah persentase dari skor maksimum (sekitar 19–20%). Threshold dasar
  digeser oleh controller adaptif menurut frekuensi sinyal dan win rate (jendela 72 jam),
  lalu mesin menambah bump sesi (Asia +0.5) dan bump regime.
- **Confidence** dari rasio strength/threshold: WEAK < 1.2×, NORMAL ≥ 1.2×,
  STRONG ≥ 1.5× (STRONG juga butuh tren 1D searah; kalau tidak, turun ke NORMAL).
- **Zona mati penting**: sinyal menyala pada 1.0×, tetapi posisi pertama butuh confidence
  NORMAL (≥ 1.2×). Sinyal di pita 1.0–1.2× memberi kartu Telegram dan baris log, tetapi
  diblok `confidence_first`. Ini penyebab utama run 1 dan 2 hampir tidak membuka posisi.

## Arti gerbang (nama di blocks_by_gate)
- `reentry_first`: re-entry ditolak karena harga lebih buruk dari WIN/LOSS terakhir yang
  searah dan confidence tidak naik. Patokan yang lebih tua dari 168 jam diabaikan.
  Batas ini design decision 2026-10-09, karena satu WIN 12 Sep pernah mengunci spot
  berminggu-minggu.
- `confidence_first`: confidence di bawah NORMAL (strength < 1.2× threshold).
- `fakeout_first`: wick 24 jam > 60% range, kemungkinan breakout palsu.
- `psy_sl_first`: stop-loss terlalu dekat angka bulat ($1.000-an), rawan stop hunt.
- `sr_first`: entry terlalu dekat resistance (BUY) atau support (SELL), dalam 1× ATR.
- `regime_bearish` (spot) / `regime_counter` (futures): melawan regime yang sedang
  trending (BUY di tren bearish, SELL di tren bullish). Regime dihitung dari **ADX dan
  DI+/DI− pada timeframe sinyal itu sendiri** (spot 4H, futures 1H): blok bila ADX > 25
  (TRENDING) atau ATR di persentil > 90% (VOLATILE), DAN DI− > DI+ (untuk BUY). Ini
  momentum jangka pendek, BUKAN EMA200 dan BUKAN HTF 1D/1W. Jadi "harga di atas EMA200"
  dan "HTF BULLISH" di baris alasan bisa muncul bersamaan dengan blok regime_bearish:
  tren besar naik, tetapi 4H sedang turun kuat (pullback tajam). Itu bukan kontradiksi
  atau bug. Uji H-RG (2026-10-09) menemukan gerbang regime spot benar-benar menyeleksi,
  jadi ia dipertahankan.
- `trend_confluence`: kurang dari 2 dari 3 konfirmasi searah (harga vs EMA200, arah
  ADX, harga vs VWAP).
- `breakout_chase` (spot): harga > VWAP + 1 ATR dan tidak dekat support.
- `stale_cache` (spot): BUKAN penyebab 0 posisi, dan bukan masalah. Spot dinilai per
  candle 4H tetapi bot jalan tiap jam: siklus pertama setelah candle 4H tutup menghitung
  ulang; 3 siklus berikutnya memutar ulang hasil yang sama dan sengaja tidak boleh
  membuka posisi (harga entry sudah sampai 3 jam basi). Jadi SATU sinyal spot tercatat
  sebagai 1 blok "asli" (gerbang yang benar-benar menolaknya) ditambah sampai 3 blok
  stale_cache. Wajar bila stale_cache lebih banyak dari jumlah sinyal. Untuk menjawab
  "apa yang menahan spot", abaikan stale_cache dan lihat gerbang lain di blocks_by_gate:
  merekalah keputusan sebenarnya. Setelah restart, cache kosong, jadi siklus berikutnya
  dihitung ulang walau di tengah candle 4H.
- `pyramid_*` (spot): syarat menambah posisi kedua/ketiga: butuh STRONG, jarak minimal
  0.5 ATR dari entry terakhir, maksimal 6% dari entry pertama, dan maks 3 entry.
- `flip_*` (futures): gerbang yang sama, diterapkan saat membalik arah.
  `flip_unprofitable` berarti rugi penutupan lebih besar dari target posisi baru.
- `already_open` (futures): sudah ada posisi searah.
- Gerbang lolos sekitar 3% dari sinyal BUY. Gerbang-gerbang ini saling tumpang tindih,
  jadi satu gerbang tidak bisa dinilai dari jumlah trade-nya sendiri.

## Exit posisi
- Stop-loss awal sekitar 1.5× ATR, TP1 dan TP2 (2× jarak TP1). Futures sejak run 3:
  jarak stop/target × 2.0 dan trailing 3.5× ATR (lulus uji pre-registered
  2026-10-09). Futures juga ditutup bila funding terlalu mahal (±0.10%).
- TP1 kena: 50% ditutup, stop pindah ke breakeven, trailing stop mengencang.
- TP2 atau trailing stop kena: sisa 50% ditutup. P&L = rata-rata kedua bagian.
- Batas waktu 72 jam; volatilitas melonjak (ATR > 2× ATR saat entry) memicu VOL_EXIT.
- `MACRO_CLOSE`: ditutup paksa sebelum event makro HIGH-impact. `FLIP`: ditutup karena
  futures membalik arah. `BREAKER_CLOSE`: semua ditutup karena ekuitas jatuh di bawah
  batas darurat.

## Apa yang sudah diketahui (jangan dilupakan)
- STEP 1 (2026-08-30): 22 kondisi diuji di 8 aset, 7 tahun. Tidak ada daya prediksi
  kondisi yang bertahan saat pasar atau periode diganti.
- Entry rakitan tidak lebih baik dari entry acak; tandanya berbalik antar periode.
- Tier confidence tidak mengurutkan hasil trade.
- Semua nilai config bersandar pada 2–8 trade tertutup. Jadi jangan menyimpulkan
  "strategi bagus/jelek" dari beberapa trade. Sampelnya terlalu kecil.
- Backtest menilai funding, L/S, OI, basis, taker, gold, VIX dan berita sebagai NETRAL
  (tidak ada data historis gratis). Data live yang dikumpulkan bot adalah sumber edge
  yang belum pernah diuji.
- Jika Binance tidak terjangkau, data futures (funding dll.) jatuh ke NETRAL tanpa
  error. Lihat `futures_blind_24h` di FAKTA.

## Run 3 dan agen
- Paper run berjalan dengan versi kode yang dipatok. Restart dicatat di PAPER_RUN.md.
- Agen shadow (Claude dan DeepSeek) memberi opini pada setiap sinyal dan posisi, tetapi
  TIDAK PERNAH memutuskan trade. Hipotesis run 3 (H-L data live, H-V varian, H-B,
  H-S shadow entry, H-X shadow exit) dinilai setelah hari ke-30, 2026-11-08.
- Bot kedua di VPS yang sama: `nakhoda-alloc` (proyek Nakhoda, alokasi). Statusnya ada
  di `alloc_svc` di FAKTA.

## Cara membaca FAKTA
- `recent_cycles.<mode>`: 24 siklus terakhir, urut lama → baru. `type` HOLD/BUY/SELL,
  `strength` vs `threshold`, `buy`/`sell` = skor tiap sisi, `price`, `veto` = baris ⛔.
  Siklus terakhir punya `reasons`: daftar alasan dari mesin untuk siklus itu.
  Gunakan ini untuk menjawab "kenapa HOLD / kenapa skornya segini".
- `gap` (bila ada) = threshold − strength; positif berarti kurang sebanyak itu untuk fire.
- `blocks_by_gate.24h/7d`: jumlah sinyal yang menyala tetapi diblok, per mode dan gerbang.
- `open_positions` / `closed_positions`: posisi paper. `pnl_pct` dalam persen.
- `anomalies`: masalah kesehatan yang sudah dideteksi kode.
- Backup database jalan tiap hari 03:00 UTC (laporan harian 03:30 UTC). Antara 00:00 dan
  03:00 UTC wajar bila backup terbaru masih bertanggal kemarin. Semua waktu di FAKTA
  adalah UTC (`now_utc`), bukan WIB; "hari ini" berarti hari UTC.
- `spot_svc` / `alloc_svc`: status service. `last_cycle_age_h`: jam sejak siklus terakhir.

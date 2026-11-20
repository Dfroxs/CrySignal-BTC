# Pengetahuan proyek SpotSignal (untuk agent tanya-jawab)

Ditulis 2026-10-09 untuk v2.9.x (gerbang regime/stale_cache dan backup diperjelas 2026-10-10; run 5, reset dan perbaikan data diperbarui 2026-11-20). Ini menjelaskan CARA KERJA bot. Angka yang berubah
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
   interest. Basis dan taker ratio tetap diambil dan dicatat, tetapi sejak 2026-10-15
   tidak dinilai lagi (basis terbaca terbalik, taker tanpa daya prediksi), sehingga skor
   maksimum futures 25.0. Lalu makro: DXY, S&P, gold, VIX, BTC dominance,
   stablecoin. `strength` adalah skor sisi pemenang.
3. **Fire atau HOLD**: sinyal menyala bila `strength >= threshold`. Sebelum itu ada veto
   di mesin yang memaksa HOLD, ditandai baris ⛔. Yang aktif di kedua mode: counter-trend
   (melawan tren 1D) dan momentum jangka pendek (kemiringan 5-SMA). Tiga veto anti-kejar
   harga (no-chase, anti-FOMO, wick entry) DIMATIKAN: spot sejak 2026-10-09, futures sejak
   deploy 2026-10-17. Keduanya keputusan desain pemilik demi data, bukan hasil uji.
   Spot masih punya gerbang Phase 3 `breakout_chase`; futures tidak punya batas kejar.
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
  searah dan confidence tidak naik. Patokan yang lebih tua dari 168 jam diabaikan, dan
  sejak 2026-11-19 juga patokan dari sebelum run sekarang dimulai.
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
- Sejak 2026-11-20 stop dan target bekerja seperti order yang menunggu di bursa: sebelum
  tiap pemeriksaan (:01 dan :31), candle 1 menit sejak pemeriksaan terakhir ditelusuri,
  jadi stop atau target yang tersentuh di antaranya terisi di levelnya.
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

## Run sekarang dan reset 2026-11-20
- Run sekarang adalah **run 5** (lihat `run_start` di FAKTA). Tiap run, akun demo di
  dashboard mulai lagi dari $1.000 dan hanya menghitung posisi yang DIBUKA di run itu.
- Posisi yang dibuka sebelum reset disebut **bawaan**: tetap berjalan sampai kena stop,
  target atau batas waktu, tetapi tidak masuk saldo run sekarang; hasilnya masuk run
  tempat ia dibuka. Contoh: posisi #20 (futures SELL, dibuka 19 Nov) milik run 4.
- Circuit breaker (batas drawdown) dan baris performa di Telegram/terminal masih menghitung
  SEMUA run sejak run 1, belum per run.
- Scanner juga di-reset: keenam buku (Claude murni, Claude + engine, acak; futures dan
  spot) mulai $1.000, dan test scanner (H-SC, H-SC2, H-SCD) dihitung dari 2026-11-21
  00:00 UTC. Posisi scanner dari sebelum itu juga "bawaan".
- Perbaikan data 2026-11-20: gold dan VIX dulu sering kosong (akhir pekan dan saat bar
  harian Yahoo berganti), sehingga kondisi gold_vix bernilai 0 di siklus itu; sudah
  diperbaiki. Alarm per jam kini juga memeriksa gold, VIX, DXY, S&P dan L/S.
- Kondisi S/R (support/resistance) dulu TIDAK PERNAH menyala karena cacat desain; sudah
  diperbaiki 2026-11-20 lalu diuji (H-SR-1) dan GAGAL: arahnya tidak konsisten antar
  pasar dan tahun. Sesuai prereg ia dimatikan (sore 2026-11-20), sehingga skor maksimum
  futures 24.25 dan spot 21.75, dan ambang ikut turun. Ia tetap DICATAT tiap siklus sebagai
  sinyal bayangan (kolom `disabled_contributions`), tanpa ikut menilai skor. Ia hanya boleh
  ikut menilai lagi lewat hipotesis baru dengan prereg sendiri, bukan uji ulang. Versi S/R
  ala trader (zona dari 4H/1D, disentuh minimal 2 kali, candle penolakan; `sr_zone`, H-SR2)
  juga diuji 2026-11-20 dan GAGAL (4 dari 10 sel), jadi S/R sebagai poin skor ditutup. S/R tetap
  dipakai bot di tempat lain: batas TP di resistance/support, gerbang `sr_first` dan gerbang
  pyramid.
- Tanggal penilaian tiap test (H-VG, H-VR/H-BS, H-DR, H-SC, H-SC2) bisa bergeser bila bot
  di-restart dengan aturan baru. Jangan menyebut tanggal dari ingatan: arahkan ke papan
  "Kapan hasilnya bisa dipercaya?" di dashboard.

## Run 3 dan agen (riwayat)
- Paper run berjalan dengan versi kode yang dipatok. Restart dicatat di PAPER_RUN.md.
- Agen shadow (Claude; DeepSeek ikut sampai 2026-10-15 lalu dimatikan pemilik) memberi opini pada setiap sinyal dan posisi, tetapi
  TIDAK PERNAH memutuskan trade. Hipotesis run 3 (H-L data live, H-V varian, H-B,
  H-S shadow entry, H-X shadow exit) dinilai setelah hari ke-30, 2026-11-08. Pengecualian
  sejak perubahan futures 2026-10-15: H-V1, H-V2 futures, H-S dan H-X dihitung ulang dari
  tanggal deploy itu dan dinilai 30 hari setelahnya.
- Bot kedua `nakhoda-alloc` dulu ikut di VPS ini; dihapus 2026-11-11. Sekarang hanya
  SpotSignal (plus scanner, dashboard dan bot Q&A).

## Cara membaca FAKTA
- `recent_cycles.<mode>`: 24 siklus terakhir, urut lama → baru. `type` HOLD/BUY/SELL,
  `strength` vs `threshold`, `buy`/`sell` = skor tiap sisi, `price`, `veto` = baris ⛔.
  Siklus terakhir punya `reasons`: daftar alasan dari mesin untuk siklus itu.
  Gunakan ini untuk menjawab "kenapa HOLD / kenapa skornya segini".
- `gap` (bila ada) = threshold − strength; positif berarti kurang sebanyak itu untuk fire.
- `blocks_by_gate.24h/7d`: jumlah sinyal yang menyala tetapi diblok, per mode dan gerbang.
- `recent_blocks`: blok gerbang 24 jam terakhir SATU PER SATU, urut lama → baru (tanpa
  stale_cache). Tiap baris: `ts`, `mode`, `gate`, `strength`, `threshold` dan `price` dari
  siklus jam itu, `ratio` = strength/threshold (≥ 1.2 = NORMAL), `confidence`, dan
  `detail` = teks penolakan dari kode. Pakai ini untuk menjawab "sinyal jam X ditolak
  gerbang apa". Satu sinyal hanya diblok oleh gerbang PERTAMA yang gagal; gerbang
  sesudahnya tidak dicek, jadi jangan menyimpulkan sinyal itu lolos gerbang lain.
- `open_positions` / `closed_positions`: posisi paper. `pnl_pct` dalam persen.
- `anomalies`: masalah kesehatan yang sudah dideteksi kode.
- Backup database jalan tiap hari 03:00 UTC (laporan harian 03:30 UTC). Antara 00:00 dan
  03:00 UTC wajar bila backup terbaru masih bertanggal kemarin. Semua waktu di FAKTA
  adalah UTC (`now_utc`), bukan WIB; "hari ini" berarti hari UTC.
- `spot_svc`: status service. `last_cycle_age_h`: jam sejak siklus terakhir.

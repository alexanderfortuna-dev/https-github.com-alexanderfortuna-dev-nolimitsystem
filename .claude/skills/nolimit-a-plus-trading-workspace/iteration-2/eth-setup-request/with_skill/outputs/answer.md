**Jawaban singkat: tidak long, tidak short. Saat ini tidak ada setup A+++ di ETH — keputusannya NO TRADE.**

```
STATUS:                 REFERENCE LEVELS ONLY (level acuan, bukan instruksi trade)
PAIR:                   ETHUSDT (Bitget USDT-FUTURES perpetual)
DIRECTION:              Short (satu-satunya trigger yang aktif); Long: tidak ada trigger
STRATEGY:               TRX (breakout 55-bar 4H, stop 2 ATR, trailing 3 ATR)
SETUP SCORE:            56.0 / 100 (ambang 90; ini ranking, bukan probabilitas menang)
VALIDATION STATUS:      REJECTED (TRX sisi short di crypto: +0.043R per trade, dianggap tanpa edge)

ENTRY:                  1579.72 (stop-entry jual, hanya jika harga menembus level ini)
STOP LOSS:              1671.75
LEVERAGE:               setting 7x, isolated; leverage efektif 0.0821x (indikatif: equity belum terverifikasi)
POSITION SIZE:          0.26 ETH, notional 410.73 USDT, margin 58.68 USDT (indikatif: equity belum terverifikasi)
TAKE PROFIT 1:          trailing: 3 ATR(14, 4H) di atas lowest low sejak entry
TAKE PROFIT 2:          none (trailing exit); penanda jarak 3R = 1303.62 (bukan target)

MAXIMUM PLANNED LOSS:   24.65 USDT = 0.493% equity (indikatif: equity belum terverifikasi)
NET REWARD/RISK:        n/a (trailing)
ESTIMATED EXECUTION COST: 0.72 USDT (fee + slippage; funding selama hold belum dihitung)
DATA TIMESTAMP:         2026-11-21 00:01:35 UTC, Bitget API v3, harga 1623.43 (bid 1623.39 / ask 1623.40), status VERIFIED
SETUP EXPIRY:           2026-11-21 04:00 UTC (11:00 WIB)
INVALIDATION:           harga menyentuh 1671.75, atau trigger 1579.72 tidak tersentuh sampai expiry

FINAL DECISION:  NO TRADE
```

**Kenapa NO TRADE**
- **Short:** struktur memang turun di semua timeframe (1W, 1D, 4H, 1H) dan ada trigger objektif di 1579.72. Tetapi catatan uji strategi ini untuk sisi short hanya +0.043R per trade dari 734 trade, statusnya REJECTED. Setup yang tampak bagus tanpa edge terukur bukan A+++. Gate yang gagal: bukti out-of-sample, ekspektasi bersih positif, risiko portofolio (akun belum terverifikasi), dan uji adversarial.
- **Long:** tidak ada trigger long sama sekali; 0 dari 4 timeframe dalam regime naik. Membeli sekarang berarti menebak dasar, dan itu bukan strategi yang teruji.

**Bukti yang melawan short**
- Harga sudah 3.14 ATR di bawah EMA20 harian (zona exhaustion) dan entry masih 2.7% di bawah harga sekarang, jadi entry terlambat dalam tren.
- Funding negatif (-0.0156% per 8 jam, sekitar -17.1%/tahun): short yang membayar, dan posisi short sudah ramai — risiko squeeze.
- Stop berjarak 5.826% dari entry; volatilitas 1H sedang melebar.
- Tidak tersedia: data likuidasi, rasio long/short, taker flow. Kalender makro juga tidak saya periksa (skor 0, bukan diasumsikan aman).

**Yang bisa mengubah keputusan**
1. Trigger long TRX muncul (breakout high 55-bar 4H, saat ini 2084.39). Sisi long punya catatan +0.276R, tetapi statusnya masih RESEARCH_CANDIDATE, jadi maksimal WAIT/paper, belum TRADE CANDIDATE.
2. Ada strategi yang lolos validasi penuh. Saat ini belum ada satu pun strategi berstatus VALIDATED, sehingga sistem ini belum bisa mengeluarkan A+++ untuk pair apa pun.
3. Equity terverifikasi: kirim 4 screenshot Bitget (Assets Unified, Positions, Open Orders, P&L hari ini + 7 hari), dalam USDT, jam terlihat, usia di bawah 5 menit. Angka 5.000 USDT yang Anda sebutkan hanya cukup untuk ukuran indikatif.

Angka ukuran posisi di atas saya tampilkan karena Anda memintanya, bukan sebagai ajakan entry. Tidak ada order yang ditempatkan; skill ini hanya analisis dan paper.

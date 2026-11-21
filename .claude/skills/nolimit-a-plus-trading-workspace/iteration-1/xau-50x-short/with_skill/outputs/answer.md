```
STATUS:                 REFERENCE LEVELS ONLY (bukan instruksi trade)
PAIR:                   XAUUSDT (Bitget USDT-FUTURES perpetual)
DIRECTION:              SHORT
STRATEGY:               TRX (trend runner) - yang diminta adalah market short tanpa trigger
SETUP SCORE:            40.0 / 100 (ranking, bukan probabilitas menang; ambang 90)
VALIDATION STATUS:      RESEARCH_CANDIDATE (hanya crypto; belum ada bukti di gold)

ENTRY:                  3547.55 (bid saat data diambil)
STOP LOSS:              3638.29 (entry + 2 ATR 4H; di atas high hari ini 3632.79)
LEVERAGE:               14x isolated (50x ditolak); effective leverage 0.1774x
POSITION SIZE:          tidak ditampilkan - akun belum terverifikasi
TAKE PROFIT 1:          3456.81
TAKE PROFIT 2:          3275.33

MAXIMUM PLANNED LOSS:   maks 0.50% dari equity terverifikasi
NET REWARD/RISK:        1.838
ESTIMATED EXECUTION COST: 0.61 USDT per 0.10 XAU (fee + slippage)
DATA TIMESTAMP:         2026-11-20 23:55:37 UTC, Bitget API v3, status VERIFIED
SETUP EXPIRY:           sudah lewat - quote hanya berlaku 15 detik untuk level eksekusi
INVALIDATION:           4H close di atas 3638.29

FINAL DECISION:  NO TRADE
```

**Kenapa NO TRADE.** Harga 3.550 yang Anda lihat cocok dengan Bitget (3547.55), jadi datanya valid. Yang gagal adalah 5 dari 7 gate:

- **Entry telat.** Harga sudah 3,86 ATR di bawah EMA20 harian dan 3,32 ATR di bawah EMA20 4H (batas exhaustion 3 ATR). Gold sudah turun dari 3.881 ke 3.508 dalam empat hari; short di sini adalah mengejar harga.
- **Tidak ada edge terukur.** Backtest TRX di XAUUSDT 4H: sisi short 0,00R dari 20 trade, out-of-sample -0,094R dari 11 trade. Tidak ada strategi berstatus VALIDATED di registry.
- **Akun belum terverifikasi.** Equity 2.000 USDT baru angka yang Anda sebut. Posisi terbuka, order pending, dan PnL hari ini/minggu ini tidak diketahui.
- **Breakout terakhir gagal.** Bar 4H terakhir menembus low 55-bar (3520.60) sampai 3508.18 lalu close kembali di 3530.63.
- **Event makro belum dicek** (skor 0), dan volatilitas sedang melebar (ATR14 4H 45,37 vs ATR100 30,18).

**Soal 50x.** Leverage tidak memperbesar ukuran posisi yang diizinkan; ukuran datang dari jarak stop dan risiko 0,50%. Di 50x margin hanya 2% dari notional, jadi likuidasi berada kurang dari 2% dari entry (di bawah kira-kira 71 poin), sedangkan stop yang wajar berjarak 90,74 poin. Artinya posisi terlikuidasi sebelum stop tersentuh, dan range 4 jam saja saat ini 45 poin.

**Yang bisa mengubah keputusan:**
1. Kirim 4 screenshot Bitget (Unified Account, Positions, Open Orders, PnL hari ini + 7 hari), jam HP terlihat, umur di bawah 5 menit. Setelah itu ukuran posisi bisa dihitung.
2. Trigger objektif, misalnya sell-stop di bawah 3508.18 atau retest ke area EMA20 4H (3681), bukan market order di tengah range.
3. Bahkan dengan dua hal itu, hasil terbaik saat ini adalah WAIT / paper, karena strateginya belum tervalidasi di gold.

Skill ini hanya analisis dan paper; tidak ada order yang dikirim.

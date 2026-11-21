Jawaban singkat: **bukan long, bukan short sekarang.** Tidak ada setup A+++ di ETH saat ini, jadi saya tidak memberi instruksi entry.

```
STATUS:                 REFERENCE LEVELS ONLY (bukan instruksi trading)
PAIR:                   ETHUSDT (Bitget USDT-FUTURES perpetual)
DIRECTION:              short (satu-satunya arah yang punya aturan objektif; long tidak punya strategi teruji)
STRATEGY:               TRX - breakout 55-bar 4H, stop 2 ATR, trailing exit 3 ATR
SETUP SCORE:            48 / 100 (ambang A+++ = 90; ini peringkat, bukan probabilitas menang)
VALIDATION STATUS:      RESEARCH_CANDIDATE (belum VALIDATED)

ENTRY:                  1579.72 sell stop (level referensi: low 55-bar 4H)
STOP LOSS:              1671.67 (level referensi: entry + 2 ATR14 4H)
LEVERAGE:               - (setting hanya ditentukan setelah akun terverifikasi; tidak pernah menambah ukuran posisi)
POSITION SIZE:          - (equity 5.000 USDT belum terverifikasi)
TAKE PROFIT 1:          trailing stop 3 ATR(14) 4H dari harga terbaik, diperbarui tiap close 4H
TAKE PROFIT 2:          none (trailing exit)

MAXIMUM PLANNED LOSS:   - (batas sistem: 0,50% dari equity terverifikasi)
NET REWARD/RISK:        - (strategi trailing; ekspektasi terukur sisi short hanya +0.043R per trade)
ESTIMATED EXECUTION COST: sekitar 3% dari jarak stop (taker 0,06% x 2 + slippage), spread 0.062 bps
DATA TIMESTAMP:         2026-11-20 23:57:20 UTC, Bitget API v3, last 1620.84, bid 1620.84 / ask 1620.85, status VERIFIED
SETUP EXPIRY:           2026-11-21 00:00 UTC (trigger hanya berlaku sampai bar 4H ini ditutup)
INVALIDATION:           harga menyentuh 1671.67

FINAL DECISION:  NO TRADE
```

**Kenapa NO TRADE**
- Tidak ada strategi berstatus VALIDATED di registry. TRX masih kandidat riset (1.424 trade, +0.156R), dan hampir semua edge-nya ada di sisi long (+0.276R); sisi short hanya +0.043R, praktis nol setelah biaya.
- Entry short sudah terlambat. ETH turun 7,9% dalam 24 jam; harga berada 3.27 ATR di bawah EMA20 pada 4H dan 3.14 ATR pada 1D (di atas 3 ATR = jenuh). Volatilitas sedang melebar (ATR14 4H 45.98 vs ATR100 39.79).
- Long juga ditolak: 1W, 1D, 4H dan 1H semuanya turun (harga di bawah EMA20/50, 4H dan 1H di bawah EMA200), dan sistem ini tidak punya strategi counter-trend atau mean reversion yang teruji.
- Akun belum terverifikasi. Angka 5.000 USDT dan "tidak ada posisi" baru pernyataan, jadi gerbang risiko portofolio gagal dan ukuran posisi tidak boleh dihitung.

**Bukti yang berlawanan dengan keputusan ini**
- Semua timeframe searah turun dan likuiditas sangat baik (turnover 24 jam 2,42 miliar USDT).
- Backtest TRX khusus ETHUSDT 4H (3.000 bar) positif: 30 trade, +0.57R, short 16 trade +0.71R. Tapi sampelnya terlalu kecil: interval 95% -0.45R sampai +1.95R, p = 0.18. Ini tidak membuktikan edge.

**Konteks lain**
- Funding berikutnya -0,0157% per 8 jam: short yang membayar. Open interest 745.706 ETH (satu snapshot, tanpa data perubahan). Data long/short ratio dan likuidasi tidak tersedia.
- Makro: rilis PCE AS 24-25 Nov (sumber berbeda soal tanggalnya) dan libur Thanksgiving 26 Nov jatuh di dalam jendela holding.
- Data 1W dan 1D dari Bitget hanya 13 dan 90 bar, jadi EMA200 harian/mingguan tidak bisa dihitung; pembacaan regime jangka panjang terbatas.

**Yang bisa mengubah keputusan**
1. Kirim 4 screenshot Bitget (USDT, tidak dipotong, jam terlihat, umur di bawah 5 menit): Assets > Unified Trading Account, Futures > Positions, Futures > Open Orders (termasuk TP/SL), dan P&L hari ini + 7 hari. Dengan itu saya bisa menghitung ukuran posisi dan leverage.
2. Dengan akun terverifikasi pun, hasil terbaik saat ini hanya WAIT, bukan TRADE CANDIDATE, selama TRX belum VALIDATED. Skill ini hanya untuk analisis dan paper trading; tidak ada order live.
3. Setup baru perlu dicek ulang setelah bar 4H berikutnya ditutup, dengan quote baru.

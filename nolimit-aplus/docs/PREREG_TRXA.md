# Pra-registrasi TRX-A (TRX + filter "super align") — ditulis SEBELUM diuji

Tanggal: 2026-11-20. Permintaan owner: sangat selektif (±1 trade/minggu), hanya setup yang selaras penuh.

## Aturan (tetap)
Sinyal dasar = TRX (docs/PREREG_TRX.md), kripto saja, 4H. Sinyal hanya diambil bila SEMUA terpenuhi pada bar sinyal:
1. Momentum 30 hari searah: close vs close 180 bar lalu (long: naik, short: turun).
2. Momentum 90 hari searah: close vs close 540 bar lalu.
3. Rezim pasar searah: tren 4H BTC (EMA50/EMA200 + close vs EMA200) sama dengan arah trade.
4. Kekuatan relatif: return 30 hari instrumen berada di sepertiga TERKUAT (long) / TERLEMAH (short)
   di antara instrumen kripto yang punya data saat itu (minimal 5 instrumen).
Exit, stop, biaya: identik TRX.

## Data dan kriteria lulus
- Data 4H kripto terpanjang yang tersedia (data/candles_ext, hingga ±6 tahun).
- Lulus bila, atas seluruh periode: >= 100 trade, rata-rata >= +0,10R bersih, PF >= 1,25,
  batas bawah CI 95% (bootstrap blok mingguan) > 0, tetap > 0 saat biaya distres,
  dan positif di minimal 2 dari 3 sepertiga waktu.
- Ini hipotesis ke-6 yang diuji (TPB, DBO, TRX, pre-sample TRX, TRX-A): ambang p dikoreksi Holm (p < 0,05/… paling ketat 0,01).
- Hasil apa pun dilaporkan apa adanya; tidak ada penyetelan ulang.

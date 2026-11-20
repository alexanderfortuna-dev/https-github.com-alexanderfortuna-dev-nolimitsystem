# Pra-registrasi hipotesis TRX (Trend Runner) — ditulis SEBELUM diuji

Tanggal: 2026-11-20. Profil yang dipilih owner: jarang trade, kemenangan besar.

## Aturan (tetap, tidak diubah setelah melihat hasil)
- Timeframe: 4H saja.
- Filter tren: EMA50 > EMA200 dan close > EMA200 (tren 4H), DAN close > EMA300 (≈ rata-rata 50 hari). Short: kebalikannya.
- Entry: stop-entry di tertinggi 55 bar (≈ 9 hari) untuk long / terendah 55 bar untuk short, hanya bila close berada dalam 1 ATR dari level itu. Berlaku 1 bar.
- Stop awal: 2 x ATR(14) dari entry.
- Exit: trailing stop chandelier = harga terbaik sejak entry − 3 x ATR(14), diperbarui tiap bar tutup, hanya bergerak searah profit. Tanpa take profit tetap. Tanpa partial.
- Batas waktu: 180 bar (30 hari).
- Satu posisi per simbol, tanpa pyramiding, tanpa averaging down.

## Pengujian
- Data, biaya, split 60/40, dan seluruh gate sama persis dengan `aplus/validation.py`.
- Koreksi Holm dihitung atas SEMUA pool yang pernah diuji (16 lama + pool TRX).
- Hasil apa pun dilaporkan apa adanya. Jika gagal, TRX tidak dipakai dan tidak disetel ulang.

# Bukti pengujian (dijalankan 2026-11-20 22:22:29 UTC)

Semua angka di bawah berasal dari eksekusi nyata di sesi build. Cara mengulang ada di tiap bagian.

## 1. Unit test dan simulasi kegagalan order — 100 lulus, 0 gagal
`python -m pytest tests -v` → log lengkap: `reports/pytest_output.txt`.

| Area | File | Yang dibuktikan |
|---|---|---|
| Keamanan klien | `tests/test_client_security.py` | Signature HMAC; key produksi tidak bisa mengirim POST apa pun (diblokir sebelum jaringan); demo hanya ke allow-list; tidak ada mode `live`; batas risiko hanya bisa diperketat |
| Risk engine | `tests/test_risk.py` | Sizing dari jarak stop ≤ 0,5%; leverage efektif ≠ setelan leverage; batas 1,5% risiko terbuka; 3x gross (posisi lama ikut dihitung); circuit breaker harian/mingguan; data basi/akun tidak lengkap/edge belum valid → tolak |
| Eksekusi | `tests/test_execution.py` | TP1 → stop ke entry → TP2; stop loss; order ganda; timeout setelah order diterima (tidak dikirim ulang); order ditolak; stop hilang → flatten; kill switch; mismatch posisi; posisi eksternal tidak disentuh |
| Backtester & validasi | `tests/test_backtest_validation.py` | Asumsi pesimis (stop dulu, gap, entry bar); biaya; funding; tanpa look-ahead; edge asli lolos, noise ditolak |
| Audit, DB, demo, API | `tests/test_audit_store_demo.py` | Tiap buku dihitung sekali; key non-read-only ditolak; constraint DB; bentuk request demo; API butuh token |

## 2. Uji historis (backtest + out-of-sample)
`python -m aplus sync-data && python -m aplus validate` → `reports/validation_summary.json`, `validation_symbols.csv`, `backtest_trades.csv.gz`.

- Data: candle Bitget `USDT-FUTURES`, 41 instrumen, 4H ≈ 3 tahun dan 1H ≈ 1 tahun (lebih pendek untuk instrumen baru).
- 16699 trade simulasi. Dua kali jalan berturut-turut menghasilkan jumlah trade yang sama (deterministik).
- **Hasil: 0 dari 16 pool lolos. 0 simbol lolos. Tidak ada A+++ yang sah saat ini.**

| Strategi | TF | Kelas aset | Simbol | Trade OOS | Rata-rata OOS (R) | PF | CI95 bawah | Stres biaya | Status |
|---|---|---|---|---|---|---|---|---|---|
| DBO | 1H | commodity | 6 | 195 | -0.452 | 0.47 | -0.816 | -0.909 | REJECTED |
| DBO | 1H | crypto_alt | 13 | 585 | +0.065 | 1.14 | -0.061 | +0.003 | REJECTED |
| DBO | 1H | crypto_major | 2 | 86 | +0.099 | 1.21 | -0.172 | +0.027 | REJECTED |
| DBO | 1H | equity | 20 | 651 | -0.074 | 0.87 | -0.255 | -0.263 | REJECTED |
| DBO | 4H | commodity | 6 | 122 | +0.042 | 1.08 | -0.254 | -0.096 | REJECTED |
| DBO | 4H | crypto_alt | 11 | 363 | -0.010 | 0.98 | -0.198 | -0.036 | REJECTED |
| DBO | 4H | crypto_major | 2 | 79 | -0.057 | 0.90 | -0.322 | -0.083 | REJECTED |
| DBO | 4H | equity | 16 | 187 | -0.123 | 0.79 | -0.417 | -0.191 | REJECTED |
| TPB | 1H | commodity | 6 | 521 | -0.389 | 0.50 | -0.540 | -0.714 | REJECTED |
| TPB | 1H | crypto_alt | 13 | 1256 | -0.095 | 0.83 | -0.203 | -0.167 | REJECTED |
| TPB | 1H | crypto_major | 2 | 213 | -0.087 | 0.84 | -0.220 | -0.162 | REJECTED |
| TPB | 1H | equity | 20 | 1365 | -0.274 | 0.61 | -0.401 | -0.464 | REJECTED |
| TPB | 4H | commodity | 6 | 210 | +0.024 | 1.05 | -0.160 | -0.117 | REJECTED |
| TPB | 4H | crypto_alt | 11 | 689 | +0.005 | 1.01 | -0.115 | -0.026 | REJECTED |
| TPB | 4H | crypto_major | 2 | 165 | +0.008 | 1.02 | -0.160 | -0.026 | REJECTED |
| TPB | 4H | equity | 16 | 335 | -0.098 | 0.82 | -0.316 | -0.184 | REJECTED |

Dilewati karena histori < 1000 bar: KORUUSDT:4H, SNXXUSDT:4H, SKHYUSDT:4H, SPCXUSDT:4H, AKEUSDT:4H, EVAAUSDT:4H.

## 3. Forward test
- Aturan dan gate dibekukan pada `frozen_at_ms = 1795213268847` (`reports/validation_freeze.json`). Hanya sinyal setelah titik ini yang dihitung sebagai bukti forward.
- Status: **baru dimulai, 0 trade forward.** Butuh ≥ 20 trade forward dengan rata-rata > 0R, dan itu hanya berlaku untuk pool yang lolos uji historis.
- Scan live + engine paper dijalankan terhadap harga Bitget nyata: sinyal teknikal muncul, semuanya ditolak (`edge_not_validated`), 0 order paper terkirim. Lihat `reports/dashboard_snapshot.html`.

## 4. Database
`supabase/migrations/0001_aplus_init.sql` dijalankan di PostgreSQL 16 lokal: sukses, aman dijalankan ulang, RLS aktif di 10 tabel, `mode='live'` ditolak, order exit non-reduce-only ditolak, role `anon` tidak bisa membaca.

## 5. Yang BELUM diuji (jujur)
| Belum diuji | Sebab | Cara menutup |
|---|---|---|
| Audit akun nyata (equity, posisi, bot, CFD) | Belum ada key read-only | Isi `BITGET_READ_API_*`, jalankan `python -m aplus audit` |
| Order ke Bitget Demo Trading | Belum ada key demo | Isi `BITGET_DEMO_API_*`, jalankan `python -m aplus demo-smoke`, lalu `APLUS_EXECUTION_MODE=demo` |
| Workflow n8n | File JSON valid, belum diimpor ke n8n | Impor 3 file di `n8n/`, pasang credential |
| Backend Supabase (PostgREST) | Skema teruji di Postgres; klien REST belum dijalankan ke proyek nyata | Terapkan migrasi, set `APLUS_STORE=supabase` |
| Indikator Pine | Belum dikompilasi di TradingView | Tempel ke Pine Editor |
| Path endpoint privat | Hanya dipastikan ADA (server menjawab error autentikasi, bukan 404); isi respons mengikuti dokumentasi | Tertutup oleh dua baris pertama |

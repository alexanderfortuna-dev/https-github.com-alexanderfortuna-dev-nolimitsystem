# NO LIMIT A+++ — Bitget trading intelligence

Sistem untuk akun Bitget yang **sudah ada**: audit akun read-only, scanner multi-aset, validasi statistik, risk engine, eksekusi **paper / Bitget Demo Trading**, dan dashboard owner.

> Tanpa data terverifikasi = tanpa trade. Tanpa edge tervalidasi = tanpa A+++. Tanpa kontrol risiko = tanpa eksekusi.
> **Order produksi tidak diimplementasikan di kode ini.** Sistem tidak pernah mengubah posisi atau bot yang sudah ada.

## Status saat ini (baca dulu)

| Hal | Status |
|---|---|
| Strategi tervalidasi | **0 dari 16 pool lolos** uji out-of-sample → **tidak ada A+++ saat ini** (`reports/TEST_EVIDENCE.md`) |
| Test otomatis | 100 lulus (`reports/pytest_output.txt`) |
| Data pasar Bitget | Terhubung dan terverifikasi (API publik v3) |
| Audit akun nyata | Menunggu key read-only dari Anda |
| Eksekusi demo Bitget | Menunggu key demo dari Anda; paper lokal sudah jalan |

Analogi: sistem ini seperti kasir yang menolak transaksi kalau barcode tidak terbaca. Dua strategi awal (TPB, DBO) setelah biaya rata-rata rugi ±0,12R per trade, jadi "kasir" menolak semuanya. Itu perilaku yang benar, bukan kerusakan.

## Pemasangan

```bash
cd nolimit-aplus
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # isi sesuai langkah di bawah
python -m pytest tests        # harus 100 passed
python -m aplus status
```

### 1. Key Bitget (dua key terpisah, tanpa izin withdraw)

| Key | Dibuat di | Izin | Dipakai untuk |
|---|---|---|---|
| READ | Akun utama → API Management | **Read-only saja**, ikat ke IP server | `aplus audit` |
| DEMO | Ganti ke mode Demo → API Key Management | Trade (demo) | order demo, header `paptrading: 1` |

Engine menolak audit jika key READ ternyata bisa trade atau withdraw. Key hanya dibaca proses Python dari `.env`; tidak masuk database, log, n8n, atau LLM. Jangan tempel key di chat.

### 2. Database
- Lokal (default): SQLite di `var/aplus.db`, tanpa setup.
- Supabase: jalankan `supabase/migrations/0001_aplus_init.sql`, tambahkan schema `aplus` ke *Exposed schemas* (Settings → API), lalu isi `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `APLUS_STORE=supabase`. RLS aktif tanpa policy untuk anon: hanya service role di server yang bisa akses.

### 3. Menjalankan

```bash
python -m aplus sync-data        # unduh histori (±6 menit)
python -m aplus validate         # backtest + validasi → reports/
python -m aplus audit            # audit akun produksi (butuh key READ)
python -m aplus scan --execute   # scan sekali; kandidat disetujui → paper/demo
python -m aplus run              # loop: scan tiap 15 menit, tick tiap 5 detik
python -m aplus dashboard        # tulis var/dashboard.html
python -m aplus kill "alasan" [--flatten]   # tombol darurat;  aplus unkill untuk melepas
python -m aplus demo-smoke       # cek baca ke API demo
```

### 4. n8n + TradingView
1. `APLUS_API_TOKEN` = string acak ≥ 24 karakter, lalu `python -m aplus serve` (bind `127.0.0.1:8787`).
2. Impor `n8n/*.json`. Buat credential *Header Auth*: `Authorization` = `Bearer <token>`. Sambungkan node *Notify/alert* ke Telegram/WhatsApp Anda.
3. n8n harus bisa menjangkau engine (satu server, atau lewat tunnel privat). Jangan buka port 8787 ke internet.
4. TradingView: tempel `tradingview/nolimit_aplus.pine`, buat alert ke webhook n8n `/webhook/aplus-tradingview`. Alert hanya dicatat sebagai konteks; **tidak bisa membuat trade**.

## Cara kerja

```
Bitget API ──► data.py (candle tertutup, cek kualitas & kesegaran)
                 │
   strategies.py (TPB, DBO) ──► backtest.py ──► validation.py ──► reports/
                 │                                      │ status per simbol
           scanner.py ──► risk.py (sizing, batas, breaker) ──► kandidat + bukti
                                         │ hanya jika disetujui
                          execution/engine.py ──► PaperBroker | DemoBroker
                                         │
                     store.py (SQLite | Supabase) ──► dashboard / server.py ◄── n8n
```

**Audit tanpa hitung ganda.** Total = Unified (`accountEquity`, sudah berisi spot + margin futures + P&L berjalan) + Funding + akun Bot + akun CFD. Angka "Overview" di aplikasi adalah jumlah itu sendiri, jadi hanya dipakai sebagai cek silang (toleransi 3%). Jika satu buku gagal dibaca, audit ditandai tidak lengkap.

**Kualifikasi A+++** (`aplus/validation.py`; dokumen kerangka validasi Anda sebelumnya tidak ada di repo, jadi ambang di `GATES` adalah usulan konservatif yang bisa Anda ganti):
split kronologis 60/40 · ≥ 100 trade OOS per pool · rata-rata ≥ +0,10R bersih · PF ≥ 1,25 · batas bawah CI 95% (bootstrap blok mingguan) > 0 · tetap positif saat biaya distres · ≥ 60% kuartal positif · koreksi Holm antar-pool · lalu ≥ 20 trade forward positif. Lolos historis saja = "A+ (paper only)".

**Risk engine** (`aplus/risk.py`): 0,5% risiko per trade · 1,5% total risiko terbuka · breaker −1,5% harian / −4% mingguan · gross exposure ≤ 3x termasuk posisi Anda yang sudah ada · isolated margin · tanpa averaging down · tidak entry di simbol yang sudah ada posisinya. Batas di `.env` hanya bisa diperketat.

*Leverage efektif vs setelan leverage.* Contoh: equity 10.000 USDT, entry 60.000, stop 59.400 (1%). Risiko 0,5% = 50 USDT → posisi ±4.400 USDT → **leverage efektif 0,44x**. Setelan di bursa bisa 20x; itu hanya menentukan margin yang dikunci (±220 USDT) dan letak likuidasi, yang dijaga minimal 2× jarak stop.

**Eksekusi** (`aplus/execution/`): trigger entry di sisi engine dengan harga segar → market order yang **membawa stop loss** (tidak ada posisi tanpa stop) → TP1 50% di 1R dan TP2 di 3R sebagai limit reduce-only → setelah TP1 stop pindah ke entry. `clientOid` deterministik + baris order ditulis sebelum request, sehingga timeout direkonsiliasi, bukan dikirim ulang.

## Cakupan produk

| Produk | Scan | Eksekusi paper/demo | Catatan |
|---|---|---|---|
| Crypto perp, XAU/XAG, energi, saham & ETF perp (USDT-M) | Ya | Ya | Satu API (`USDT-FUTURES`); tiap kelas divalidasi terpisah |
| Index / FX perp | Tidak | Tidak | Turnover 24 jam di bawah 20 juta USD |
| Spot | Audit saja | Tidak | |
| TradFi CFD | Audit saja | Tidak | API `/api/v3/cfd/*` terpisah; tanpa `clientOid`, demo tidak terdokumentasi |
| Grid bot | Audit saja | Tidak pernah diubah | Saldo akun bot terbaca; hasil per bot butuh `APLUS_GRID_BOT_IDS=id1,id2` |

## Keterbatasan yang perlu Anda tahu
- Universe dipilih dari likuiditas hari ini (bias survivorship): hasil backtest cenderung terlalu bagus, bukan terlalu jelek.
- Riwayat funding dari API hanya ±90 hari; sebelum itu dipakai asumsi pesimis 0,01%/8 jam melawan posisi.
- Saham/komoditas perp baru terdaftar < 1 tahun: sampel pendek.
- Simulator paper memakai harga terakhir hasil polling, bukan order book.
- Untuk mengaktifkan trading produksi dibutuhkan perubahan kode yang disengaja. Tidak ada saklar konfigurasi untuk itu.

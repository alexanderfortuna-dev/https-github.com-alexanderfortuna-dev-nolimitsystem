# nolimit-a-plus-trading — instalasi dan laporan validasi

Paket: `nolimit-a-plus-trading.skill` (isi sama dengan `nolimit-a-plus-trading.zip`). Sumber: `.claude/skills/nolimit-a-plus-trading/`.

## Instalasi

**Claude (Max / claude.ai / desktop):** Settings → Capabilities → Skills → Upload skill → pilih `nolimit-a-plus-trading.zip`. Code execution harus aktif. Catatan: sandbox claude.ai umumnya tidak punya akses jaringan ke `api.bitget.com`; di sana skill bekerja dari screenshot dan data konektor, bukan dari `--fetch-bitget`.

**Claude Code (proyek ini):** sudah ada di `.claude/skills/nolimit-a-plus-trading/`; terbaca otomatis pada sesi berikutnya di repo ini. Untuk semua proyek: salin folder itu ke `~/.claude/skills/`.

**Aktivasi:** tulis permintaan biasa, mis. "cari setup ETH", "berapa leverage dan ukuran posisi untuk long BTC", atau panggil `/nolimit-a-plus-trading` di Claude Code.

**Cek mandiri setelah pasang:** `python scripts/validate_skill.py` dari folder skill (harus 22/22).

## Konektor dan izin

| Konektor | Wajib | Izin | Status di sesi build |
|---|---|---|---|
| API publik Bitget (`api.bitget.com`) | Ya untuk level eksekusi | Tanpa key | Terverifikasi berjalan |
| Key Bitget read-only | Untuk audit akun otomatis | Read saja, tanpa trade/withdraw, terikat IP | Belum ada key |
| TradingView MCP (`https://mcp.tradingview.com/mcp`) | Opsional (cek silang, chart) | Login TradingView oleh pemilik | Ditambahkan ke `.mcp.json`; belum tersambung/teruji |
| CoinGlass atau sejenis | Opsional (likuidasi, taker flow, long/short) | Read | Tidak ada konektor |
| Kalender ekonomi / berita | Opsional (risiko event) | Read | Tidak ada konektor |

TradingView di claude.ai: Settings → Connectors → Add custom connector → tempel URL di atas → login. Di Claude Code: setujui server `tradingview` dari `.mcp.json` saat sesi baru dimulai, lalu `/mcp` untuk login.

## Laporan validasi (20–21 Nov 2026)

| # | Pemeriksaan | Hasil |
|---|---|---|
| 1 | Struktur skill (26 file) | LULUS — `validate_skill.py` dan packager resmi skill-creator |
| 2 | YAML frontmatter | LULUS — setelah satu bug nyata diperbaiki (titik dua di deskripsi); ada test regresi |
| 3 | Sintaks script + unit test | LULUS — 37/37 |
| 4 | Perhitungan vs contoh manual | LULUS — sizing BTC cocok sampai sen; backtester mandiri = mesin repo (86 trade, +0,0598R) |
| 5 | Data hilang | LULUS — quote kosong, tanpa timestamp, error sumber, spesifikasi kontrak hilang → INSUFFICIENT DATA |
| 6 | Kontrak salah | LULUS — XAUUSD.s (CFD) vs XAUUSDT (perp), spesifikasi simbol lain, venue lain → ditolak |
| 7 | Slippage dan likuidasi | LULUS — slippage memperkecil ukuran; likuidasi ≥ 2× jarak stop; leverage 100x diminta → tetap 20x, ukuran sama |
| 8 | Sinyal bertentangan | LULUS — konflik timeframe / sumber berbeda > 0,5% → WAIT / CONFLICT |
| 9 | Skenario no-trade | LULUS — strategi ditolak/tak dikenal, bukti di luar kelas aset atau arah, breaker, kill switch |
| 10 | API/konektor hilang | LULUS — kegagalan fetch → INSUFFICIENT DATA, exit code 2 |
| 11 | Data live | LULUS — `validate_market_data.py --fetch-bitget` dan `analyze_market.py` pada BTC/ETH/XAU |
| 12 | Dipakai agen Claude lain | 2 uji iterasi-1 (ETH; "short XAU 50x"): keduanya NO TRADE, 50x ditolak, ukuran tidak dikarang. Temuan mereka (tak ada script analisis; aturan equity tak konsisten; target tetap untuk strategi trailing) sudah diperbaiki |

**Tidak bisa diuji di sini:** pemasangan di claude.ai (butuh akun pemilik); penemuan otomatis skill oleh sesi Claude Code baru; TradingView MCP; audit akun dengan key nyata; sumber derivatif dan makro pihak ketiga; perbandingan dengan/ tanpa skill (baseline) tidak dijalankan.

## Keterbatasan yang diketahui
- Registry tidak memuat strategi berstatus VALIDATED, jadi skill saat ini tidak bisa mengeluarkan TRADE CANDIDATE. Itu disengaja.
- Retest breakout, failed-breakout reversal, dan mean reversion belum punya definisi teruji: hanya bisa diriset, tidak direkomendasikan.
- Skor derivatif dibatasi 5/10 tanpa sumber posisi; skor makro 0 sampai diisi dari kalender bersumber.
- Kontrak baru (emas, saham perp) punya histori pendek; script mengumumkannya dan memakai uji yang lebih lemah.
- Tidak ada eksekusi live. Eksekusi paper/demo ada di repo `nolimit-aplus`, bukan di skill.
- Batas risiko adalah nilai operasi sementara, bukan nilai optimal yang terbukti.

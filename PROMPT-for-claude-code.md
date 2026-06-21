# Prompt untuk Claude Code: Gridora (lanjutan)

Salin semuanya di bawah ini ke Claude Code.

---

Kamu melanjutkan project **Gridora** (folder ini). Baca `CLAUDE.md`, `Gridora-Plan.md`,
dan seluruh `backend/`, `contracts/`, `frontend/` sebelum mengubah apa pun. Jangan buat
ulang yang sudah ada, project ini hasil porting dari dua repo kami:
`/Users/kiel/Documents/Hacathon/perps-agent` dan `/Users/kiel/Documents/Hacathon/BridgeAgent`.

## Kejelasan goal (PENTING, ada koreksi arsitektur)

Gridora = agen grid-trading adaptif, non-custodial, verifiable, untuk **BNB Hack: AI
Trading Agent Edition** (Track 1 + special prize *Best Use of Trust Wallet Agent Kit*).

Fakta kompetisi yang harus jadi pegangan (jangan menyimpang):
- **Eksekusi = ON-CHAIN di DEX BSC, venue resmi PancakeSwap.** Bukan CEX. Bukan iZiSwap.
- Trading = swap antar **149 token BEP-20** yang ada di allowlist. Di luar itu tidak dihitung.
- **TWAK adalah satu-satunya execution layer.** Self-custody: private key tidak pernah
  keluar dari TWAK. Setiap aksi on-chain ditandatangani lokal oleh TWAK.
- **TWAK secara native menyediakan swap, DCA, dan LIMIT ORDER di BSC, plus guardrails
  (token allowlist, per-trade/daily limit, slippage) dan native x402.** Manfaatkan ini.
- Skoring: PnL riil per jam; **drawdown 30% = DQ**; **min 1 trade/hari**; pegang aset
  in-scope ≠ 0 di awal; portofolio ≤ $1 satu jam = 0% jam itu; **ada simulated tx cost**.
- Register on-chain sebelum **22 Juni** ke kontrak kompetisi
  `0x212c61b9b72c95d95bf29cf032f5e5635629aed5` via `twak compete register` /
  MCP `competition_register`.

## Langkah 0: Audit dulu, lapor sebelum ngoding besar

1. Jalankan `cd backend && PYTHONPATH=src python -m pytest -q` dan
   `PYTHONPATH=src python -m gridora.runner --mode dry --market CAKE/USDT`. Laporkan hasil.
2. Baca `adapters/exchanges/bsc_twak/{adapter.py,twak_client.py,bsc_tokens.py}`,
   `adapters/signals/cmc.py`, `adapters/payments/x402.py`, `app/service.py`, `agent/loop.py`,
   `app/engine.py`, `app/safety.py`.
3. Tulis ringkasan singkat: apa yang sudah real, apa yang masih stub/placeholder, dan apa
   yang akan kamu ubah sesuai daftar di bawah. Baru lanjut.

## Yang harus DIGANTI / DIPERBAIKI

1. **Eksekusi lewat action native TWAK, bukan hand-roll PancakeSwap.**
   Sekarang `bsc_twak/adapter.py` menyusun calldata router PancakeSwap V2 sendiri dan TWAK
   hanya dipakai untuk `sign_and_send`. Ubah supaya eksekusi memakai **action native TWAK**:
   - Tambah method di `TwakClient`: `swap(...)` dan `place_limit_order(...)` /
     `cancel_limit_order(...)` yang memanggil aksi TWAK (MCP/REST/CLI). **Verifikasi nama
     aksi & parameter persisnya dari dokumentasi TWAK** (portal.trustwallet.com). Jangan
     menebak; kalau belum yakin, beri TODO jelas + tipe yang benar.
   - `BscTwakExchange.place_order()` pakai **limit order TWAK** (maker grid 1:1 ke level).
     Ini lebih baik dari synthetic-swap dan menambah kedalaman integrasi TWAK.
   - Pertahankan jalur **swap PancakeSwap (router) sebagai fallback** saja, di belakang flag,
     untuk pair yang tidak punya limit order. Jangan dihapus, tapi bukan jalur utama.
   - Slippage, allowlist, per-trade/daily limit: serahkan ke guardrails native TWAK bila
     tersedia, dan tetap cek lokal (defense in depth).

2. **Turunkan iZiSwap jadi opsional / bersihkan dead code.**
   Hapus ketergantungan pada `IZISWAP_LIMIT_ORDER_MANAGER` di jalur utama. Boleh simpan satu
   komentar "opsi alternatif limit-order DEX" tapi jangan ada konstanta kosong yang dipakai
   import. Pastikan tidak ada referензi iZiSwap yang bikin bingung di `Gridora-Plan.md`,
   `CLAUDE.md`, dan docstring. Venue resmi adalah **PancakeSwap (atau limit order native TWAK)**.

3. **Grid harus sadar biaya (fee-aware), kalau belum ada, tambahkan.**
   Karena ada simulated tx cost + fee PancakeSwap (V2 = 0.25%; V3 stable bisa 0.01–0.05%),
   tiap pasangan buy/sell harus menutup biaya round-trip. Tambahkan guard di `app/service.py`
   (`config_for_preset`) atau `domain/grid.py`: **tolak/lebarkan grid jika jarak antar-level
   < (2×fee + slippage_bps + estimasi gas)**. Buat `min_spacing_bps` per preset. Pertimbangkan
   PancakeSwap **V3** untuk pair stable demi fee lebih kecil (boleh sebagai konfigurasi).

## Yang masih KURANG (lengkapi)

4. **Allowlist 149 token lengkap.** `adapters/signals/allowlist.py` masih subset. Lengkapi
   ke 149 token dari brief. `bsc_tokens.py` lengkapi address+decimals untuk token yang
   realistis kita tradекан (minimal semua stable + 15–20 alt likuid). **Verifikasi tiap
   address dari sumber resmi (BscScan/PancakeSwap)**. Salah address = kehilangan dana.
5. **Address testnet (chainId 97).** Sediakan tabel token & router PancakeSwap untuk testnet,
   karena default kita testnet. Pastikan `config.guard()` menolak mismatch env↔chain.
6. **TwakClient real.** Implementasikan transport ke TWAK (pilih satu: MCP / REST / CLI),
   termasuk `address()`, `sign_and_send()`, `pay_402()`, `compete_register()`, dan method
   swap/limit-order baru di poin 1. Tetap sediakan mode dry (fakes) agar test offline jalan.
7. **CMC SignalPort real.** Lengkapi `adapters/signals/cmc.py`: endpoint funding & momentum
   (bukan hanya Fear & Greed), semua dibayar via x402-through-TWAK di dalam loop.
8. **preflight.py.** Implement cek: TWAK reachable, 1 panggilan x402 CMC sukses, RPC BSC live,
   address kontrak terisi, allowlist termuat. Harus semua PASS sebelum live.
9. **Contracts deploy ke BSC testnet.** `forge test` harus hijau; sediakan instruksi/skrip
   deploy dan isi address ke `backend/.env` + `frontend/web/.env`.
10. **Frontend verifier** baca address kontrak dari env; pastikan `pnpm build` sukses.

## Batasan (jangan dilanggar)

- Jaga **seam hexagonal**: engine & agent TIDAK boleh import adapter konkret atau SDK
  chain/wallet. UI hanya lewat `GridService`.
- **Testnet default.** Untuk aksi mainnet / uang sungguhan: berhenti dan tanya dulu.
- Jangan pernah commit/print private key. `.secrets/` dan `.env` gitignored.
- Pertahankan **semua test hijau**; tambah test untuk tiap perubahan (limit-order path,
  fee-aware spacing, allowlist 149). Optimasi target **risk-adjusted**, bukan PnL mentah.

## Urutan kerja yang diminta

1. Audit + ringkasan (Langkah 0).
2. Refactor eksekusi ke limit-order/swap native TWAK + bersihkan iZiSwap (poin 1–2).
3. Fee-aware grid + preset (poin 3).
4. Allowlist 149 + address (testnet & mainnet) + TwakClient + CMC + preflight (poin 4–8).
5. Contracts deploy + frontend env (poin 9–10).
6. Update `Gridora-Plan.md` & `CLAUDE.md` agar konsisten (PancakeSwap/TWAK-native, bukan iZiSwap).
7. Jalankan `pytest` + dry-run, laporkan, dan tulis daftar langkah live (fund wallet, register).

Kerjakan bertahap, commit kecil-kecil yang hijau. Mulai dari Langkah 0 sekarang.

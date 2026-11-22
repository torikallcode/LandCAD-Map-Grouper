# LandCAD Map Grouper

Aplikasi desktop **lokal/offline** untuk mengelompokkan dokumen Surat Ukur (SU) PDF
berdasarkan kemiripan peta dasar.

> Status: **Phase 9 — Perbaikan Akurasi Pengelompokan** selesai.
> Peta berbeda tidak lagi digabung hanya karena berbagi bingkai halaman.

## Fitur Phase 1 (PDF Importer & Viewer)

- Membuka aplikasi desktop (PySide6, bahasa Indonesia).
- Memilih satu folder berisi PDF.
- Memindai folder dan menampilkan daftar seluruh PDF (urut alami: `SU_2` sebelum `SU_10`).
- Validasi setiap file dengan status jelas: **Valid**, **Rusak**, **Terkunci**, **Tanpa halaman**,
  **Tidak ditemukan**, **Tidak dapat dibaca**.
- Memilih dokumen dan melihat pratinjau halaman PDF.
- Navigasi halaman: pertama / sebelumnya / nomor halaman / berikutnya / terakhir.
- Pratinjau: sesuaikan lebar, zoom in/out (`Ctrl` + scroll), ukuran asli.
- Ringkasan dashboard: total PDF, berhasil dibaca, gagal dibaca, total halaman, status proses.
- Pencarian cepat berdasarkan nama file atau status.
- Mengeluarkan file dari daftar **tanpa menghapus file asli**.

## Fitur Phase 2 (Map Detection & Normalization)

- **Deteksi halaman peta berbasis citra**, bukan aturan "halaman 2". Setiap halaman
  dinilai dengan sinyal: kepadatan teks, rasio tinta, luas area kosong, kekuatan garis,
  dan proporsi garis diagonal. Ambang dan bobot dapat dikonfigurasi.
- **Pratinjau per halaman dengan skor** langsung di toolbar (mis. `Halaman 2 dari 2 • skor 0.90`).
- **Normalisasi halaman peta**: grayscale → CLAHE (kontras) → median denoise →
  koreksi orientasi 90° → deskew → resize seragam (sisi terpanjang 1600 px).
- **Mode tampilan Asli / Normalisasi** di panel pratinjau untuk membandingkan
  halaman asli dengan hasil normalisasi.
- **Kolom baru** di daftar dokumen: *Halaman Peta* dan *Skor*.
- **Kartu dashboard** "Halaman Peta Ditemukan" (mis. `2/2`).
- Gambar asli dan gambar ternormalisasi **disimpan terpisah** (`save_normalized`).
- Analisis berjalan di background thread dengan progress bar; UI tidak membeku.

Ekstraksi fitur (ORB), alignment, similarity, dan clustering **belum** ada di fase ini.

## Fitur Phase 3 (Feature Extraction & Alignment)

- **Ekstraksi fitur ORB** dari halaman peta ternormalisasi (CLAHE → blur → ORB).
- **Cache fitur di disk** (`data/cache/features/`), kunci = hash gambar + versi pipeline,
  sehingga gambar yang sama tidak dihitung ulang.
- **Tab “Perbandingan Peta”** untuk membandingkan dua dokumen berdampingan.
- **Feature matching**: BFMatcher Hamming + Lowe ratio test 0.75.
- **Estimasi transformasi**: affine (RANSAC) sebagai dasar; homografi hanya dipakai
  bila jumlah kecocokan cukup dan matriksnya stabil.
- **Overlay hasil alignment**: biru = peta A, merah = peta B setelah diselaraskan,
  gelap = tinta yang berimpit.
- **Mode Overlay / Berdampingan** dan tombol **Tukar** (A ↔ B).
- **Ringkasan metrik**: model transformasi, jumlah kecocokan, inlier, rasio inlier,
  galat reproyeksi, kualitas alignment, dan tumpang tindih tinta.

Hasil pada dokumen referensi (SU_057 vs SU_058):

| Metrik | Nilai |
| --- | --- |
| Model | Homografi (stabil) |
| Kecocokan fitur | 499 |
| Inlier | 363 (72.7%) |
| Galat reproyeksi | 1.33 px |
| Kualitas alignment | 0.877 (Tinggi) |
| Tumpang tindih tinta | 0.805 |

Overlay menunjukkan jalan dan batas bidang sekitar **berimpit**, sementara kotak
bidang utama (274 m² vs 244 m²) tetap terpisah — yaitu perilaku yang diminta PRD.

Similarity scoring dan clustering **belum** ada di fase ini.

## Fitur Phase 4 (Main Parcel Detection)

- **Deteksi bidang utama** sebagai poligon tertutup yang garisnya **konsisten gelap**,
  bukan sekadar garis tebal. Pengukuran pada dokumen referensi menunjukkan lebar
  stroke bidang utama (2.8 px) **tidak** lebih tebal dari tinta peta dasar
  (2.0–4.0 px), tetapi 94–100% piksel tepinya ≤150 grey, sementara peta dasar hanya
  22–34%. Jadi ambang kegelapan itulah pemisah yang sebenarnya.
- **Masking hanya garis bidang**, interior dibiarkan agar label bidang tetap utuh dan
  peta dasar di sekitarnya tidak ikut terhapus.
- **Toggle “Abaikan Bidang Utama”** di tab Perbandingan; perubahan memicu perbandingan
  ulang.
- **Empat mode tampilan**: Overlay, Berdampingan, Overlay tanpa masking, dan
  Bidang utama (menandai poligon hasil deteksi).
- **Skor dilaporkan dua arah** — dengan dan tanpa masking — supaya dampaknya terlihat.
- **Penandaan pemeriksaan manual** bila keyakinan deteksi rendah.

### Penting: arah pengaruh masking

Masking bukan berarti skor selalu naik. Tanda selisih bergantung pada posisi bidang:

| Skenario | Tanpa masking | Dengan masking | Selisih |
| --- | --- | --- | --- |
| Bidang berjauhan (uji terkontrol, 516 px) | 0.865 | **0.998** | **+0.133** |
| SU_057 vs SU_058 (bidang relatif berdekatan) | 0.805 | 0.721 | −0.084 |

Pada SU_057/SU_058 skor tanpa masking justru **terlalu tinggi** karena kedua bidang
kebetulan bertumpuk. Yang benar adalah angka dengan masking, karena hanya mengukur
kesepakatan peta dasar.

## Fitur Phase 5 (Similarity & Clustering)

- **Skor kemiripan 0–100** dengan **lima indikator berbobot**, bukan satu indikator:
  cakupan peta acuan (0.45), kualitas alignment (0.20), dukungan inlier (0.15),
  kemiripan struktur garis (0.12), sebaran inlier (0.08).
- **Gerbang keras**: pasangan dengan cakupan acuan < 0.40 atau inlier < 30 ditolak
  (skor 0) walau alignment-nya terlihat bagus.
- **Graph-based clustering** pada pasangan berskor ≥ 65.

  > **Perbaikan penting (chaining).** Sebelumnya dipakai *single linkage*: cukup
  > satu pasangan perbatasan untuk menyatukan dua kelompok. Akibatnya batch 118
  > PDF dengan peta berbeda semuanya masuk **satu** kelompok. Sekarang dipakai
  > **average linkage**, yang hanya menyatukan kelompok bila kemiripan
  > rata-ratanya memenuhi ambang. Diuji: 118 dokumen / 3 lokasi → **3 kelompok
  > (40/40/38)**, tanpa pencampuran, dan grup kecil 2 dokumen berskor 77,2 tetap
  > terbentuk.

  Dokumen tanpa pasangan **tidak dipaksa** masuk kelompok.
- **Keyakinan kelompok**: Tinggi (≥85), Sedang (65–84, perlu verifikasi), Rendah.
- **Tab “Hasil Pengelompokan”**: pohon kelompok + anggota, panel rincian, dan
  pemilihan anggota untuk melompat ke dokumen.
- **Kategori tampilan sesuai PRD 9**: ≥85 kemiripan tinggi, 65–84 perlu pemeriksaan,
  <65 kemiripan rendah.

### Hasil pada dokumen referensi

| Pasangan | Skor | Kategori |
| --- | --- | --- |
| SU_057 vs SU_058 | **84.4** | Perlu pemeriksaan |
| SU_057 vs SU_058 (rotasi 4°) | 78.4 | Perlu pemeriksaan |
| SU_057 vs SU_058 (skala 0.9) | 77.2 | Perlu pemeriksaan |
| SU_057 vs dirinya sendiri | 97.3 | Kemiripan tinggi |
| Peta vs halaman administrasi | 0.0 | Kemiripan rendah |
| Peta vs peta sintetis berbeda | 0.0 | Kemiripan rendah |

Kedua dokumen contoh **berhasil dikelompokkan bersama** (KELOMPOK_001), dengan status
"perlu verifikasi manual" karena skor 84.4 berada di bawah 85.

### Temuan penting: alignment bagus bukan bukti kemiripan

Pengukuran pada negatif sulit menunjukkan indikator geometris saja menyesatkan:

| Kasus | Kualitas alignment | Inlier | Cakupan acuan |
| --- | --- | --- | --- |
| Peta asli vs peta asli | 1.000 | 2000 | 1.000 |
| SU_057 vs SU_058 | 0.887 | 341 | 0.721 |
| **Hanya 30% halaman** | **0.976** | 147 | **0.095** |
| **Hanya 70% halaman** | **0.990** | 928 | **0.314** |
| Peta vs admin | 0.000 | 0 | 0.000 |

Sepotong halaman yang sama menghasilkan alignment **lebih baik** daripada pasangan
asli, tetapi hanya menjelaskan sebagian kecil halaman. Karena itu cakupan acuan
diberi bobot terbesar dan menjadi gerbang keras.

### Catatan keterbatasan prefilter

Prefilter murah (jumlah kecocokan rasio-test) **tidak** memisahkan dua peta berbeda
yang sama-sama berupa garis renggang: keduanya menghasilkan 469–576 kecocokan.
Prefilter hanya membuang halaman kosong/berfitur sangat sedikit. Pemisahan sebenarnya
dilakukan oleh gerbang cakupan pada tahap skoring.

Review manual dan ekspor laporan **belum** ada di fase ini.

## Fitur Phase 6 (Group Review & Export)

- **Koreksi manual** di tab Hasil Pengelompokan:
  - Tandai / batalkan **verifikasi** kelompok.
  - **Gabungkan** kelompok ke kelompok lain.
  - **Pisahkan** dokumen dari kelompoknya (masuk “Belum Terkelompok”).
  - **Kembalikan Otomatis** untuk membuang semua koreksi.
- **Semua koreksi bersifat immutable** — setiap operasi menghasilkan state baru,
  dokumen tidak pernah hilang atau terduplikasi (`test_every_document_appears_exactly_once`).
- **Ekspor laporan** CSV atau XLSX dengan 8 kolom sesuai PRD 10A:
  Nama file, Nomor SU, Halaman peta, ID kelompok, Jumlah pasangan cocok,
  Skor kemiripan, Confidence, Status.
- **Salin ke folder** `KELOMPOK_xxx/` dan `BELUM_TERKELOMPOK/` dengan operasi
  **COPY**, bukan move. File asli tidak pernah diubah atau dihapus.
- **Konfirmasi sebelum menyalin** bila masih ada kelompok belum terverifikasi.

### Contoh isi laporan

```csv
Nama file;Nomor SU;Halaman peta;ID kelompok;Jumlah pasangan cocok;Skor kemiripan;Confidence;Status
SU_057.pdf;057;2;KELOMPOK_001;463;84.4;Sedang;Perlu verifikasi
SU_058.pdf;058;2;KELOMPOK_001;463;84.4;Sedang;Perlu verifikasi
```

### Struktur folder hasil

```
OUTPUT/
├── KELOMPOK_001/
│   ├── SU_057.pdf
│   └── SU_058.pdf
└── BELUM_TERKELOMPOK/
```

Menyalin ulang tanpa menimpa melewati file yang sudah ada (`skipped`), sehingga
aman dijalankan berulang.

## Fitur Phase 7 (Optimization)

- **Cache analisis di disk** (`data/cache/analysis/`): render, deteksi halaman peta,
  normalisasi, dan mutasi bidang disimpan per dokumen. Kunci cache memakai
  SHA-256 isi file + versi pipeline (detektor, normalisasi, bidang), jadi
  perubahan algoritma otomatis menggugurkan cache lama.
  - Terukur: **1,27 s → 85 ms per dokumen** (15× lebih cepat) dengan hasil identik.
  - Ukuran ~166 KB per dokumen (160 MB untuk 999 dokumen).
- **Database SQLite** (`data/landcad.db`) sesuai PRD 11: tabel `documents`,
  `features`, `similarity`, `groups`, `group_members`. Metadata dokumen dan
  halaman peta disimpan setelah analisis. Deskriptor fitur tetap di file cache
  karena PRD mengizinkan file bila lebih efisien.
- **Ekspor di background thread** — menyalin ratusan PDF tidak lagi membekukan UI,
  progress ditampilkan per file.
- **Pemulihan kesalahan**: satu PDF rusak tidak menghentikan batch; cache yang
  korup dihapus dan dihitung ulang; kegagalan tulis database tidak mematikan UI.
- **Packaging PyInstaller** (`LandCADMapGrouper.spec` + `build_exe.bat`):
  `dist/LandCADMapGrouper/LandCADMapGrouper.exe`, bundle ~314 MB.
  `data/` dibuat di sebelah `.exe`, bukan di `_internal` (yang read-only).

### Angka sebelum/sesudah

| Tahap | Sebelum | Sesudah |
| --- | --- | --- |
| Persiapkan 2 dokumen (run ulang) | 2.573 ms | **170 ms** |
| Grouping 2 dokumen (hangat) | 2,51 s | **0,35 s** |
| Bundle PyInstaller | 363 MB | 314 MB |
| Penyalinan 100+ file | membekukan UI | background + progress |

Catatan jujur: pada mesin ini, memperkecil bundle lebih jauh terkendala
`cv2.pyd` (86 MB) dan `_mupdf.pyd` (12 MB) yang memang dipakai; keduanya tidak
bisa dibuang tanpa mengganti library.

## Fitur Phase 8 (Penyempurnaan PRD)

Menutup tiga celah yang ditemukan saat audit terhadap PRD.

**1. Hasil pengelompokan kini tersimpan (PRD 11).**
Sebelumnya hanya tabel `documents` yang diisi, sehingga kelompok dan koreksi
manual hilang saat aplikasi ditutup. Sekarang tabel `groups`, `group_members`,
dan `similarity` diisi, termasuk kolom `match_count` (PRD 10A) yang baru
ditambahkan lewat migrasi skema otomatis v1 → v2.

- Tersimpan otomatis setiap kali hasil berubah (kelompokkan, verifikasi,
  pisahkan, gabung).
- Saat membuka folder, aplikasi menawarkan **“Muat hasil tersimpan?”**.
- Tombol **Muat Hasil Tersimpan** tersedia di tab Hasil Pengelompokan.
- Data lama tidak hilang saat upgrade: migrasi hanya menambah kolom.

**2. Pengaturan ambang di antarmuka (PRD 9).**
Tab **Pengaturan** baru berisi: ambang kemiripan (taut), ambang keyakinan
tinggi, ambang halaman peta, dan prefilter — disimpan lewat QSettings.
Nilai divalidasi (keyakinan tinggi harus di atas ambang taut) dan dipakai
sungguhan oleh analisis serta pengelompokan.

**3. Dashboard lengkap (PRD 8.1).**
Delapan kartu: Total PDF, Berhasil Dibaca, Gagal Dibaca, Total Halaman,
Halaman Peta Ditemukan, **Jumlah Kelompok**, **Belum Berkelompok**, Status.

Verifikasi terukur: setelah menutup dan membuka aplikasi, hasil tersimpan
dimuat kembali dengan status verifikasi, skor 84,4, inlier 341, dan 463
kecocokan tetap utuh; file PDF asli tidak berubah (SHA-256 identik).

## Fitur Phase 9 (Perbaikan Akurasi)

**Masalah yang diperbaiki:** peta berbeda ikut masuk satu kelompok. Penyebabnya
bukan clustering, melainkan **skor** — dan penyebabnya terukur, bukan dugaan.

Setiap halaman peta memuat elemen yang sama di semua dokumen: bingkai, panah
utara, `SKALA 1 : 500`, `PENJELASAN`, `batas tanah ini`. ORB mencocokkan elemen
itu di **setiap** pasangan. Diukur pada halaman nyata:

| Kasus | area isi | area pinggir | inlier isi |
| --- | --- | --- | --- |
| Peta **sama** (SU_057 vs SU_058) | **82,5** | 66,2 | 135 |
| Peta **beda**, bingkai sama | **1,8 – 5,8** | **100,0** | **0** |

Peta berbeda mendapat area pinggir sempurna (100,0) dengan 786 inlier di sana,
tetapi **nol inlier di area isi**. Template menyumbang sekitar +64 poin.

**Perbaikan:**

1. **Bukti wajib dari area isi peta.** Indikator dominan sekarang
   `interior_coverage` (bobot 0,45), bukan cakupan seluruh halaman. Gate keras:
   cakupan isi ≥ 0,45 dan inlier isi ≥ 25.
2. **Ambang gabung 82**, bukan 65. PRD 9 menyebut 65–84 "Perlu pemeriksaan" —
   itu kategori *tampilan*, bukan syarat gabung.
3. **Rentang 65–81 ditampilkan sebagai "Kandidat (perlu pemeriksaan)"** dan
   **tidak** digabung otomatis.

Hasil:

| Kasus | Sebelum | Sesudah |
| --- | --- | --- |
| Peta sama | 84,4 | 83,0 (tetap bergabung) |
| Peta sama, rotasi 8° | 70,6 | **85,2** (bergabung) |
| Peta beda, bingkai sama | 63,5–65,9 | **0,0** (ditolak) |

Rotasi juga jadi lebih stabil karena cakupan seluruh halaman dulu menghukum
rotasi (0,53 saat rotasi 8°), sedangkan cakupan area isi tetap 0,82.

**Yang perlu Anda lakukan:** jalankan ulang **Kelompokkan**. Pasangan yang
sebelumnya salah bergabung akan hilang; pasangan yang meragukan muncul di
simpul *Kandidat* untuk Anda periksa.

**Batasan:** ambang 82 adalah nilai konservatif dari dua dokumen, **bukan hasil
kalibrasi** pada dataset berlabel (PRD 14). Pasangan asli yang lebih lemah dari 82
akan menjadi kandidat, bukan kelompok — ini disengaja.

## Catatan penting tentang orientasi

Kedua dokumen contoh menyimpan halaman peta dalam keadaan **menyamping**
(teks `SKALA 1 : 500` terbaca setelah diputar). Normalisasi mendeteksi ini lewat
arah tata letak teks dan memutar halaman sehingga label terbaca horizontal.
Halaman yang sudah tegak tidak diubah.

Efeknya: halaman peta yang aslinya portrait menjadi kanvas landscape setelah dikoreksi.
Gambar asli tetap disimpan tanpa perubahan.

## Persyaratan

- Windows 10/11
- Python 3.11
- Tidak memerlukan koneksi internet saat aplikasi berjalan (kecuali instalasi dependency)
- Tidak ada layanan AI cloud, tidak ada pengiriman dokumen ke pihak ketiga

## Instalasi

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Menjalankan aplikasi

```powershell
.\.venv\Scripts\python.exe main.py
```

Langkah penggunaan:

1. Klik **Buka Folder PDF**, pilih folder yang berisi dokumen (mis. folder proyek ini).
2. Tunggu proses validasi; progress bar dan status bar menampilkan file yang sedang diperiksa.
3. Klik salah satu baris untuk menampilkan pratinjau.
4. Gunakan tombol navigasi halaman atau `PageDown` / `PageUp`.
5. Klik **Deteksi Halaman Peta** (`Ctrl+D`) untuk mendeteksi halaman peta dan
   menjalankan normalisasi. Progress bar menampilkan dokumen yang sedang dianalisis.
6. Setelah selesai, kolom **Halaman Peta** dan **Skor** terisi. Pilih dokumen lalu
   klik **Normalisasi** di panel pratinjau untuk melihat hasil normalisasi.
7. Buka tab **Perbandingan Peta**, pilih dokumen **A** dan **B**, lalu klik **Bandingkan**.
   Hasil overlay muncul bersama ringkasan metrik alignment.
8. Buka tab **Hasil Pengelompokan**, klik **Kelompokkan** untuk membentuk kelompok.
   Klik anggota kelompok untuk melihat rincian dan melompat ke dokumennya.
9. Perbaiki hasil bila perlu: **Tandai Terverifikasi**, **Gabungkan ke…**, atau
   **Pisahkan Dokumen**. Hasil tersimpan otomatis.
10. Klik **Ekspor Laporan** untuk menyimpan CSV/XLSX, atau **Salin ke Folder** untuk
    menyalin dokumen ke folder kelompok.
11. Buka tab **Pengaturan** untuk mengubah ambang kemiripan, ambang halaman peta,
    atau membersihkan cache analisis. Jalankan ulang pengelompokan agar berlaku.
12. Klik **Keluarkan dari Daftar** bila ingin mengeluarkan file dari daftar (file asli tetap utuh).
13. Klik **Muat Ulang** untuk memindai ulang folder aktif.

## Menjalankan pengujian

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

Pengujian GUI berjalan headless (`QT_QPA_PLATFORM=offscreen`) sehingga tidak memerlukan layar.

## Membuat executable (PyInstaller)

```powershell
.\build_exe.bat
```

Hasil: `dist\LandCADMapGrouper\LandCADMapGrouper.exe` (bundle ~314 MB).
Jalankan `LandCADMapGrouper.exe`; folder `data\` (log, cache, database, output)
dibuat otomatis di sebelah executable.

Build manual tanpa skrip:

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm LandCADMapGrouper.spec
```

## Struktur proyek

```
LandCAD Map Grouper/
├── main.py                  # entry point + tema aplikasi
├── config.py                # konstanta & path aplikasi
├── requirements.txt
├── README.md
├── app/                     # lapisan GUI (PySide6)
│   ├── main_window.py
│   ├── dashboard.py
│   ├── models.py
│   ├── preview_view.py
│   ├── render_worker.py
│   ├── scan_worker.py
│   ├── analysis_worker.py
│   ├── comparison_worker.py
│   ├── comparison_view.py
│   ├── grouping_worker.py
│   ├── group_results_view.py
│   ├── export_worker.py
│   ├── settings_store.py
│   └── settings_view.py
├── core/                    # logika citra/PDF tanpa Qt
│   ├── pdf_reader.py
│   ├── map_page_detector.py
│   ├── image_normalizer.py
│   ├── feature_extractor.py
│   ├── image_alignment.py
│   ├── parcel_detector.py
│   ├── similarity_engine.py
│   ├── clustering_engine.py
│   ├── review_model.py
│   └── diagnose_pair.py
├── services/                # orkestrasi (scan, analisis, perbandingan, grouping, ekspor)
│   ├── import_service.py
│   ├── processing_service.py
│   ├── comparison_service.py
│   ├── grouping_service.py
│   ├── export_service.py
│   ├── cache_service.py
│   └── persistence_service.py
├── database/                # SQLite (PRD 11)
│   └── db.py
├── utils/
│   ├── file_utils.py
│   ├── image_utils.py
│   └── logging_utils.py
├── data/                    # dibuat otomatis: logs, cache, normalized, output
│   ├── cache/
│   │   └── features/
│   ├── logs/
│   ├── normalized/
│   └── output/
├── docs/
│   ├── architecture.md
│   └── user_guide.md
└── tests/
    ├── conftest.py
    ├── test_pdf_reader.py
    ├── test_import_service.py
    ├── test_models.py
    ├── test_map_detection.py
    ├── test_normalization.py
    ├── test_processing_service.py
    ├── test_feature_extractor.py
    ├── test_alignment.py
    ├── test_comparison_service.py
    ├── test_comparison_view.py
    ├── test_parcel_detector.py
    ├── test_parcel_comparison.py
    ├── test_similarity.py
    ├── test_clustering.py
    ├── test_grouping_service.py
    ├── test_group_results_view.py
    ├── test_review_model.py
    ├── test_export_service.py
    ├── test_cache_service.py
    ├── test_database.py
    ├── test_chaining_regression.py
    ├── test_group_persistence.py
    ├── test_settings.py
    ├── test_dashboard.py
    ├── test_region_similarity.py
    ├── test_threshold_semantics.py
    └── test_packaging.py
```

## Catatan keamanan

- PDF dibuka **read-only**; file asli tidak pernah dimodifikasi (`test_document_does_not_modify_original`).
- Tidak ada proses jaringan atau API cloud pada fase ini.
- Log aplikasi berada di `data/logs/landcad.log` dan tidak memuat isi dokumen.

## Dokumentasi tambahan

- [docs/architecture.md](docs/architecture.md) — keputusan arsitektur (Phase 1–9)
- [docs/user_guide.md](docs/user_guide.md) — panduan penggunaan

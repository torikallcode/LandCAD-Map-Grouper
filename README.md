# LandCAD Map Grouper

Aplikasi desktop **lokal/offline** untuk mengelompokkan dokumen Surat Ukur (SU) PDF
berdasarkan kemiripan peta dasar.

> Status: **Phase 5 — Similarity & Clustering** selesai.
> Review grup manual dan ekspor belum diimplementasikan (Phase 6).

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
- **Graph-based clustering** (connected components) pada pasangan berskor ≥ 65.
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
9. Klik **Keluarkan dari Daftar** bila ingin mengeluarkan file dari daftar (file asli tetap utuh).
10. Klik **Muat Ulang** untuk memindai ulang folder aktif.

## Menjalankan pengujian

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

Pengujian GUI berjalan headless (`QT_QPA_PLATFORM=offscreen`) sehingga tidak memerlukan layar.

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
│   └── group_results_view.py
├── core/                    # logika citra/PDF tanpa Qt
│   ├── pdf_reader.py
│   ├── map_page_detector.py
│   ├── image_normalizer.py
│   ├── feature_extractor.py
│   ├── image_alignment.py
│   ├── parcel_detector.py
│   ├── similarity_engine.py
│   └── clustering_engine.py
├── services/                # orkestrasi (scan, analisis, perbandingan, grouping)
│   ├── import_service.py
│   ├── processing_service.py
│   ├── comparison_service.py
│   └── grouping_service.py
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
    └── test_group_results_view.py
```

## Catatan keamanan

- PDF dibuka **read-only**; file asli tidak pernah dimodifikasi (`test_document_does_not_modify_original`).
- Tidak ada proses jaringan atau API cloud pada fase ini.
- Log aplikasi berada di `data/logs/landcad.log` dan tidak memuat isi dokumen.

## Dokumentasi tambahan

- [docs/architecture.md](docs/architecture.md) — keputusan arsitektur (Phase 1–5)
- [docs/user_guide.md](docs/user_guide.md) — panduan penggunaan

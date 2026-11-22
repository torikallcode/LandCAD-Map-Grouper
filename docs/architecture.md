# Arsitektur — Phase 1 (PDF Importer & Viewer)

## Tujuan fase

Membaca folder PDF, memvalidasi setiap file, menampilkan daftar dokumen, dan
menampilkan pratinjau halaman. Tidak ada deteksi peta, normalisasi, ekstraksi fitur,
maupun pengelompokan pada fase ini.

## Prinsip

1. **Pemisahan GUI dan logika.** Semua akses PDF berada di `core/` dan `services/`
   tanpa import PySide6. Ini membuat logika dapat diuji tanpa event loop.
2. **Satu phase, satu tanggung jawab.** Fase ini hanya importer + viewer.
3. **File asli tidak disentuh.** PDF dibuka read-only; operasi copy/delete baru ada di Phase 6.
4. **Offline penuh.** Tidak ada jaringan, tidak ada model cloud.
5. **Kesalahan per file tidak menghentikan batch.** File rusak menjadi entri berstatus gagal.

## Lapisan

```
main.py  (entry point, tema, palette)
   │
   ├── app/            GUI: MainWindow, DashboardBar, PreviewPanel, model tabel, worker thread
   │        │
   │        └── memanggil ↓ (hanya lewat API publik)
   ├── services/       orkestrasi: scan folder + validasi massal (ImportResult)
   │        │
   │        └── memanggil ↓
   ├── core/           akses PDF: validasi (inspect_pdf) & render (PdfDocument.render_page)
   │
   ├── utils/          helper file/log
   └── config.py       konstanta, path data
```

Arah dependensi hanya ke bawah. `core` tidak tahu apa-apa tentang Qt.

## Komponen

### `core/pdf_reader.py`

| Simbol | Tanggung jawab |
| --- | --- |
| `inspect_pdf(path)` | Validasi + metadata (jumlah halaman, ukuran, mtime) tanpa render |
| `validate_pdf(path)` | Versi ringkas: `(bool, pesan)` |
| `PdfDocument` | Context manager read-only untuk render halaman |
| `PdfDocument.render_page(index, dpi, max_dimension)` | Render ke PNG bytes (RGB, tanpa alpha) |
| `DocumentStatus` | Enum status: `OK`, `CORRUPT`, `ENCRYPTED`, `EMPTY`, `MISSING`, `UNREADABLE` |
| `PdfInfo` | Dataclass metadata + status, dipakai seluruh aplikasi |

Detail penting:

- **Pemeriksaan tanda tangan file.** PyMuPDF dapat membuka file non-PDF yang di-rename
  menjadi `.pdf`. Karena itu 1 KB pertama diperiksa harus memuat `%PDF-`; kalau tidak,
  statusnya `CORRUPT`. Ini yang mencegah gambar tersamar menjadi "PDF valid".
- **DPI dijepit.** `max_dimension` membatasi sisi terpanjang hasil render sehingga halaman
  besar tidak menghasilkan bitmap raksasa. Preview memakai 144 DPI dengan batas 2400 px.
- **Tidak ada render ke disk.** Semua hasil render berupa bytes di memori.

### `services/import_service.py`

- `find_pdf_files(folder, recursive=False)` — daftar PDF dengan urutan alami.
- `import_folder(...)` — scan + validasi dengan callback `progress` dan `should_cancel`.
- `inspect_files(paths, ...)` — validasi daftar eksplisit, dedup berdasarkan path.
- `ImportResult` — hasil agregat (`valid`, `invalid`, `total_pages`, `cancelled`).

Kontrak: satu file gagal **tidak** menghentikan proses; kegagalan dicatat per file.

### `app/`

| File | Isi |
| --- | --- |
| `main_window.py` | Toolbar, dashboard, splitter daftar/pratinjau, status bar, aksi keyboard |
| `dashboard.py` | Kartu ringkasan (total, valid, gagal, halaman, status) |
| `models.py` | `DocumentTableModel` + `DocumentFilterProxyModel` (filter & natural sort) |
| `preview_view.py` | Pratinjau halaman, navigasi, zoom, fit-to-width, indikator status |
| `scan_worker.py` | `QThread` untuk scan folder agar UI tidak freeze |
| `render_worker.py` | `QRunnable` untuk render halaman di thread pool |

#### Threading

- **Scan** berjalan di `ScanWorker` (QThread). Progres dikirim via signal
  `progress(current, total, name)`; pembatalan lewat `requestInterruption()`
  yang diperiksa `should_cancel`.
- **Render halaman** berjalan di `QThreadPool` (maks. 2 thread). Setiap permintaan render
  membawa token monotonik; hasil dengan token lama dibuang. Ini mencegah halaman lama
  menimpa halaman baru saat pengguna berpindah cepat.

## Alur data

```
Klik "Buka Folder PDF"
  → MainWindow._choose_folder → _start_scan(folder)
  → ScanWorker(QThread) → import_folder(...)
      → find_pdf_files → inspect_pdf per file
  → ImportResult → DocumentTableModel.set_documents
  → DashboardBar.update_summary

Pilih baris
  → PreviewPanel.show_document(document)
  → RenderTask(token, path, page_index, dpi) di QThreadPool
  → signal finished(token, png, w, h, dpi)
  → QPixmap → fit-to-width / zoom → QLabel
```

## Keputusan desain dan alasannya

| Keputusan | Alasan |
| --- | --- |
| PyMuPDF untuk render (bukan Qt PDF) | Satu mesin render untuk semua fase; hasil konsisten dengan OpenCV nanti |
| Preview via PNG bytes, bukan path | Tidak menulis file sementara; mudah diuji dan dibuang |
| `QAbstractTableModel` + proxy | Filter dan sort tanpa membangun ulang widget; siap untuk ratusan baris |
| Scan di QThread | PRD 8.3: pemrosesan tidak boleh membekukan UI |
| Token render | Menghindari race condition saat navigasi cepat |
| Hidup tanpa SQLite di Phase 1 | Database baru dibutuhkan saat ada fitur, metadata, hash |
| Data folder `data/` | Menyiapkan lokasi log/cache/output untuk fase berikutnya |

## Batasan fase ini (disengaja)

- Belum ada deteksi halaman peta (Phase 2).
- Belum ada normalisasi gambar, cache fitur, maupun database.
- Belum ada dukungan drag-and-drop atau pemilihan file individual di luar folder.
- Belum ada paket PyInstaller (Phase 7).

---

# Phase 2 — Map Detection & Normalization

## Tujuan fase

Menemukan halaman peta pada setiap PDF **berdasarkan karakteristik visual** (bukan
asumsi "halaman 2"), lalu menormalkan halaman tersebut agar siap untuk pencocokan
di Phase 3. Tidak ada ekstraksi fitur, alignment, maupun clustering.

## Modul baru

| File | Tanggung jawab |
| --- | --- |
| `core/map_page_detector.py` | Skor dan label per halaman; pilih halaman peta terbaik |
| `core/image_normalizer.py` | Estimasi orientasi, estimasi kemiringan, pipeline normalisasi |
| `utils/image_utils.py` | Helper OpenCV murni: binarize, rotasi, segmen Hough, text mask |
| `services/processing_service.py` | Render→deteksi→normalisasi per dokumen, progres, export |
| `app/analysis_worker.py` | Menjalankan analisis di QThread |

## Deteksi halaman peta

### Fitur yang diukur (`PageFeatures`)

| Fitur | Arti | Perilaku pada data contoh |
| --- | --- | --- |
| `components_per_megapixel` | Kepadatan komponen terhubung | admin ≈ 443–479, peta ≈ 37–39 |
| `text_rows` | Jumlah pita baris dari proyeksi horizontal | admin 19–26, peta 1–2 |
| `ink_ratio` | Proporsi piksel tinta | admin ≈ 0.044, peta ≈ 0.006–0.007 |
| `blank_ratio` | Proporsi area kosong (dilasi 25×25) | admin ≈ 0.71, peta ≈ 0.95 |
| `diagonal_share` | Porsi panjang segmen Hough diagonal | admin 0.00, peta 0.02–0.14 |
| `line_strength` | Total panjang segmen / diagonal halaman | keduanya ≈ 1.0 |

### Skor komposit

```
skor = 0.30·text_density + 0.22·ink + 0.22·structure + 0.16·blank + 0.10·text_rows
```

Setiap sub-skor dinormalisasi ke 0–1 dengan fungsi `_decreasing`/`_increasing`.
Bobot dan ambang (`threshold=0.45`) dapat diubah lewat konstruktor, dan nama bobot
yang tidak dikenal ditolak (`ValueError`) agar salah ketik tidak diam-diam diabaikan.

### Hasil pengukuran pada dokumen referensi

| Dokumen | Halaman 1 (administrasi) | Halaman 2 (peta) |
| --- | --- | --- |
| SU_057 | 0.12 | **0.90** |
| SU_058 | 0.13 | **0.88** |

Margin > 0.75 pada data referensi. Pengujian sintetis menambahkan variasi rotasi
kuartal dan resolusi rendah untuk memastikan detektor tidak overfit ke dua file ini.

### Mengapa bukan proyeksi seluruh halaman

Deteksi orientasi memakai proyeksi **mask teks** (komponen seukuran glif), bukan
proyeksi seluruh halaman. Pada halaman peta, bingkai halaman mendominasi proyeksi
mentah sehingga halaman menyamping tetap terlihat "horizontal". Setelah bingkai dan
garis panjang dibuang, rasio variansi baris/kolom menjadi pemisah yang stabil:

| Kondisi | Rasio baris/kolom |
| --- | --- |
| Halaman teks tegak | 3.0–3.5 |
| Halaman teks menyamping | 0.28–0.32 |
| Halaman peta tegak | 2.19–3.10 |
| Halaman peta menyamping | 0.42–0.46 |

Ambang: ≤ 0.60 → putar 90°; ≥ 1.60 → biarkan; di antaranya → ambigu, tidak diputar.
Bila tinta teks < 120 piksel, orientasi tidak ditebak sama sekali.

## Normalisasi

Urutan pipeline (versi `normalize-v2`):

1. **grayscale** — kanal tunggal dari render PNG.
2. **CLAHE** — `clipLimit=2.0`, grid 8×8.
3. **median denoise** — kernel 3.
4. **rotate90** — hanya bila orientasi terdeteksi yakin.
5. **deskew** — hanya bila estimasi andal dan |sudut| ≥ 0.10°.
6. **resize** — sisi terpanjang 1600 px (interpolasi AREA saat mengecil).

Gambar asli dan hasil normalisasi dikembalikan terpisah (`NormalizedImage.original`
vs `.image`) dan dapat ditulis ke disk lewat `save_normalized()`.

### Estimasi kemiringan

Memakai **median sudut segmen Hough**, bukan projection-variance. Alasannya empiris:
pada halaman peta yang sparse, projection-variance melaporkan kemiringan −16° untuk
halaman yang sebenarnya tegak. Median Hough memberi galat ≤ 0.5° pada uji rotasi
−10° … +14° (residual < 0.1° setelah dikoreksi).

Keyakinan = `0.65·(1 − MAD/2.5) + 0.35·min(jumlah_garis/40, 1)`; deskew hanya
diterapkan bila keyakinan ≥ 0.5 dan ada ≥ 6 segmen.

## Threading dan UI

- `AnalysisWorker` (QThread) menjalankan `analyse_documents` dengan callback progres
  dan pemeriksaan pembatalan, sama seperti `ScanWorker`.
- Kolom baru di tabel: `Halaman Peta` dan `Skor` (`Column.MAP_PAGE`, `Column.SCORE`).
- Panel pratinjau punya dua baris toolbar ringkas. Versi satu baris memaksa lebar
  minimum 851 px dan memotong kolom tabel; setelah dipecah, minimum turun ke 382 px.
- Tombol **Normalisasi** hanya aktif pada halaman yang memang halaman peta.

## Keputusan desain Phase 2

| Keputusan | Alasan |
| --- | --- |
| Deteksi berbasis fitur, bukan indeks halaman | PRD melarang asumsi dua halaman |
| Mask teks untuk orientasi | Proyeksi halaman mentah didominasi bingkai |
| Hough untuk deskew | Terbukti stabil pada peta sparse, tidak seperti projection-variance |
| Orientasi sebelum deskew | Sudut Hough diukur pada kerangka akhir |
| Ambang ambigu → jangan putar | Salah memutar lebih merusak daripada membiarkan |
| Ambil halaman skor tertinggi sebagai peta | Dokumen bisa punya >1 kandidat; skor memberi peringkat |
| Normalisasi tidak fatal | Deteksi tetap tersimpan walau normalisasi gagal |

## Batasan Phase 2

- Belum ada cache hasil normalisasi antar-sesi (baru disimpan saat diminta).
- Deteksi belum dilatih ulang pada dataset berlabel; ambang masih nilai awal.
- Orientasi 180° memang tidak dideteksi (tidak dibedakan dari 0°); untuk peta
  hal ini dapat diterima karena Phase 3 melakukan alignment.
- Belum ada penyimpanan ke SQLite.

## Pengujian Phase 2

| Berkas | Cakupan |
| --- | --- |
| `tests/test_map_detection.py` | Fitur, skor, ambang, bobot kustom, rotasi kuartal, resolusi rendah |
| `tests/test_normalization.py` | Grayscale, resize, orientasi, deskew, imutabilitas input |
| `tests/test_processing_service.py` | Deteksi + normalisasi pada SU_057/SU_058, error per file, progres, cancel, export |

---

# Phase 3 — Feature Extraction & Alignment

## Tujuan fase

Mengekstrak deskriptor visual dari halaman peta ternormalisasi, mencocokkannya
antar dokumen, dan menghitung transformasi geometri yang menyelaraskan keduanya.
Belum ada perhitungan skor kemiripan akhir maupun pengelompokan.

## Modul baru

| File | Tanggung jawab |
| --- | --- |
| `core/feature_extractor.py` | ORB + cache `.npz`, preprocessing CLAHE/blur |
| `core/image_alignment.py` | BFMatcher, ratio test, RANSAC, warp, overlay |
| `services/comparison_service.py` | Persiapan peta, perbandingan, metrik tinta, ringkasan |
| `app/comparison_worker.py` | Menjalankan perbandingan di QThread |
| `app/comparison_view.py` | Tab “Perbandingan Peta” (overlay / berdampingan) |

## Ekstraksi fitur

Pipeline: `grayscale → CLAHE(2.0, 8×8) → Gaussian(3) → ORB`.

Parameter ORB diubah dari default OpenCV karena peta ini hampir kosong:

| Parameter | Default OpenCV | Dipakai | Alasan |
| --- | --- | --- | --- |
| `fastThreshold` | 20 | **10** | default terlalu sedikit corner pada garis peta tipis |
| `edgeThreshold` | 31 | **15** | menjaga fitur dekat tepi/bingkai |
| `nfeatures` | 500 | **2000** | cadangan fitur untuk RANSAC |

Catatan: `nfeatures` di ORB adalah target **per level**, bukan batas keras —
dengan `nfeatures=50` hasilnya bisa 53 keypoint.

### Cache fitur

- Kunci: `sha1(gambar.tobytes())[:16]` + versi pipeline
  (`FEATURE_VERSION | PREPROCESSING_VERSION | ALIGNMENT_VERSION`).
- Disimpan sebagai `.npz` terkompresi di `data/cache/features/`.
- Cache rusak tidak fatal: file dihapus lalu fitur dihitung ulang.
- Perubahan pipeline otomatis menggugurkan cache lama karena versi ikut di nama file.

## Pencocokan dan alignment

1. **BFMatcher** `NORM_HAMMING`, `knnMatch(k=2)`.
2. **Lowe ratio test** 0.75.
3. **RANSAC** dengan `estimateAffinePartial2D` (threshold 4 px, 3000 iterasi).
4. **Homografi** dihitung hanya bila kecocokan ≥ 16, lalu dipakai bila:
   - inlier-nya lebih banyak daripada affine, **dan**
   - matriksnya stabil: suku perspektif menggeser sudut halaman ≤ 5% dimensi halaman.

Alasan aturan kestabilan: homografi dari 12 titik yang hampir segaris tetap lolos
RANSAC tetapi menggeser sudut sejauh 124% halaman. Kasus nyata hanya menggeser 0.8%
pada pencocokan asli.

### Arah transformasi (penting)

Matriks **harus** diestimasi dari **kandidat → acuan**, lalu diterapkan langsung ke
gambar kandidat. `cv2.warpAffine`/`warpPerspective` sudah memetakan piksel sumber ke
kerangka tujuan, jadi matriks dipakai apa adanya — **tidak** di-inverse.

Diukur pada rotasi sintetis 8°:

| Perlakuan | Tumpang tindih tinta |
| --- | --- |
| Matriks arah benar | 0.626 |
| Matriks arah salah | 0.131 |

Angka ini juga menjadi regression test (`test_warp_uses_forward_matrix_not_inverse`).

### Metrik kualitas

```
quality = 0.45·min(inlier/150, 1) + 0.30·inlier_ratio + 0.25·(1 − error/8)
```

Kategori tampilan: ≥ 0.75 Tinggi, ≥ 0.50 Sedang, selain itu Rendah.

**Peringatan penting:** kualitas alignment hanya mengukur seberapa baik dua gambar
bisa dipertemukan secara geometris — bukan bukti bahwa keduanya peta yang sama.
Halaman administrasi dari dua dokumen berbeda juga mendapat quality 0.92. Skor
kemiripan akhir baru dibuat di Phase 5; PRD menegaskan hal yang sama.

## Metrik tumpang tindih tinta

IoU biner **ditolak** untuk garis tipis: rotasi 4° menjatuhkan IoU ke 0.027 padahal
alignment benar. Yang dipakai adalah *coverage* via distance transform:

```
coverage = min( fraksi tinta A dalam 6 px dari tinta B,
                fraksi tinta B dalam 6 px dari tinta A )
```

Ambang tinta **225** (bukan Otsu). Otsu memilih ~175 pada halaman ini dan membuang
garis peta dasar yang pucat (gray 210–235), sehingga pasangan asli hanya mencapai
0.559 — setelah ambang 225 nilainya 0.805. Ambang 245 mulai menggabungkan glif
menjadi blok hitam.

Validasi pada data referensi:

| Pasangan | Coverage |
| --- | --- |
| Identik | 1.000 |
| SU_057 vs SU_058 | 0.805 |
| Peta vs halaman administrasi | 0.000 |
| Peta vs peta sintetis berbeda | 0.003 |

## Hasil pada dokumen referensi

| Metrik | SU_057 vs SU_058 |
| --- | --- |
| Model | Homografi (stabil) |
| Kecocokan fitur | 499 |
| Inlier | 363 (72.7%) |
| Galat reproyeksi | 1.33 px |
| Kualitas | 0.877 (Tinggi) |
| Tumpang tindih tinta | 0.805 |

Overlay memperlihatkan jalan dan batas bidang sekitar berimpit, sementara kotak
bidang utama (274 m² vs 244 m²) tetap terpisah. Ini menunjukkan alignment mengikuti
peta dasar, bukan bidang utama — tujuan inti produk.

## Kinerja terukur

| Tahap | Waktu |
| --- | --- |
| ORB detect+compute (2 gambar) | ~366 ms |
| knnMatch | ~19 ms |
| RANSAC (affine atau homografi) | ~1 ms |
| `compare_maps` total | ~111 ms |
| `prepare_comparable` (dengan render ulang) | ~1.8 s |

Bottleneck ada pada render + normalisasi ulang per perbandingan, bukan pada ORB.
Optimasi (cache analisis antar-perbandingan) dijadwalkan di Phase 7.

## Batasan Phase 3

- Belum ada skor kemiripan 0–100 maupun pengelompokan (Phase 5).
- Cache fitur belum meng-cache hasil deteksi/normalisasi, jadi render ulang tetap terjadi.
- Perbandingan masih manual dua dokumen, belum pairwise massal.
- Bidang utama belum dimasking; perbedaannya baru dianalisis di Phase 4.
- Belum ada penyimpanan hasil ke SQLite.

## Pengujian Phase 3

| Berkas | Cakupan |
| --- | --- |
| `tests/test_feature_extractor.py` | Preprocessing, keypoint/deskriptor, determinisme, cache round-trip |
| `tests/test_alignment.py` | Matching, ratio test, rotasi/skala, kegagalan graceful, arah warp, overlay |
| `tests/test_comparison_service.py` | Pair asli, simetri, kontrol negatif, cache hit/miss, coverage, PNG |
| `tests/test_comparison_view.py` | Populasi combo, tombol aktif/nonaktif, tukar, overlay/berdampingan, worker |

---

# Phase 4 — Main Parcel Detection

## Tujuan fase

Mengenali bidang tanah utama pada halaman peta, lalu mengabaikan perbedaannya saat
membandingkan, tanpa merusak informasi peta dasar di sekitarnya.

## Modul baru

| File | Tanggung jawab |
| --- | --- |
| `core/parcel_detector.py` | `detect_main_parcels`, `build_parcel_mask`, `ParcelCandidate`, `ParcelMask` |

Perubahan: `services/comparison_service.py` (masking + skor dua arah),
`app/comparison_view.py` (toggle + 4 mode tampilan), `app/comparison_worker.py`.

## Temuan pengukuran yang mengubah desain

Dugaan awal: bidang utama digambar dengan **garis lebih tebal**. Pengukuran pada
SU_057/SU_058 membantahnya:

| Aspek | Bidang utama | Peta dasar |
| --- | --- | --- |
| Median lebar stroke | 2.8 px | 2.0–4.0 px |
| Piksel tepi ≤ 128 grey | **94–100%** | 22–34% |
| Median intensitas tepi | ~102 | ~217 |

Jadi bidang utama **tidak lebih tebal**, tetapi jauh lebih **gelap dan konsisten**.
Deteksi karena itu memakai kegelapan, bukan ketebalan. Pendekatan berbasis erosi
(menghapus garis tipis) gagal: yang tersisa justru bingkai halaman, bukan bidang.

## Algoritma deteksi

1. Ambang tinta `200` → mask biner.
2. `MORPH_CLOSE` 5×5 agar outline yang putus karena scan tersambung.
3. `findContours(RETR_CCOMP)`, hanya kontur luar.
4. Saring: luas 0.5–15% halaman, dan bukan bingkai (tidak >85% lebar/tinggi halaman).
5. `approxPolyDP` dengan epsilon 2% perimeter → harus 4–8 sudut.
6. Sampel titik sepanjang setiap sisi; ambil **nilai tergelap** dalam jendela 5×5
   (inti stroke, bukan tepi kontur — versi awal salah di sini dan selalu mengukur
   ~1 px).
7. Syarat lolos: minimal 80% titik sampel ≤ 150 grey.
8. Keyakinan = `0.70 · darkness · shape + 0.30 · size`, dengan `size` menurun bila
   poligon terlalu kecil atau terlalu besar.

### Mengapa jendela 5×5

Titik kontur berada di **batas** tinta, sehingga `distanceTransform` di titik itu
selalu ≈ 1 px. Mengambil nilai maksimum dalam jendela kecil di sekitar titik
mengembalikan lebar stroke sebenarnya.

## Masking

- Hanya **garis** bidang yang di-mask (polyline tebal 9 px), bukan interiornya.
- Interior dibiarkan karena memuat label bidang (nomor SU) yang merupakan identitas
  dokumen; peta dasar di sekeliling juga harus tetap utuh (PRD F).
- Masking diterapkan pada **gambar sebelum ekstraksi fitur**, sehingga baik alignment
  maupun skor dihitung tanpa bidang utama.
- Cache fitur ikut dibuat untuk gambar ter-mask (kunci = hash gambar), jadi dua
  entri cache per dokumen — bukan bug.

## Dampak masking (diukur, bukan diasumsikan)

| Skenario | Tanpa masking | Dengan masking | Selisih |
| --- | --- | --- | --- |
| Uji terkontrol: peta dasar identik, bidang 516 px terpisah | 0.865 | **0.998** | **+0.133** |
| SU_057 vs SU_058 (bidang nyata relatif berdekatan) | 0.805 | 0.721 | −0.084 |

Pada uji terkontrol, 8.9% mask bidang B jatuh di atas bidang A setelah alignment,
jadi tanpa masking tinta bidang yang berbeda dihitung sebagai ketidakcocokan.

Pada pasangan referensi, tinta bidang = 10.7% dari total tinta dan sebagian besar
bertumpuk, sehingga skor **tanpa** masking justru terlalu tinggi. Kesimpulan:
angka dengan masking yang lebih dapat dipercaya karena hanya mengukur peta dasar.

Konsekuensi penting untuk Phase 5: skor kemiripan **harus** memakai angka
parcel-free, dan selisih terhadap angka mentah perlu dilaporkan sebagai indikator
tersendiri, bukan disembunyikan.

## Penanganan ketidakpastian

Sesuai PRD F: bila deteksi tidak yakin (`confidence < 0.50`), hasil ditandai
"perlu pemeriksaan" dan catatan ditampilkan di ringkasan. Bila bidang tidak
terdeteksi sama sekali, perbandingan tetap berjalan tanpa masking dan alasannya
dilaporkan.

## Batasan Phase 4

- Deteksi mengandalkan gambar vektor bersih; scan miring berat atau resolusi sangat
  rendah belum diuji.
- Bila bidang utama menempel/berbagi garis dengan bidang tetangga, poligon yang
  terbentuk bisa mencakup keduanya.
- Belum ada koreksi manual terhadap hasil deteksi (ditunda ke Phase 6).
- Belum ada penilaian akurasi pada dataset berlabel; ambang masih nilai awal dari
  dua dokumen.

## Pengujian Phase 4

| Berkas | Cakupan |
| --- | --- |
| `tests/test_parcel_detector.py` | Deteksi, penolakan bidang pucat/terbuka/bingkai/kecil, konfigurasi, masking, ringkasan |
| `tests/test_parcel_comparison.py` | Masking aktif/nonaktif, skor dua arah, catatan, uji terkontrol bidang berjauhan |

---

# Phase 5 — Similarity & Clustering

## Tujuan fase

Memberi skor kemiripan 0–100 untuk setiap pasangan peta dasar dan membentuk
kelompok, tanpa memaksa dokumen yang tidak punya pasangan masuk ke kelompok.

## Modul baru

| File | Tanggung jawab |
| --- | --- |
| `core/similarity_engine.py` | Skor 5 indikator, gerbang keras, prefilter |
| `core/clustering_engine.py` | Matriks kemiripan, graph-based grouping, ringkasan |
| `services/grouping_service.py` | Persiapan dokumen parcel-free, orkestrasi grouping |
| `app/grouping_worker.py` | Menjalankan grouping di QThread |
| `app/group_results_view.py` | Tab “Hasil Pengelompokan” |

## Indikator dan bobot

| Indikator | Bobot | Alasan |
| --- | --- | --- |
| Cakupan peta acuan | 0.45 | Satu-satunya yang menolak potongan halaman |
| Kualitas alignment | 0.20 | Menilai kecocokan geometris |
| Dukungan inlier | 0.15 | Menilai banyaknya bukti |
| Kemiripan struktur garis | 0.12 | Panjang garis Hough total |
| Sebaran inlier | 0.08 | Mencegah pola terulang di satu area saja |

Bobot dinormalisasi otomatis (`SimilarityWeights.normalised()`), sehingga
mengubahnya tidak merusak skala.

## Mengapa cakupan acuan dominan

Pengukuran negatif sulit memperlihatkan indikator geometris saja menyesatkan:

| Kasus | quality | inlier | ref_coverage |
| --- | --- | --- | --- |
| dokumen vs dirinya | 1.000 | 2000 | 1.000 |
| SU_057 vs SU_058 | 0.887 | 341 | 0.721 |
| hanya 30% halaman | **0.976** | 147 | **0.095** |
| hanya 70% halaman | **0.990** | 928 | **0.314** |
| peta vs admin | 0.000 | 0 | 0.000 |

Potongan halaman memberi alignment *lebih baik* daripada pasangan asli. Gerbang
keras `ref_coverage >= 0.40` dan `inlier >= 30` menolak kasus seperti itu.

Konsekuensi yang disadari: **halaman penuh vs halaman ter-crop** ditolak (skor 0),
karena crop memang bukan representasi peta yang lengkap. Crop-vs-crop yang setara
tetap mendapat skor 91.4, jadi perbandingan yang adil tidak dirugikan.

## Gerbang dan kategori

- Hard gate: `ref_coverage < 0.40` atau `inlier < 30` → skor 0, alasan dilaporkan.
- Kategori (PRD 9): ≥85 tinggi, 65–84 perlu pemeriksaan, <65 rendah.
- Penautan kelompok hanya pada pasangan **≥ 65**; skor lemah tidak pernah menautkan.

## Clustering

- **Average linkage (UPGMA)** di atas ambang taut — bukan single linkage.
- Skor minimum kelompok **hanya dihitung dari pasangan yang benar-benar bertaut**.
- Singleton tidak dipaksa berkelompok; masuk `ungrouped`.

### Perbaikan penting: chaining pada batch besar

Laporan pengguna: **118 PDF dengan peta berbeda semuanya masuk satu kelompok.**

Penyebabnya *single linkage* (connected components). Single linkage hanya butuh
**satu** pasangan perbatasan untuk menyatukan dua kelompok, sehingga rantai
antarpeta bertetangga menyeret seluruh batch menjadi satu KELOMPOK.

Direproduksi: 118 dokumen, 3 lokasi (40/40/38), pasangan asli 68–96, pasangan
tetangga 62–72 dengan peluang 10%.

| Strategi | Hasil |
| --- | --- |
| single linkage @65 (sebelumnya) | **1 kelompok berisi 118 dokumen** |
| complete linkage @65–78 | over-fragmentasi (30+ kelompok kecil) |
| **average linkage @65** | **3 kelompok: 40 / 40 / 38** |

Complete linkage ditolak karena memecah kelompok sah menjadi potongan kecil;
average linkage hanya butuh kesamaan **rata-rata** antar anggota, sehingga satu
jembatan tidak cukup untuk menggabungkan dua kelompok, tetapi kelompok asli yang
skornya tidak merata tetap utuh.

Diuji pada lima skenario (tetangga 60–74, pasangan asli 66–96, 3–6 lokasi, grup
kecil 2 dokumen berskor 77,2): average linkage @65 benar di semuanya, termasuk
mempertahankan grup kecil yang hanya berskor 77,2 — sementara menaikkan ambang ke
85 akan merusaknya.

Biaya: O(n³) dengan update Lance-Williams, terukur 3 ms untuk 118 dokumen,
31 ms untuk 300, dan 3,2 s untuk 999.

`ClusterConfig(linkage="single")` tetap tersedia untuk diagnosis/perbandingan.

## Prefilter: keterbatasan yang terdokumentasi

Prefilter (jumlah kecocokan ratio-test ≥ 12) diukur ~9 ms/pasangan vs ~14 ms untuk
alignment penuh. Tetapi **ia tidak memisahkan dua peta berbeda**:

| Pasangan | Kecocokan |
| --- | --- |
| SU_057 vs SU_058 (asli) | 463 |
| peta sintetis berbeda (seed 11 vs 77) | 576 |
| peta sintetis berbeda (seed 11 vs 99) | 469 |
| peta vs halaman kosong | 0 |

Karena keduanya sama-sama garis renggang, statistik corner-nya mirip. Prefilter
hanya membuang halaman kosong. Pemisahan nyata terjadi pada tahap skoring.

## Hasil pengelompokan pada set campuran

8 dokumen (2 asli + 3 varian rotasi/skala + 3 peta sintetis asing):

```
KELOMPOK_001: 5 dokumen (Sedang, min 68.4)
  SU_057, SU_058, SU_057_rot3, SU_058_rot-2, SU_057_scale
Belum terkelompok: ASING_1, ASING_2, ASING_3
Pasangan asli: 68.4–96.3   Pasangan asing: 0.0
```

## Batasan Phase 5

- Ambang 65/85 masih nilai awal; belum dikalibrasi pada dataset berlabel.
- Kelompok yang dinaikkan keyakinannya masih perlu review manual (Phase 6).
- Clustering memakai O(n²) pasangan. Untuk 100 dokumen terukur ~44 s prefilter +
  ~69 s jika semua pasangan di-align penuh; 999 dokumen belum layak tanpa
  pengelompokan awal berbasis fitur yang lebih selektif.
- Belum ada penyimpanan matriks kemiripan ke SQLite, jadi hasil hilang saat aplikasi
  ditutup.
- Belum ada ekspor laporan atau penyalinan file.

## Pengujian Phase 5

| Berkas | Cakupan |
| --- | --- |
| `tests/test_similarity.py` | Skor, band, bobot, gerbang cakupan, prefilter, ringkasan |
| `tests/test_clustering.py` | Ambang, transitivitas, kelompok terpisah, ungrouped, matriks, cancel |
| `tests/test_grouping_service.py` | Persiapan parcel-free, progres, kegagalan, grouping SU_057/SU_058 |
| `tests/test_group_results_view.py` | Pohon kelompok, ungrouped, rincian, sinyal, error |

---

# Phase 6 — Group Review & Export

## Tujuan fase

Memungkinkan pengguna mengoreksi hasil pengelompokan secara manual, lalu mengekspor
laporan dan menyalin dokumen ke folder kelompok — tanpa pernah menyentuh file asli.

## Modul baru

| File | Tanggung jawab |
| --- | --- |
| `core/review_model.py` | `ReviewState` immutable: move, merge, split, create, verify |
| `services/export_service.py` | Laporan CSV/XLSX, penyalinan file ke folder kelompok |

Perubahan: `app/group_results_view.py` (bar koreksi + tombol ekspor/salin),
`core/clustering_engine.py` (`SimilarityEdge.match_count` untuk kolom laporan).

## Model review

Semua operasi mengembalikan **state baru** (frozen dataclass), sehingga:

- Dokumen tidak pernah hilang atau terduplikasi.
- Perubahan bisa di-reset ke hasil otomatis kapan saja.
- Tidak ada state parsial bila sebuah operasi gagal.

| Operasi | Perilaku |
| --- | --- |
| `move_document(key, target)` | Pindah ke kelompok lain, atau ke ungrouped bila `None` |
| `merge_groups(ids, target)` | Gabung beberapa kelompok; id target dipertahankan |
| `split_document(key)` | Keluarkan dari kelompok (masuk ungrouped) |
| `create_group(keys)` | Bentuk kelompok baru; id = maksimum + 1 |
| `set_verified(id, bool)` | Tandai / batalkan verifikasi |
| `verify_all()` | Tandai semua terverifikasi |
| `reset(clustering)` | Kembali ke hasil otomatis |

### Aturan konsistensi

1. Kelompok yang kehilangan semua anggota **dihapus**.
2. Setiap perubahan anggota **membatalkan verifikasi** kelompok itu — hasil yang
   berubah tidak boleh tampak sudah diverifikasi.
3. `min_pair_score` dan `confidence` **dihitung ulang** dari skor pasangan yang
   benar-benar bertaut (≥ 65), bukan dari semua pasangan.

## Laporan (PRD 10A)

Kolom persis sesuai PRD, dalam urutan yang sama:

| Kolom | Sumber |
| --- | --- |
| Nama file | `PdfInfo.filename` |
| Nomor SU | Pola `SU_<angka>` pada nama file; kosong bila tidak cocok |
| Halaman peta | Jumlah halaman dokumen |
| ID kelompok | `KELOMPOK_xxx` atau `BELUM_TERKELOMPOK` |
| Jumlah pasangan cocok | `SimilarityEdge.match_count` pasangan terbaik |
| Skor kemiripan | Skor terbaik anggota di dalam kelompoknya |
| Confidence | Keyakinan kelompok |
| Status | `Terverifikasi` / `Perlu verifikasi` |

Nomor SU **tidak dikarang**: bila nama file tidak mengikuti pola `SU_057`,
kolomnya dikosongkan.

Format: XLSX (default, header tebal, lebar kolom diatur) atau CSV (delimiter `;`,
UTF-8 BOM agar Excel membaca karakter Indonesia dengan benar).

## Penyalinan file (PRD 10B)

- Operasi **COPY** (`shutil.copy2`), tidak pernah move/delete.
- Struktur: `KELOMPOK_001/`, `KELOMPOK_002/`, `BELUM_TERKELOMPOK/`.
- Nama folder disanitasi (`safe_folder_name`).
- Tanpa `overwrite`, file yang sudah ada **dilewati** dan dilaporkan, sehingga
  penyalinan berulang aman.
- Diuji: setelah penyalinan, isi file asli byte-identik
  (`test_copy_never_moves_originals`).

## Batasan Phase 6

- Belum ada penyimpanan ke SQLite: review hilang saat aplikasi ditutup (Phase 7).
- Belum ada ekspor PDF laporan (opsional pada PRD).
- Belum ada drag-and-drop untuk memindahkan dokumen antar kelompok; koreksi lewat
  tombol dan dropdown.
- Belum ada pengaturan threshold di UI (masih konstan di `config`/`ClusterConfig`).
- Penyalinan berjalan di thread UI; untuk ratusan file ini akan terasa lambat
  (Phase 7).

## Pengujian Phase 6

| Berkas | Cakupan |
| --- | --- |
| `tests/test_review_model.py` | Semua operasi review, integritas dokumen, imutabilitas, reset |
| `tests/test_export_service.py` | Kolom PRD, CSV/XLSX, nomor SU, penyalinan, file asli utuh |
| `tests/test_group_results_view.py` | Verifikasi, merge, split, reset, detail, regression seleksi |

---

# Phase 7 — Optimization

## Tujuan fase

Menjadikan aplikasi layak untuk batch 100–999 PDF: cache, background processing,
recovery, dan paket distribusi — tanpa mengubah hasil perhitungan.

## Profil sebelum optimasi (diukur, bukan diasumsikan)

| Tahap | Waktu |
| --- | --- |
| render 2 halaman (120 dpi) | 761 ms |
| deteksi halaman peta | 155 ms |
| normalisasi | 105 ms |
| deteksi bidang | 32 ms |
| **ORB (fitur)** | **27–31 ms** |
| `prepare_comparable` total | ~1.070 ms |

Kesimpulan penting: ORB bukan bottleneck. Render + deteksi + normalisasi mendominasi,
padahal hasilnya identik setiap kali untuk file yang sama.

## Cache analisis

`services/cache_service.py` menyimpan per dokumen:

- `meta.json` — skor deteksi per halaman, halaman peta, poligon bidang + keyakinan,
  versi skema, waktu dibuat.
- `normalized.png`, `masked.png` — gambar hasil normalisasi dan bebas bidang.

Kunci: `sha1(skema | sha256(file) | dpi | versi detektor | versi normalisasi | versi bidang)`.

Konsekuensi desain:

- Mengubah algoritma **apa pun** otomatis menggugurkan cache — tidak mungkin
  membandingkan peta hasil pipeline lama dengan pipeline baru secara diam-diam.
- Cache rusak (JSON/PNG tidak valid, skema berbeda) **dihapus lalu dihitung ulang**,
  bukan membuat aplikasi gagal.

### Hasil terukur

| Skenario | Sebelum | Sesudah |
| --- | --- | --- |
| 2 dokumen, run ulang | 2.573 ms | **170 ms** (15,2×) |
| per dokumen | ~1.270 ms | **85 ms** |
| grouping 2 dokumen (hangat) | 2,51 s | **0,35 s** (7,3×) |

Hasil identik diverifikasi: `coverage 0.720700` sama persis dengan/tanpa cache,
jumlah fitur dan label bidang sama.

Biaya penyimpanan: **166 KB/dokumen** → ~160 MB untuk 999 dokumen.

## Database

Skema sesuai PRD 11, di `database/db.py`:

- `documents`, `features`, `similarity`, `groups`, `group_members`.
- `PRAGMA foreign_keys = ON`, `journal_mode = WAL`.
- `original_path` UNIQUE → `upsert_document` memperbarui, bukan menduplikasi.
- Pasangan similarity selalu disimpan terurut (`document_a < document_b`) agar
  `UNIQUE (document_a, document_b)` efektif.
- `connect()` adalah context manager yang **rollback** saat ada error.

Deskriptor fitur tetap berupa file `.npz` (PRD mengizinkan bila lebih efisien).

## Background processing

`app/export_worker.py` menjalankan penulisan laporan dan penyalinan file di QThread
dengan sinyal progress per file. Sebelumnya penyalinan berjalan di UI thread dan
membekukan jendela untuk ratusan dokumen.

## Pemulihan kesalahan

| Skenario | Perilaku |
| --- | --- |
| PDF rusak | Dicatat per file, batch lanjut |
| Cache korup | Dihapus, dihitung ulang |
| Tulis database gagal | Dicatat sebagai warning, UI tetap jalan |
| Ekspor gagal | Pesan error, tidak ada file parsial |
| Worker dibatalkan | Pemeriksaan `isInterruptionRequested()` tiap item |

## Packaging

- `LandCADMapGrouper.spec` + `build_exe.bat`.
- **Bug ditemukan & diperbaiki:** build pertama menulis `data/` ke `_internal`,
  yang read-only saat terinstal. `config.py` sekarang memakai
  `sys.executable`'s parent saat `sys.frozen`.
- Bundle 363 MB → **314 MB** dengan membuang model ONNX `pymupdf-layout`
  (19 file, ~49 MB) yang tidak pernah dipakai lewat filter `a.datas`.
- Sisa ukuran didominasi `cv2.pyd` (86 MB) dan `_mupdf.pyd` (12 MB) — keduanya
  dipakai dan tidak bisa dibuang tanpa mengganti library.
- Exe diverifikasi **benar-benar dijalankan** (12 detik tanpa crash) dan membuat
  `data/{cache,logs,output}` + `landcad.db` di sebelah executable.

## Batasan Phase 7

- Cache belum punya batas ukuran/LRU; 999 dokumen ≈ 160 MB dan terus bertambah.
- Belum ada UI untuk membersihkan cache atau melihat statistiknya.
- Grouping masih O(n²) pasangan; cache mempercepat, tidak mengurangi jumlahnya.
- Database menyimpan metadata saja; riwayat sesi (review manual) belum dipulihkan
  saat aplikasi dibuka kembali.
- Bundle belum ditandatangani (code signing) dan belum ada installer.

## Pengujian Phase 7

| Berkas | Cakupan |
| --- | --- |
| `tests/test_cache_service.py` | Kunci, versi pipeline, round-trip, korupsi, clear/stats, speedup |
| `tests/test_database.py` | Skema, FK, upsert, features, similarity, groups, rollback |
| `tests/test_packaging.py` | Path frozen/normal, isi spec, build script, larangan jaringan & tulis file asli |

---

# Phase 8 — Penyempurnaan PRD

## Tujuan fase

Menutup tiga celah yang ditemukan saat audit terhadap PRD setelah Phase 7.
Tidak ada perubahan pada algoritma similarity maupun clustering.

## Celah yang ditutup

| Celah | Bukti sebelum perbaikan |
| --- | --- |
| Tabel `similarity`/`groups`/`group_members` tidak pernah diisi | hanya `db.upsert_document` dipanggil |
| Tidak ada UI ambang (PRD 9) | ambang hanya konstanta modul |
| Dashboard kurang 2 metrik (PRD 8.1) | tidak ada kartu kelompok & belum berkelompok |

## 1. Persistensi hasil pengelompokan

### Migrasi skema v1 → v2

`SCHEMA_VERSION = 2`. Perubahan: `similarity.match_count` (kolom PRD 10A).

`initialize()` membaca `PRAGMA user_version` lalu memanggil `_migrate()`:

- Kolom hanya ditambahkan bila memang belum ada (`PRAGMA table_info`), sehingga DB
  baru yang sudah memuat kolom dari `SCHEMA` tidak terkena `ALTER` ganda.
- Data lama tidak disentuh — hanya `ALTER TABLE ... ADD COLUMN`.

### Semantik penyimpanan

- Dokumen **tanpa** baris di `group_members` = belum terkelompok. Karena itu
  `ungrouped` tidak butuh tabel sendiri; saat memuat, himpunan itu dihitung ulang.
- `save_review_state()` menolak bila ada id yang sekaligus berkelompok dan belum
  terkelompok (`ValueError`) — menjaga integritas.
- `normalize_path_key()` menyamakan aturan path dengan `PdfInfo.key`
  (`os.path.normcase(resolve())`) agar hasil tersimpan bisa dipasangkan kembali
  pada Windows yang case-insensitive.

### Lapisan service

`services/persistence_service.py`:

| Fungsi | Perilaku |
| --- | --- |
| `save_review(db, state, documents)` | upsert dokumen → tulis groups/members/similarity. Kegagalan hanya di-log, tidak melempar |
| `load_review(db, only_within=folder)` | bangun ulang `ReviewState` **tanpa membuka PDF**, lewati path hilang, filter per folder |
| `clear_review(db)` | hapus groups/members/similarity, dokumen tetap |

Kelompok yang setelah pemuatan tersisa < 2 anggota dianggap tidak valid dan
dibubarkan menjadi `ungrouped`.

### Alur UI

1. Setiap perubahan review → sinyal `review_changed` → main window menyimpan.
2. Setelah scan selesai → `_offer_saved_review()` menawarkan pemuatan (dialog).
3. Tab Hasil Pengelompokan punya tombol **Muat Hasil Tersimpan** yang aktif bila
   DB memang berisi kelompok untuk folder tersebut.

## 2. Pengaturan ambang

`app/settings_store.py` memakai `QSettings(APP_ORGANIZATION, APP_NAME)` (Qt
bawaan, tanpa dependency baru). `AppSettings.validated()` menjepit nilai rentang
dan memaksa `high_confidence_threshold > link_threshold`.

`app/settings_view.py` menyediakan tab **Pengaturan**:

- Ambang taut 40–95, ambang keyakinan tinggi 41–100, ambang halaman peta 0.30–0.80.
- Prefilter on/off.
- Simpan / Kembalikan Default / Bersihkan Cache Analisis (dengan konfirmasi).
- Menolak simpan bila nilai tidak konsisten, dengan pesan.

Penerapan: `main_window` membangun `MapPageDetector(threshold=...)` dan
`ClusterConfig(...)` dari settings, lalu meneruskannya ke worker. Diuji dengan
`link_threshold=95`: pengelompokan menghasilkan **0 kelompok** — membuktikan nilai
benar-benar dipakai, bukan konstanta.

## 3. Dashboard

PRD 8.1: total PDF, berhasil dibaca, gagal dibaca, jumlah kelompok, dokumen belum
berkelompok, status proses. Dua kartu baru (`groups_card`, `ungrouped_card`)
diisi dari `group_view.current_review()` dan disegarkan lewat `review_changed`.

## Batasan Phase 8

- Catatan per kelompok (`ReviewedGroup.note`) tidak disimpan: PRD 11 tidak punya
  kolomnya.
- Hasil tersimpan bersifat "satu batch terakhir"; tidak ada riwayat beberapa sesi.
- Ambang belum dikalibrasi pada dataset berlabel (PRD 14) — UI hanya memberi akses
  untuk bereksperimen.

## Pengujian Phase 8

| Berkas | Cakupan |
| --- | --- |
| `tests/test_group_persistence.py` | Migrasi v1→v2 (data utuh), save/load round-trip, filter folder, file hilang, DB rusak |
| `tests/test_settings.py` | Default, round-trip QSettings, penjepitan, validasi UI, konfirmasi cache |
| `tests/test_dashboard.py` | Semua metrik PRD 8.1, nilai default, reset |

Total 463 test lolos pada fase ini.

---

# Phase 9 — Perbaikan Akurasi Pengelompokan

## Masalah

Pengguna melaporkan 118 PDF dengan peta berbeda tetap masuk satu kelompok.
Chaining sudah diperbaiki di Phase 5, jadi penyebabnya ada di **skor**, bukan
clustering.

## Diagnosis terukur (bukan dugaan)

Ditambahkan `core/diagnose_pair.py` yang memisahkan bukti menjadi **area pinggir**
(bingkai, panah utara, legenda, teks skala) dan **area isi peta**.

Hasil pada halaman nyata:

| Kasus | overall | **interior** | border | inlier isi | inlier pinggir |
| --- | --- | --- | --- | --- | --- |
| Peta **sama** (057 vs 058) | 72.1 | **82.5** | 66.2 | 135 | 206 |
| Peta **beda**, bingkai sama #1 | 63.5 | **5.8** | 100.0 | **0** | 789 |
| Peta **beda**, bingkai sama #2 | 65.9 | **1.8** | 100.0 | **0** | 786 |
| Peta beda tanpa bingkai | 0.0 | 0.0 | 0.0 | 0 | 0 |

Bukti paling jelas: peta berbeda mendapat **border 100.0 dengan 786 inlier di
pinggir tetapi 0 inlier di area isi**. Bingkai + panah utara + legenda identik di
semua dokumen, sehingga ORB mencocokkannya di setiap pasangan dan menaikkan skor
sekitar **+64 poin**.

Temuan kedua: **cakupan seluruh halaman menghukum rotasi**. Rotasi 8° menurunkan
`ref_cov` ke 0.53 padahal `interior_coverage` tetap 0.82.

## Perbaikan

**1. Bukti wajib dari area isi** (`core/similarity_engine.py`)

- Indikator dominan diganti dari `reference_coverage` → `interior_coverage`
  (bobot 0.45).
- Dukungan inlier memakai `interior_inliers`, bukan seluruh inlier.
- Gate keras baru: `interior_coverage ≥ 0.45` **dan** `interior_inliers ≥ 25`.
  Alasan blokir menyebut "kecocokan hanya di bingkai/legenda".

**2. Semantik ambang diperbaiki** (`core/clustering_engine.py`)

PRD 9 menyebut 65–84 = *"Perlu pemeriksaan"* — itu **kategori tampilan**, bukan
syarat gabung. Kode sebelumnya memakai 65 sebagai ambang gabung.

| Skor | Perilaku baru |
| --- | --- |
| ≥ 82 (`link_threshold`) | boleh bergabung menjadi kelompok |
| 65–81 (`candidate_threshold`) | **dilaporkan sebagai kandidat**, tidak digabung |
| < 65 | tidak berhubungan |

Validator menolak `candidate_threshold >= link_threshold`.

**3. Kandidat ditampilkan di UI**

Tab Hasil Pengelompokan menambah simpul **"Kandidat (perlu pemeriksaan)"** berisi
pasangan 65–81 — terlihat, dapat diperiksa, tetapi tidak pernah digabung otomatis.

## Hasil sesudah perbaikan

| Kasus | Sebelum | Sesudah |
| --- | --- | --- |
| Peta sama (057 vs 058) | 84.4 | **83.0** (tetap bergabung) |
| Peta sama, rotasi 4° | 78.3 | **82.4** (bergabung) |
| Peta sama, rotasi 8° | 70.6 | **85.2** (bergabung) |
| Peta beda, bingkai sama | 63.5–65.9 | **0.0** (ditolak) |
| Peta beda tanpa bingkai | 0.0 | 0.0 |

Rotasi kini stabil (79.9–85.2) karena tidak lagi dihukum oleh bingkai.

## Batasan Phase 9

- Ambang 82 adalah nilai konservatif dari pengukuran dua dokumen, **bukan hasil
  kalibrasi** dataset berlabel (PRD 14).
- Pasangan asli yang lebih lemah dari 82 akan menjadi kandidat, bukan kelompok.
  Ini disengaja (PRD 21: selalu sediakan pemeriksaan manual).
- Belum ada masking eksplisit elemen template; perbaikan dilakukan dengan
  mengabaikan area pinggir saat menilai, bukan menghapusnya dari gambar. Masking
  penuh tetap opsi bila sampel pengguna menunjukkan sisa gangguan.
- Grouping tetap O(n²).

## Pengujian Phase 9

| Berkas | Cakupan |
| --- | --- |
| `tests/test_region_similarity.py` | Pemisahan region, template isolation, kasus bingkai-sama-peta-beda, ketahanan rotasi |
| `tests/test_threshold_semantics.py` | 82 gabung, 65–81 kandidat, validasi, skenario 3 lokasi |
| `tests/test_chaining_regression.py` | Diperbarui ke rentang skor terukur |

Total 487 test lolos pada fase ini.

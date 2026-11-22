PRODUCT REQUIREMENTS DOCUMENT (PRD)

LandCAD Map Grouper

PDF Map Similarity & Automatic Grouping System

Versi: 1.0
Platform: Windows Desktop
Target pemrosesan: 100–999 file PDF per batch
Bahasa aplikasi: Indonesia
Mode pemrosesan: Lokal / Offline
Teknologi utama: Python, OpenCV, PySide6

---

1. Latar Belakang

Dalam pekerjaan pengolahan dokumen pertanahan, terdapat banyak Surat Ukur (SU) yang memiliki gambar peta dasar serupa, tetapi bidang tanah utama yang ditampilkan berbeda.

Contoh dokumen:

- SU_057.pdf
- SU_058.pdf

Kedua dokumen tersebut memperlihatkan karakteristik:

- Peta dasar dan lingkungan sekitarnya memiliki kemiripan.
- Susunan jalan dan bidang-bidang di sekitar relatif serupa.
- Bidang tanah utama yang digambarkan dengan garis lebih tebal berbeda.
- Identitas dan luas bidang berbeda.

Masalah utama: Mengelompokkan ratusan PDF berdasarkan kesamaan peta dasar tanpa menganggap perbedaan bidang utama sebagai perbedaan lokasi peta.

Saat ini, proses tersebut berpotensi membutuhkan pemeriksaan dokumen satu per satu.

2. Tujuan Produk

Membangun aplikasi desktop yang dapat:

1. Mengimpor ratusan file PDF sekaligus.
2. Mengidentifikasi halaman yang berisi gambar peta.
3. Mengenali kemiripan peta dasar antar dokumen.
4. Mengabaikan perbedaan bidang utama ketika membandingkan peta.
5. Mengelompokkan dokumen berdasarkan kemiripan peta.
6. Menampilkan hasil pengelompokan untuk diperiksa pengguna.
7. Mengekspor laporan hasil pengelompokan.
8. Membuat folder berdasarkan kelompok tanpa mengubah atau menghapus dokumen asli.

Tujuan akhir: Dokumen dengan peta dasar yang sama masuk dalam kelompok yang sama, walaupun bidang utama yang ditampilkan berbeda.

3. Ruang Lingkup

3.1 Fitur utama

- Import PDF secara batch.
- Preview halaman PDF.
- Deteksi halaman peta.
- Normalisasi gambar.
- Ekstraksi fitur visual.
- Pencocokan peta dasar.
- Deteksi dan pengabaian bidang utama.
- Penghitungan skor kemiripan.
- Automatic grouping.
- Manual merge dan split group.
- Laporan hasil.
- Ekspor dan penyalinan file ke folder kelompok.

3.2 Di luar ruang lingkup versi pertama

- Digitasi otomatis bidang tanah.
- Penentuan koordinat geografis.
- Penentuan luas bidang.
- Validasi legalitas dokumen.
- Integrasi langsung dengan database pertanahan.
- Google Sheets API.
- AI Agent atau layanan AI berbasis cloud.

4. Contoh Kasus Pengujian

Gunakan dua dokumen berikut sebagai contoh utama:

Dokumen| Karakteristik
SU_057.pdf| Peta dasar serupa, bidang utama berbeda, luas 274 m²
SU_058.pdf| Peta dasar serupa, bidang utama berbeda, luas 244 m²

Kedua dokumen diharapkan masuk dalam kelompok yang sama.

Sistem harus memprioritaskan kemiripan struktur peta dasar, bukan kemiripan seluruh gambar.

5. Teknologi

Komponen| Teknologi
Bahasa pemrograman| Python 3.11
Desktop GUI| PySide6
PDF processing| PyMuPDF
Image processing| OpenCV
Numerical processing| NumPy
Feature extraction| ORB
Image alignment| OpenCV
Image similarity| OpenCV / NumPy
Clustering| scikit-learn
Database lokal| SQLite
Testing| pytest
Packaging| PyInstaller

Semua proses dilakukan secara lokal. Tidak ada kewajiban mengirim dokumen ke server eksternal.

6. Arsitektur Sistem

Sistem terdiri atas beberapa komponen:

A. PDF Importer

Tanggung jawab:

- Memilih satu folder yang berisi PDF.
- Mendukung pemilihan banyak file.
- Membaca metadata dasar PDF.
- Memeriksa file rusak atau tidak dapat dibuka.
- Menyimpan daftar dokumen yang diproses.

B. Map Page Detector

Tanggung jawab:

- Membaca seluruh halaman PDF.
- Merender halaman menjadi gambar.
- Mengidentifikasi halaman yang kemungkinan berisi peta.
- Membedakan halaman administrasi dengan halaman gambar peta.
- Memberikan preview halaman yang dipilih.

Pada contoh SU, gambar peta berada di halaman kedua.

Jangan mengasumsikan seluruh dokumen selalu memiliki dua halaman. Halaman peta harus dapat ditemukan berdasarkan karakteristik visual.

C. Image Normalizer

Tanggung jawab:

- Mengubah gambar menjadi grayscale.
- Menyamakan resolusi pemrosesan.
- Memperbaiki kontras.
- Mengurangi noise.
- Mengidentifikasi dan memperbaiki kemiringan.
- Menangani rotasi.
- Menangani perbedaan ukuran dan crop.

Simpan gambar asli dan gambar hasil normalisasi secara terpisah.

D. Feature Extractor

Tanggung jawab:

- Mengekstrak fitur visual dari peta.
- Mengenali pola jalan.
- Mengenali garis batas bidang di sekitar.
- Mengenali persimpangan dan struktur visual lainnya.
- Menghasilkan deskriptor untuk proses pencocokan.

Gunakan ORB sebagai pendekatan awal karena relatif ringan dan dapat dijalankan secara offline.

E. Map Alignment Engine

Tanggung jawab:

- Mencocokkan fitur dua gambar.
- Menentukan pasangan titik yang sesuai.
- Menghitung transformasi gambar.
- Menyesuaikan rotasi, posisi, skala, dan perspektif jika memungkinkan.
- Memeriksa jumlah dan kualitas titik yang cocok.

Gunakan:

- BFMatcher.
- Lowe's ratio test.
- RANSAC.
- Affine transformation atau Homography sesuai kebutuhan.

Jangan memaksakan homography apabila hasil pencocokan tidak cukup kuat.

F. Main Parcel Suppression Engine

Ini merupakan salah satu komponen terpenting.

Masalahnya, bidang utama pada dokumen dapat memiliki garis lebih tebal atau lebih gelap dibandingkan garis peta dasar.

Sistem perlu mengidentifikasi kandidat bidang utama agar perbedaannya tidak terlalu memengaruhi perhitungan kemiripan.

Pendekatan awal:

1. Mengidentifikasi garis yang memiliki ketebalan atau intensitas berbeda.
2. Mendeteksi kontur tertutup.
3. Mencari kandidat poligon bidang utama.
4. Menganalisis posisi dan karakteristik garis.
5. Membuat mask pada area kandidat bidang utama.
6. Mengabaikan atau mengurangi bobot area tersebut ketika menghitung kemiripan.

Penting:

- Jangan menghapus semua garis gelap secara otomatis.
- Jalan dan batas bidang sekitar juga merupakan informasi penting.
- Masking harus mempertahankan informasi peta dasar di sekitar bidang utama.
- Apabila deteksi bidang utama tidak yakin, sistem harus menggunakan metode pembandingan alternatif dan menandai hasilnya untuk pemeriksaan.

G. Similarity Engine

Menghitung kemiripan dua peta berdasarkan beberapa indikator:

1. Jumlah feature matches.
2. Persentase inlier setelah RANSAC.
3. Kualitas alignment.
4. Kemiripan struktur garis.
5. Kemiripan pola jalan.
6. Kemiripan lingkungan bidang.
7. Konsistensi posisi fitur setelah alignment.

Hasilnya berupa skor kemiripan 0–100.

Bobot setiap indikator harus dapat dikonfigurasi dan diuji menggunakan dokumen nyata yang telah dianonimkan.

Jangan menggunakan satu indikator saja.

H. Clustering Engine

Tanggung jawab:

- Membentuk kelompok dokumen berdasarkan skor kemiripan.
- Menghindari penggabungan dokumen yang hanya memiliki sedikit kesamaan umum.
- Mendukung dokumen yang tidak memiliki pasangan.
- Menghasilkan kelompok dengan tingkat keyakinan.

Pendekatan awal:

- Pairwise similarity matrix.
- Agglomerative clustering atau graph-based clustering.
- Threshold yang dapat dikonfigurasi.
- Manual correction setelah pengelompokan.

Untuk ratusan dokumen, proses perbandingan perlu dioptimalkan. Gunakan fitur awal untuk menyaring pasangan kandidat sebelum melakukan alignment yang lebih mahal.

7. Alur Pemrosesan

START
  |
  v
Import Folder PDF
  |
  v
Validasi File
  |
  v
Identifikasi Halaman Peta
  |
  v
Render PDF ke Gambar
  |
  v
Image Normalization
  |
  v
Extract Visual Features
  |
  v
Identify Main Parcel Candidates
  |
  v
Generate Candidate Document Pairs
  |
  v
Align Map Images
  |
  v
Suppress Main Parcel Differences
  |
  v
Calculate Similarity Score
  |
  v
Clustering
  |
  v
Display Group Results
  |
  v
Manual Review
  |
  v
Export Report / Copy Files to Group Folders
  |
  v
END

8. Antarmuka Pengguna

Aplikasi menggunakan bahasa Indonesia.

8.1 Dashboard

Menampilkan:

- Total PDF.
- PDF berhasil dibaca.
- PDF gagal dibaca.
- Jumlah kelompok.
- Jumlah dokumen yang belum memiliki kelompok.
- Status proses.

8.2 Import Dokumen

Fitur:

- Pilih folder.
- Tambahkan PDF.
- Hapus file dari daftar pemrosesan tanpa menghapus file asli.
- Preview dokumen.
- Validasi file.

8.3 Processing Panel

Menampilkan tahapan:

- Membaca PDF.
- Mendeteksi halaman peta.
- Normalisasi gambar.
- Ekstraksi fitur.
- Pencocokan.
- Pengelompokan.

Tampilkan progress bar dan informasi file yang sedang diproses.

Pemrosesan harus berjalan di background thread agar aplikasi tidak freeze.

8.4 Group Results

Contoh tampilan:

KELOMPOK 001
Jumlah dokumen: 2

[Preview SU_057] [Preview SU_058]

SU_057.pdf
SU_058.pdf

Status: Perlu verifikasi pengguna

Fitur:

- Membuka preview dokumen.
- Membandingkan dua peta berdampingan.
- Menampilkan overlay hasil alignment.
- Menampilkan skor kemiripan.
- Memindahkan dokumen ke kelompok lain.
- Menggabungkan dua kelompok.
- Memisahkan dokumen dari kelompok.
- Menandai hasil sebagai sudah diverifikasi.

8.5 Unmatched Documents

Menampilkan PDF yang belum cocok dengan kelompok mana pun.

Dokumen tersebut tidak boleh dipaksakan masuk ke kelompok tertentu.

9. Pengaturan Threshold

Pengguna dapat mengatur ambang kemiripan.

Kategori tampilan awal:

Skor| Kategori tampilan
85–100| Kemiripan tinggi
65–84| Perlu pemeriksaan
Di bawah 65| Kemiripan rendah

Angka tersebut hanya nilai awal untuk eksperimen, bukan ambang akurasi yang sudah tervalidasi.

Skor harus dikalibrasi menggunakan dataset pengujian yang memiliki label pasangan benar dan salah.

Selain skor, sistem harus mempertimbangkan kualitas alignment dan jumlah fitur yang cocok.

10. Output

A. Laporan

Format:

- Excel (.xlsx) atau CSV.
- PDF laporan opsional.

Kolom laporan:

Kolom| Keterangan
Nama file| Nama dokumen
Nomor SU| Jika tersedia
Halaman peta| Halaman yang digunakan
ID kelompok| Kelompok hasil
Jumlah pasangan cocok| Jumlah pasangan yang terdeteksi
Skor kemiripan| Skor 0–100
Confidence| Tingkat keyakinan sistem
Status| Perlu verifikasi / terverifikasi

B. Folder hasil

Contoh:

OUTPUT/
├── KELOMPOK_001/
│   ├── SU_057.pdf
│   └── SU_058.pdf
│
├── KELOMPOK_002/
│   ├── SU_059.pdf
│   └── SU_061.pdf
│
└── BELUM_TERKELOMPOK/
    └── SU_060.pdf

Gunakan operasi COPY sebagai default. Jangan memindahkan atau menghapus dokumen asli tanpa persetujuan pengguna.

11. Database

Gunakan SQLite untuk menyimpan:

Tabel documents

- id
- filename
- original_path
- file_hash
- page_count
- map_page
- processing_status
- created_at

Tabel features

- id
- document_id
- feature_data_path
- feature_count
- preprocessing_version

Tabel similarity

- id
- document_a
- document_b
- similarity_score
- alignment_quality
- inlier_count
- review_status

Tabel groups

- id
- group_name
- created_at
- verification_status

Tabel group_members

- id
- group_id
- document_id

Simpan data fitur dalam bentuk file cache bila lebih efisien daripada menyimpan seluruh deskriptor dalam database.

12. Struktur Folder Proyek

LandCAD_Map_Grouper/
│
├── main.py
├── config.py
├── requirements.txt
├── README.md
│
├── app/
│   ├── main_window.py
│   ├── dashboard.py
│   ├── import_view.py
│   ├── processing_view.py
│   ├── group_result_view.py
│   ├── comparison_view.py
│   └── settings_view.py
│
├── core/
│   ├── pdf_reader.py
│   ├── map_page_detector.py
│   ├── image_normalizer.py
│   ├── feature_extractor.py
│   ├── parcel_detector.py
│   ├── image_alignment.py
│   ├── similarity_engine.py
│   └── clustering_engine.py
│
├── services/
│   ├── import_service.py
│   ├── processing_service.py
│   ├── grouping_service.py
│   └── export_service.py
│
├── database/
│   ├── db.py
│   └── models.py
│
├── utils/
│   ├── image_utils.py
│   ├── file_utils.py
│   └── logging_utils.py
│
├── data/
│   ├── cache/
│   ├── projects/
│   └── output/
│
├── tests/
│   ├── test_pdf_reader.py
│   ├── test_map_detection.py
│   ├── test_normalization.py
│   ├── test_alignment.py
│   ├── test_similarity.py
│   └── test_clustering.py
│
└── docs/
    ├── architecture.md
    └── user_guide.md

13. Non-Functional Requirements

Performance

- Target batch 100–999 PDF.
- Hindari pemrosesan gambar resolusi tinggi yang tidak diperlukan.
- Gunakan cache agar file yang sama tidak perlu diproses ulang.
- Gunakan candidate filtering untuk mengurangi jumlah perbandingan.
- Tampilkan progress pemrosesan.
- Sediakan pembatalan proses yang aman.

Reliability

- File PDF rusak tidak boleh menghentikan seluruh batch.
- Kegagalan satu dokumen dicatat secara terpisah.
- Proses dapat dilanjutkan setelah kegagalan jika memungkinkan.
- Jangan membuat kelompok berdasarkan skor yang tidak valid.

Security

- Semua dokumen diproses secara lokal.
- Tidak menggunakan API cloud.
- Tidak mengirim dokumen ke layanan pihak ketiga.
- Jangan menulis informasi pemilik tanah ke log yang tidak diperlukan.
- Dokumen asli tidak boleh dimodifikasi.

Usability

- Antarmuka sederhana.
- Bahasa Indonesia.
- Pengelompokan mudah diperiksa.
- Pengguna dapat mengoreksi hasil otomatis.

14. Testing dan Validasi

Gunakan contoh SU_057 dan SU_058 sebagai salah satu pengujian awal.

Buat dataset tambahan dengan variasi:

1. Peta dasar sama, bidang utama berbeda.
2. Peta dasar sama, tetapi rotasi berbeda.
3. Peta dasar sama, tetapi skala berbeda.
4. Peta dasar sama, tetapi posisi dan crop berbeda.
5. Peta dasar sama dengan kualitas scan berbeda.
6. Peta berbeda tetapi memiliki pola jalan yang mirip.
7. Peta berbeda dan tidak memiliki hubungan geografis.

Evaluasi:

- Precision pengelompokan.
- Recall pasangan yang seharusnya satu kelompok.
- False positive.
- False negative.
- Waktu pemrosesan.
- Keberhasilan mendeteksi halaman peta.

Jangan menetapkan klaim akurasi sebelum pengujian dataset dilakukan.

15. Kriteria Penerimaan

Versi pertama dianggap memenuhi kebutuhan apabila:

- Pengguna dapat mengimpor ratusan PDF.
- Sistem dapat mendeteksi halaman peta pada dokumen uji.
- Sistem dapat menangani perbedaan orientasi dan ukuran tertentu.
- Sistem dapat membandingkan peta dasar tanpa terlalu dipengaruhi bidang utama.
- SU_057 dan SU_058 dapat dikenali sebagai pasangan yang perlu dikelompokkan bersama.
- Dokumen yang tidak cocok dapat dipisahkan.
- Pengguna dapat mengoreksi hasil.
- Laporan dapat diekspor.
- File asli tidak berubah.

16. Tahapan Pengembangan

Phase 1 — PDF Importer & Viewer

- Import folder PDF.
- Render halaman.
- Preview halaman.
- Navigasi dokumen.
- Validasi file.

Phase 2 — Map Detection & Normalization

- Deteksi halaman peta.
- Grayscale.
- Denoising.
- Contrast enhancement.
- Deskew.
- Rotation handling.

Phase 3 — Feature Extraction & Alignment

- ORB.
- Feature matching.
- RANSAC.
- Image alignment.
- Overlay comparison.

Phase 4 — Main Parcel Detection

- Deteksi kandidat bidang utama.
- Deteksi garis tebal.
- Kontur dan poligon.
- Masking kandidat bidang.
- Manual correction jika diperlukan.

Phase 5 — Similarity & Clustering

- Similarity score.
- Candidate filtering.
- Pairwise comparison.
- Clustering.
- Unmatched documents.

Phase 6 — Group Review & Export

- Group result UI.
- Manual merge/split.
- Verification.
- Excel/CSV export.
- Copy files to folders.

Phase 7 — Optimization

- Cache.
- Background processing.
- Batch optimization.
- Error recovery.
- PyInstaller packaging.

17. Prinsip Pengembangan

18. Utamakan akurasi daripada kecepatan.
19. Jangan menganggap seluruh garis gelap sebagai bidang utama.
20. Jangan menganggap skor kemiripan sebagai bukti bahwa dua dokumen berasal dari lokasi yang sama.
21. Selalu sediakan pemeriksaan manual.
22. Jangan menghapus atau mengubah file asli.
23. Gunakan arsitektur modular.
24. Setiap phase harus memiliki pengujian.
25. Jangan lanjut ke phase berikutnya sebelum phase sebelumnya berjalan dan diverifikasi.

---
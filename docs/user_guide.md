# Panduan Pengguna — Phase 1 (PDF Importer & Viewer)

## 1. Menjalankan aplikasi

```powershell
.\.venv\Scripts\python.exe main.py
```

Jendela utama akan terbuka dengan daftar dokumen kosong dan pesan
"Belum ada dokumen".

## 2. Membuka folder PDF

1. Klik tombol **Buka Folder PDF** (atau tekan `Ctrl+O`).
2. Pilih folder yang berisi dokumen PDF.
3. Aplikasi memindai folder di background. Progress bar dan status bar menampilkan
   nama file yang sedang diperiksa.
4. Setelah selesai, daftar dokumen terisi dan kartu ringkasan diperbarui.

Subfolder **tidak** ikut dipindai pada versi ini.

## 3. Memahami daftar dokumen

| Kolom | Keterangan |
| --- | --- |
| No | Urutan tampilan |
| Nama File | Nama dokumen (klik header untuk urutkan) |
| Halaman | Jumlah halaman, `-` bila file gagal dibaca |
| Ukuran | Ukuran file |
| Status | Valid / Rusak / Terkunci / Tanpa halaman / Tidak ditemukan / Tidak dapat dibaca |

Baris dengan masalah ditampilkan berwarna merah. Arahkan kursor ke baris untuk
melihat tooltip berisi lokasi file dan keterangan kegagalan.

## 4. Mencari dokumen

Ketik di kotak pencarian kanan atas. Pencarian mencocokkan **nama file** dan **status**,
dengan beberapa kata dipisah spasi (semua kata harus cocok).

## 5. Pratinjau halaman

- Klik satu baris untuk memuat pratinjau halaman pertama.
- Tombol navigasi: `|<` pertama, `‹` sebelumnya, `›` berikutnya, `>|` terakhir.
- Kotak nomor halaman dapat diisi langsung.
- Keyboard: `PageDown` halaman berikutnya, `PageUp` halaman sebelumnya.

## 6. Zoom

- **Sesuaikan Lebar** — halaman mengikuti lebar panel pratinjau.
- Tombol `+` / `−` — zoom manual (25%–400%).
- `Ctrl` + scroll pada panel pratinjau — zoom masuk/keluar.

## 7. Memeriksa status file

Informasi status tampil di tiga tempat:

1. Kolom **Status** pada daftar.
2. Baris keterangan di bawah daftar (lokasi file, status, jumlah halaman).
3. Header panel pratinjau (jumlah halaman dan status).

Bila dokumen gagal dibaca, panel pratinjau menampilkan alasannya.

## 8. Mengeluarkan file dari daftar

Pilih baris, klik **Keluarkan dari Daftar**. File hanya dihapus dari daftar kerja
aplikasi — **file asli di disk tidak diubah atau dihapus**.
Gunakan **Muat Ulang** (`F5`) untuk memunculkannya kembali.

## 9. Mendeteksi halaman peta (Phase 2)

1. Setelah folder dimuat, klik **Deteksi Halaman Peta** (atau `Ctrl+D`).
2. Progress bar dan status bar menampilkan dokumen yang sedang dianalisis.
   Proses berjalan di background, jadi jendela tetap responsif.
3. Setelah selesai, dua kolom baru terisi:
   - **Halaman Peta** — nomor halaman peta, `Tidak ditemukan`, atau `Gagal`.
   - **Skor** — skor keyakinan 0.00–1.00 (semakin tinggi semakin mirip peta).
4. Kartu **Halaman Peta Ditemukan** menunjukkan rasio, mis. `2/2`.
5. Bila dokumen dipilih, panel pratinjau langsung membuka halaman peta.

## 10. Melihat hasil normalisasi

Pada panel pratinjau tersedia dua tombol tampilan:

- **Asli** — halaman PDF apa adanya.
- **Normalisasi** — halaman peta setelah grayscale, peningkatan kontras, denoise,
  koreksi rotasi 90°, dan deskew.

Tombol **Normalisasi** hanya aktif bila halaman yang sedang dilihat memang halaman peta.

Di bawah toolbar pratinjau, keterangan biru merangkum hasil, misalnya:
`Halaman peta: Halaman 2 • rotasi 90° diterapkan • tanpa deskew`

Rincian lengkap (skor per halaman, langkah normalisasi, sudut kemiringan) tampil
di blok keterangan di bawah daftar dokumen.

## 11. Memahami skor

| Skor | Arti praktis |
| --- | --- |
| ≥ 0.60 | Kuat: halaman hampir pasti peta |
| 0.45–0.59 | Lolos ambang, tapi periksa manual |
| < 0.45 | Dianggap bukan peta |

Ambang 0.45 masih nilai awal dan belum dikalibrasi pada dataset berlabel.
Selalu periksa hasilnya secara manual.

## 12. Membandingkan dua peta (Phase 3)

1. Jalankan **Deteksi Halaman Peta** terlebih dahulu. Tab **Perbandingan Peta**
   baru aktif setelah ada minimal dua dokumen dengan halaman peta ditemukan.
2. Buka tab **Perbandingan Peta**.
3. Pilih dokumen **A** (acuan) dan **B** (pembanding) pada dropdown.
   Gunakan **Tukar** untuk membalik peran keduanya.
4. Klik **Bandingkan**. Proses berjalan di background.
5. Pilih mode tampilan:
   - **Overlay** — biru = peta A, merah = peta B setelah diselaraskan,
     gelap = tinta yang berimpit. Inilah cara tercepat melihat seberapa cocok
     peta dasarnya.
   - **Berdampingan** — kedua peta bersebelahan tanpa overlay.

Ringkasan di bawah gambar menampilkan model transformasi, jumlah kecocokan fitur,
inlier, galat reproyeksi, kualitas alignment, dan tumpang tindih tinta.

### Membaca hasil

- **Jalan dan batas bidang yang berimpit gelap** → peta dasar cocok.
- **Kotak bidang utama terpisah** (biru vs merah) → normal, karena bidang utama
  memang berbeda. Fase ini sengaja menyelaraskan peta dasar, bukan bidang utama.
- **Kualitas alignment tinggi** hanya berarti kedua gambar bisa dipertemukan secara
  geometris. Ini **bukan** bukti dua dokumen berasal dari lokasi yang sama.
  Halaman administrasi dari dua dokumen berbeda pun bisa mendapat kualitas 0.92.
  Penilaian kemiripan akhir baru dibuat di Phase 5.

## 13. Mengabaikan bidang utama (Phase 4)

Aplikasi mendeteksi bidang tanah utama dan, secara default, **mengabaikannya** saat
membandingkan peta. Ini membuat perbedaan bidang (mis. 274 m² vs 244 m²) tidak
menurunkan penilaian kemiripan peta dasar.

**Tombol “Abaikan Bidang Utama”** di tab Perbandingan:

- **Aktif (default)** — garis bidang utama dihapus sebelum pencocokan. Skor yang
  ditampilkan adalah kesepakatan peta dasar saja.
- **Nonaktif** — bidang utama ikut dihitung. Gunakan untuk melihat pengaruhnya.

**Mode tampilan**:

| Mode | Isi |
| --- | --- |
| Overlay | Hasil dengan masking (garis bidang dihilangkan) |
| Berdampingan | Kedua peta utuh, bersebelahan |
| Overlay tanpa masking | Hasil bila bidang tidak diabaikan |
| Bidang utama | Menandai poligon bidang yang terdeteksi (garis merah) |

### Cara membaca selisih skor

Ringkasan menampilkan dua angka, misalnya:

```
Tumpang tindih tinta (peta dasar): 0.721
Pembanding tanpa masking: 0.805 (selisih -0.084; ...)
```

- **Selisih negatif** berarti tanpa masking skor **terlalu tinggi**, karena kedua
  bidang kebetulan bertumpuk dan kesamaannya ikut terhitung.
- **Selisih positif** berarti tanpa masking skor **terlalu rendah**, karena bidang
  berada di posisi berbeda dan perbedaannya dihitung sebagai ketidakcocokan.

Angka **dengan** masking yang sebaiknya dipakai, karena hanya mengukur peta dasar.

### Bila bidang tidak terdeteksi

Ringkasan menampilkan catatan seperti “Bidang utama tidak terdeteksi pada kedua peta;
tanpa masking”, dan perbandingan tetap berjalan. Bila keyakinan deteksi rendah,
muncul “Perlu pemeriksaan manual” — periksa mode **Bidang utama** untuk melihat
poligon yang terdeteksi.

## 14. Mengelompokkan dokumen (Phase 5)

1. Jalankan **Deteksi Halaman Peta** lebih dulu.
2. Buka tab **Hasil Pengelompokan**, klik **Kelompokkan**.
   Proses berjalan di background; status menampilkan kemajuan.
3. Pohon hasil menampilkan:
   - **KELOMPOK_001**, **KELOMPOK_002**, … beserta jumlah dokumen.
   - Anggota setiap kelompok.
   - Bagian **Belum Terkelompok**.

Klik dokumen mana pun untuk melihat rincian di panel kanan: kelompoknya, skor
pasangan terendah, keyakinan, daftar anggota, dan pasangan terukur teratas.
Mengklik anggota juga melompat ke tab Dokumen dengan baris tersebut terpilih.

### Warna dan keyakinan

| Warna | Arti |
| --- | --- |
| Hijau | Keyakinan Tinggi — semua pasangan dalam kelompok ≥ 85 |
| Jingga | Perlu pemeriksaan — skor terendah 65–84 |
| Merah | Belum terkelompok — tidak ada pasangan yang cukup mirip |

### Cara membaca skor

| Skor | Kategori | Tindakan |
| --- | --- | --- |
| 85–100 | Kemiripan tinggi | Kemungkinan besar satu kelompok |
| 65–84 | Perlu pemeriksaan | Verifikasi manual disarankan |
| < 65 | Kemiripan rendah | Dianggap tidak berhubungan |

Pada dokumen contoh, SU_057 dan SU_058 mendapat **84.4** sehingga masuk kelompok
yang sama tetapi ditandai **perlu verifikasi**.

### Hal yang perlu dipahami

- **Dokumen tidak dipaksa berkelompok.** Kalau tidak ada pasangan yang cukup mirip,
  dokumen masuk “Belum Terkelompok”.
- **Halaman penuh tidak dicocokkan dengan potongan halaman.** Skornya akan 0 karena
  potongan hanya menjelaskan sebagian kecil peta; ini disengaja agar potongan tidak
  salah dianggap peta yang sama.
- Ambang 65 dan 85 masih nilai awal dan belum dikalibrasi pada dataset berlabel.

## 15. Mengeluarkan file dari daftar

Pilih baris, klik **Keluarkan dari Daftar**. File hanya dihapus dari daftar kerja
aplikasi — **file asli di disk tidak diubah atau dihapus**.
Gunakan **Muat Ulang** (`F5`) untuk memunculkannya kembali.

## 16. Log

Aktivitas pencatatan tersimpan di `data/logs/landcad.log` (maksimum ~1 MB per file,
3 file rotasi). Log tidak memuat isi dokumen.

## 17. Batasan versi ini

- Belum ada koreksi manual kelompok (gabung/pisah) dan ekspor laporan (Phase 6).
- Belum ada penyalinan file ke folder kelompok.
- Hasil pengelompokan hilang saat aplikasi ditutup (belum disimpan ke database).
- Hasil deteksi bidang belum bisa dikoreksi manual.
- Orientasi 180° tidak dibedakan dari 0°.

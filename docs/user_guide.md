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
| 82–84 | Perlu pemeriksaan | Tetap digabung, verifikasi disarankan |
| 65–81 | **Kandidat** | **Tidak digabung** — muncul di simpul *Kandidat* |
| < 65 | Kemiripan rendah | Dianggap tidak berhubungan |

Pada dokumen contoh, SU_057 dan SU_058 mendapat **83** sehingga tetap masuk
kelompok yang sama dengan status perlu verifikasi.

### Kandidat vs kelompok

Sejak perbaikan akurasi, aplikasi **tidak lagi menggabungkan** pasangan yang
skornya 65–81. Pasangan seperti itu ditampilkan di simpul
**“Kandidat (perlu pemeriksaan)”** supaya Anda bisa memutuskan sendiri.

Alasannya: setiap halaman peta memiliki bingkai, panah utara, `SKALA 1 : 500`,
`PENJELASAN`, dan `batas tanah ini` yang **sama di semua dokumen**. Dulu
kecocokan elemen itu ikut dihitung sehingga peta berbeda bisa tampak mirip.
Sekarang hanya kecocokan di **area isi peta** yang dihitung sebagai bukti.

### Hal yang perlu dipahami

- **Dokumen tidak dipaksa berkelompok.** Kalau tidak ada pasangan yang cukup mirip,
  dokumen masuk “Belum Terkelompok”.
- **Satu pasangan mirip tidak cukup menggabungkan dua kelompok.** Pengelompokan
  memakai kemiripan rata-rata antar anggota, sehingga rantai peta yang saling
  bertetangga tidak lagi menyeret seluruh batch menjadi satu kelompok.
  (Sebelum perbaikan, 118 PDF dengan peta berbeda bisa masuk satu kelompok.)
- **Halaman penuh tidak dicocokkan dengan potongan halaman.** Skornya akan 0 karena
  potongan hanya menjelaskan sebagian kecil peta; ini disengaja agar potongan tidak
  salah dianggap peta yang sama.
- Ambang 65 dan 85 masih nilai awal dan belum dikalibrasi pada dataset berlabel.
- Bila hasilnya masih terlalu ketat atau terlalu longgar, gunakan **Koreksi**
  (gabung/pisah) — dan laporkan contohnya agar ambang bisa dikalibrasi.

## 15. Mengoreksi hasil pengelompokan (Phase 6)

Setelah pengelompokan selesai, bar **Koreksi** aktif:

| Tombol | Kegunaan |
| --- | --- |
| **Tandai Terverifikasi** | Menandai kelompok terpilih sudah dicek manusia |
| **Batalkan Verifikasi** | Mengembalikan status perlu verifikasi |
| **Gabungkan ke…** | Menggabungkan kelompok terpilih ke kelompok pada dropdown |
| **Pisahkan Dokumen** | Mengeluarkan dokumen terpilih dari kelompoknya |
| **Kembalikan Otomatis** | Membuang semua koreksi, kembali ke hasil mesin |

Petunjuk pemakaian:

1. Klik **baris kelompok** (mis. `KELOMPOK_001`) lalu pilih tindakan kelompok.
2. Klik **baris dokumen** lalu **Pisahkan Dokumen** untuk memisahkan satu dokumen.
3. Setiap perubahan otomatis membatalkan verifikasi kelompok tersebut — hasil yang
   berubah tidak boleh tampak sudah diverifikasi.

Kelompok terverifikasi ditandai **biru**. Dokumen yang dipisahkan masuk ke
**Belum Terkelompok** dan tidak pernah dipaksa kembali ke kelompok lain.

## 16. Mengekspor laporan

Klik **Ekspor Laporan**, pilih lokasi dan format:

| Format | Keterangan |
| --- | --- |
| `.xlsx` | Excel, header tebal, lebar kolom diatur |
| `.csv` | Dipisah titik koma, UTF-8, aman dibuka di Excel Indonesia |

Kolom laporan: Nama file, Nomor SU, Halaman peta, ID kelompok, Jumlah pasangan
cocok, Skor kemiripan, Confidence, Status.

Kolom **Nomor SU** hanya terisi bila nama file mengikuti pola `SU_057`. Sistem
tidak mengarang nomor.

## 17. Menyalin dokumen ke folder kelompok

Klik **Salin ke Folder**, pilih folder tujuan. Struktur yang dihasilkan:

```
<folder tujuan>/
├── KELOMPOK_001/
│   ├── SU_057.pdf
│   └── SU_058.pdf
└── BELUM_TERKELOMPOK/
```

Penting:

- Operasi **salin**, bukan pindah. File asli **tidak diubah atau dihapus**.
- File yang sudah ada di folder tujuan **dilewati**, jadi aman dijalankan berulang.
- Bila masih ada kelompok belum terverifikasi, aplikasi meminta konfirmasi dulu.

## 19. Kinerja dan cache (Phase 7)

Aplikasi menyimpan hasil analisis agar dokumen yang sama tidak dihitung ulang.

- Analisis pertama untuk sebuah dokumen memakan ~1 detik.
- Analisis kedua (cache hangat) hanya ~85 ms — **15× lebih cepat**.
- Cache disimpan di `data/cache/analysis/`, sekitar 166 KB per dokumen.

Cache otomatis **digugurkan** bila: isi PDF berubah, atau algoritma diperbarui
(versi detektor/normalisasi/bidang). Jadi hasil lama tidak pernah tercampur dengan
hasil baru.

Aplikasi juga menyimpan metadata ke `data/landcad.db` (SQLite) agar daftar dokumen
dan halaman peta tercatat.

### Kapan perlu membersihkan cache?

Bila folder `data/` terasa besar, Anda boleh menghapus isi
`data/cache/analysis/` saat aplikasi tertutup. Analisis berikutnya akan menghitung
ulang (lebih lambat sekali, lalu cepat lagi).

## 20. Hasil tersimpan (Phase 8)

Hasil pengelompokan dan koreksi manual kini **disimpan otomatis** ke database
(`data/landcad.db`) setiap kali berubah — setelah mengelompokkan, memverifikasi,
memisahkan, atau menggabungkan.

Cara memuatnya kembali:

- Saat membuka folder, aplikasi menanyakan **“Muat hasil pengelompokan tersimpan?”**
  bila menemukan hasil untuk dokumen di folder itu.
- Atau buka tab **Hasil Pengelompokan** lalu klik **Muat Hasil Tersimpan**.

Yang ikut dipulihkan: anggota kelompok, status verifikasi, dan skor pasangan.
Dokumen yang filenya sudah dihapus/dipindahkan akan dilewati dan dilaporkan.

## 21. Pengaturan ambang (Phase 8)

Tab **Pengaturan** memungkinkan mengubah:

| Pengaturan | Arti |
| --- | --- |
| Ambang gabung kelompok | Pasangan ≥ nilai ini boleh digabung (default 82) |
| Ambang keyakinan tinggi | Kelompok dengan skor terendah di atas nilai ini berstatus Tinggi |
| Ambang halaman peta | Skor minimal sebuah halaman agar dianggap halaman peta |
| Prefilter | Lewati pasangan yang jelas tidak cocok (menghemat waktu) |

Pasangan di antara 65 dan ambang gabung **tidak** digabung; mereka muncul sebagai
kandidat untuk diperiksa manual.

Tombol **Simpan** menerapkan, **Kembalikan Default** mengembalikan 82/85/0.45, dan
**Bersihkan Cache Analisis** menghapus cache gambar (analisis berikutnya lebih
lambat sekali).

Ambang keyakinan tinggi wajib lebih besar dari ambang taut; bila tidak, aplikasi
menolak menyimpan.

Perubahan ambang **tidak** otomatis menghitung ulang. Jalankan **Deteksi Halaman
Peta** (untuk ambang halaman) atau **Kelompokkan** (untuk ambang kemiripan) lagi.

## 22. Dashboard

Delapan kartu ringkasan: Total PDF, Berhasil Dibaca, Gagal Dibaca, Total Halaman,
Halaman Peta Ditemukan, **Jumlah Kelompok**, **Belum Berkelompok**, dan Status
Proses. Kartu kelompok ikut berubah saat Anda memisahkan atau menggabungkan
kelompok.

## 23. Menjalankan versi portable (executable)

Bila Anda menerima folder `LandCADMapGrouper`, cukup jalankan
`LandCADMapGrouper.exe`. Tidak perlu Python.
Folder `data\` (log, cache, database, output) dibuat otomatis di sebelah executable,
jadi letakkan folder aplikasi di lokasi yang dapat ditulis (mis. Desktop atau
`D:\Aplikasi\`), bukan di `Program Files` tanpa izin admin.

## 24. Mengeluarkan file dari daftar

Pilih baris, klik **Keluarkan dari Daftar**. File hanya dihapus dari daftar kerja
aplikasi — **file asli di disk tidak diubah atau dihapus**.
Gunakan **Muat Ulang** (`F5`) untuk memunculkannya kembali.

## 25. Log

Aktivitas pencatatan tersimpan di `data/logs/landcad.log` (maksimum ~1 MB per file,
3 file rotasi). Log tidak memuat isi dokumen.

## 26. Batasan versi ini

- Cache analisis belum punya batas ukuran otomatis (tombol bersihkan sudah ada di
  tab Pengaturan).
- Hasil tersimpan hanya untuk batch terakhir; belum ada riwayat beberapa sesi.
- Catatan per kelompok belum disimpan (tidak ada kolomnya di PRD 11).
- Pengelompokan masih membandingkan semua pasangan (O(n²)); cache mempercepat
  tiap perbandingan, bukan mengurangi jumlahnya.
- Ambang 82 adalah nilai konservatif dari pengukuran dua dokumen, belum
  dikalibrasi pada dataset berlabel (PRD 14). Pasangan asli yang lebih lemah dari
  82 akan muncul sebagai kandidat, bukan kelompok.
- Belum ada installer maupun tanda tangan digital untuk executable.
- Orientasi 180° tidak dibedakan dari 0°.

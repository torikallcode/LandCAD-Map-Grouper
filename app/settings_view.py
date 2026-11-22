from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.settings_store import (
    DEFAULT_SETTINGS,
    AppSettings,
    save_settings,
)
from utils.logging_utils import get_logger

logger = get_logger("settings_view")


class SettingsView(QWidget):
    """Threshold settings (PRD section 9) plus analysis-cache maintenance."""

    settings_changed = Signal(object)
    clear_cache_requested = Signal()

    def __init__(self, settings: AppSettings | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self.set_settings(settings or DEFAULT_SETTINGS)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        title = QLabel("Pengaturan Ambang")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        note = QLabel(
            "Nilai ini adalah titik awal eksperimen, bukan ambang akurasi yang "
            "sudah tervalidasi. Kalibrasi memerlukan dataset berlabel (PRD 14)."
        )
        note.setObjectName("legendLabel")
        note.setWordWrap(True)
        layout.addWidget(note)

        layout.addWidget(self._build_form())

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self._save_button = QPushButton("Simpan")
        self._save_button.clicked.connect(self._on_save)
        buttons.addWidget(self._save_button)

        self._reset_button = QPushButton("Kembalikan Default")
        self._reset_button.clicked.connect(self._on_reset)
        buttons.addWidget(self._reset_button)

        buttons.addSpacing(16)
        self._clear_cache_button = QPushButton("Bersihkan Cache Analisis")
        self._clear_cache_button.setToolTip(
            "Hapus gambar analisis tersimpan. Analisis berikutnya lebih lambat "
            "sekali, lalu cepat lagi."
        )
        self._clear_cache_button.clicked.connect(self._on_clear_cache)
        buttons.addWidget(self._clear_cache_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self._status_label = QLabel("")
        self._status_label.setObjectName("pageInfo")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)
        layout.addStretch(1)

    def _build_form(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("statCard")
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        form = QFormLayout(frame)
        form.setContentsMargins(14, 12, 14, 12)
        form.setSpacing(10)

        self._link_spin = QDoubleSpinBox()
        self._link_spin.setRange(60.0, 98.0)
        self._link_spin.setDecimals(1)
        self._link_spin.setSingleStep(1.0)
        self._link_spin.setSuffix(" / 100")
        self._link_spin.setToolTip(
            "Pasangan di bawah nilai ini tidak digabung. Rentang 65–81 hanya "
            "dilaporkan sebagai kandidat untuk diperiksa manual."
        )
        form.addRow("Ambang gabung kelompok:", self._link_spin)

        self._high_spin = QDoubleSpinBox()
        self._high_spin.setRange(61.0, 100.0)
        self._high_spin.setDecimals(1)
        self._high_spin.setSingleStep(1.0)
        self._high_spin.setSuffix(" / 100")
        self._high_spin.setToolTip(
            "Kelompok dengan skor terendah di atas nilai ini diberi keyakinan Tinggi"
        )
        form.addRow("Ambang keyakinan tinggi:", self._high_spin)

        self._map_spin = QDoubleSpinBox()
        self._map_spin.setRange(0.30, 0.80)
        self._map_spin.setDecimals(2)
        self._map_spin.setSingleStep(0.01)
        self._map_spin.setToolTip(
            "Skor minimal sebuah halaman agar dianggap halaman peta"
        )
        form.addRow("Ambang halaman peta:", self._map_spin)

        self._prefilter_check = QCheckBox("Lewati pasangan yang jelas tidak cocok (prefilter)")
        self._prefilter_check.setToolTip(
            "Hemat waktu pada batch besar. Prefilter hanya membuang halaman "
            "kosong; pemisahan sebenarnya tetap dilakukan oleh skor kemiripan."
        )
        form.addRow("", self._prefilter_check)
        return frame

    # --------------------------------------------------------------- public
    def set_settings(self, settings: AppSettings) -> None:
        self._link_spin.setValue(settings.link_threshold)
        self._high_spin.setValue(settings.high_confidence_threshold)
        self._map_spin.setValue(settings.map_page_threshold)
        self._prefilter_check.setChecked(settings.prefilter_enabled)

    def current_settings(self) -> AppSettings:
        return AppSettings(
            link_threshold=round(self._link_spin.value(), 1),
            high_confidence_threshold=round(self._high_spin.value(), 1),
            map_page_threshold=round(self._map_spin.value(), 2),
            prefilter_enabled=self._prefilter_check.isChecked(),
        )

    def set_status(self, message: str) -> None:
        self._status_label.setText(message)

    # ------------------------------------------------------------ internals
    def _on_save(self) -> None:
        settings = self.current_settings()
        if not settings.is_consistent:
            QMessageBox.warning(
                self,
                "Nilai tidak konsisten",
                "Ambang keyakinan tinggi harus lebih besar daripada ambang "
                "kemiripan (taut).",
            )
            return

        stored = save_settings(settings)
        self.set_settings(stored)
        self.set_status(
            f"Tersimpan. Ambang taut {stored.link_threshold:.1f}, "
            f"keyakinan tinggi {stored.high_confidence_threshold:.1f}. "
            "Jalankan ulang pengelompokan agar berlaku."
        )
        self.settings_changed.emit(stored)

    def _on_reset(self) -> None:
        stored = save_settings(DEFAULT_SETTINGS)
        self.set_settings(stored)
        self.set_status("Dikembalikan ke nilai default.")
        self.settings_changed.emit(stored)

    def _on_clear_cache(self) -> None:
        answer = QMessageBox.question(
            self,
            "Bersihkan cache analisis?",
            "Cache analisis akan dihapus. Dokumen dan hasil pengelompokan tidak "
            "terpengaruh, tetapi analisis berikutnya lebih lambat.\n\nLanjutkan?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.clear_cache_requested.emit()

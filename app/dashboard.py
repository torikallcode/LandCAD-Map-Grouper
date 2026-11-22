from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget


class StatCard(QFrame):
    def __init__(self, title: str, accent: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)

        self._value_label = QLabel("0")
        self._value_label.setObjectName("statValue")
        if accent:
            self._value_label.setStyleSheet(f"color: {accent};")

        title_label = QLabel(title)
        title_label.setObjectName("statTitle")
        title_label.setWordWrap(True)

        layout.addWidget(self._value_label)
        layout.addWidget(title_label)

    def set_value(self, value: int | str) -> None:
        self._value_label.setText(str(value))


class DashboardBar(QWidget):
    """Ringkasan angka sesuai PRD 8.1.

    PRD asks for: total PDF, berhasil dibaca, gagal dibaca, jumlah kelompok,
    dokumen belum berkelompok, dan status proses. "Total halaman" and "halaman
    peta ditemukan" are kept because they explain what the pipeline found.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.total_card = StatCard("Total PDF")
        self.valid_card = StatCard("Berhasil Dibaca", accent="#157347")
        self.invalid_card = StatCard("Gagal Dibaca", accent="#b22222")
        self.pages_card = StatCard("Total Halaman")
        self.map_card = StatCard("Halaman Peta Ditemukan", accent="#1f6feb")
        self.groups_card = StatCard("Jumlah Kelompok", accent="#1f6feb")
        self.ungrouped_card = StatCard("Belum Berkelompok", accent="#a06a14")
        self.status_card = StatCard("Status Proses", accent="#1f6feb")

        for card in (
            self.total_card,
            self.valid_card,
            self.invalid_card,
            self.pages_card,
            self.map_card,
            self.groups_card,
            self.ungrouped_card,
            self.status_card,
        ):
            layout.addWidget(card, 1)

    def update_summary(
        self,
        total: int,
        valid: int,
        invalid: int,
        pages: int,
        map_found: int,
        analysed: int,
        status: str,
        group_count: int = 0,
        ungrouped_count: int = 0,
    ) -> None:
        self.total_card.set_value(total)
        self.valid_card.set_value(valid)
        self.invalid_card.set_value(invalid)
        self.pages_card.set_value(pages)
        self.map_card.set_value(f"{map_found}/{analysed}" if analysed else "0")
        self.groups_card.set_value(group_count)
        self.ungrouped_card.set_value(ungrouped_count)
        self.status_card.set_value(status)

    def reset(self) -> None:
        self.update_summary(0, 0, 0, 0, 0, 0, "Siap")

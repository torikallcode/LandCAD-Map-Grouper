"""Dashboard must expose every metric PRD 8.1 asks for."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def dashboard(qapp):
    from app.dashboard import DashboardBar

    widget = DashboardBar()
    yield widget
    widget.deleteLater()


def _value(card) -> str:
    return card._value_label.text()


def test_prd_metrics_are_present(dashboard):
    """PRD 8.1: total, valid, invalid, groups, ungrouped, status."""
    for card in (
        dashboard.total_card,
        dashboard.valid_card,
        dashboard.invalid_card,
        dashboard.groups_card,
        dashboard.ungrouped_card,
        dashboard.status_card,
    ):
        assert card is not None


def test_update_sets_every_card(dashboard):
    dashboard.update_summary(
        total=118,
        valid=115,
        invalid=3,
        pages=230,
        map_found=110,
        analysed=115,
        status="Selesai",
        group_count=4,
        ungrouped_count=8,
    )

    assert _value(dashboard.total_card) == "118"
    assert _value(dashboard.valid_card) == "115"
    assert _value(dashboard.invalid_card) == "3"
    assert _value(dashboard.pages_card) == "230"
    assert _value(dashboard.map_card) == "110/115"
    assert _value(dashboard.groups_card) == "4"
    assert _value(dashboard.ungrouped_card) == "8"
    assert _value(dashboard.status_card) == "Selesai"


def test_group_counts_default_to_zero(dashboard):
    dashboard.update_summary(
        total=2, valid=2, invalid=0, pages=4, map_found=2, analysed=2, status="Siap"
    )
    assert _value(dashboard.groups_card) == "0"
    assert _value(dashboard.ungrouped_card) == "0"


def test_map_card_shows_dash_before_analysis(dashboard):
    dashboard.update_summary(
        total=2, valid=2, invalid=0, pages=4, map_found=0, analysed=0, status="Siap"
    )
    assert _value(dashboard.map_card) == "0"


def test_reset_clears_everything(dashboard):
    dashboard.update_summary(
        total=10, valid=9, invalid=1, pages=20, map_found=8, analysed=9,
        status="Selesai", group_count=2, ungrouped_count=1,
    )
    dashboard.reset()

    assert _value(dashboard.total_card) == "0"
    assert _value(dashboard.groups_card) == "0"
    assert _value(dashboard.ungrouped_card) == "0"
    assert _value(dashboard.status_card) == "Siap"

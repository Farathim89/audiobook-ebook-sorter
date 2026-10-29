"""The top tabs and the remembered column widths."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QTableWidget  # noqa: E402

from app.ui.columns import resizable_columns  # noqa: E402
from app.ui.sidebar import AppTabs  # noqa: E402

app = QApplication.instance() or QApplication([])


def test_a_click_on_the_tab_already_shown_leads_back_to_it():
    tabs = AppTabs()
    got = []
    tabs.currentIndexChanged.connect(got.append)
    tabs.setCurrentIndex(1)  # the window switched to Collection: no signal back
    assert got == [] and tabs.currentIndex() == 1
    tabs._group.button(1).click()  # Tools is shown, you click Collection again
    tabs._group.button(2).click()
    assert got == [1, 2]


def test_widths_are_saved_from_your_drags_only(tmp_path):
    store = QSettings(str(tmp_path / "w.ini"), QSettings.IniFormat)
    t = QTableWidget(3, 3)
    resizable_columns(t, "w", {0: 40, 1: 200}, lambda: store, stretch_last=True)
    t.resize(900, 300)
    t.show()
    app.processEvents()
    t.resize(500, 300)  # the window / splitter resizes: the stretched column follows - nothing saved
    app.processEvents()
    t.setColumnWidth(1, 120)
    assert store.value("w") is None
    store.setValue("w", [40, 300, 600])  # saved while the window was big
    t2 = QTableWidget(3, 3)
    resizable_columns(t2, "w", {}, lambda: store, stretch_last=True)
    assert (t2.columnWidth(0), t2.columnWidth(1)) == (40, 300) and t2.columnWidth(2) != 600
    t.close()

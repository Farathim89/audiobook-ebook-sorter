"""Colour themes. Every colour in the app comes from the current Palette."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Palette:
    bg: str
    panel: str
    field: str
    border: str
    text: str
    muted: str
    accent: str
    accent_dark: str
    selection: str
    alt_row: str
    on_accent: str  # text on accent-coloured buttons
    ready: str
    check: str
    problem: str
    review: str
    texture: str = ""  # 'cobblestone': a drawn texture behind the sidebar and pages


THEMES: dict[str, Palette] = {
    "Gold (dark)": Palette("#1b1b1f", "#24242a", "#2d2d34", "#3a3a42", "#e6e1d3", "#8a8577", "#d4af37", "#a8862a",
                           "#4a3f1c", "#28282f", "#111111", "#6fbf73", "#e0b050", "#e06c6c", "#6fa8dc"),
    "Midnight blue": Palette("#141a24", "#1b2330", "#232d3d", "#33405a", "#dfe6f0", "#7f8ba0", "#5fa8ff", "#3f7fd0",
                             "#22406b", "#1f2836", "#0b1220", "#6fcf8f", "#f0c060", "#ff7a7a", "#b48cff"),
    "Forest": Palette("#161d18", "#1d2620", "#253029", "#344437", "#e3ebe1", "#87978a", "#7fc97f", "#5a9e5a",
                      "#2f4a33", "#212b24", "#0f1a10", "#8fd694", "#e8c46a", "#ec7b6e", "#7fb8e6"),
    "Crimson": Palette("#1c1416", "#251a1d", "#2e2125", "#45313a", "#f0e2e4", "#9a8589", "#e0475f", "#b23349",
                       "#5a2430", "#2a1e21", "#ffffff", "#7bd389", "#f2c260", "#ff6b6b", "#79b0f0"),
    "Purple night": Palette("#17141f", "#1f1a2b", "#282236", "#3b3350", "#e8e3f3", "#8c84a3", "#b28cff", "#8a63d9",
                            "#3d2f63", "#221d30", "#120e1c", "#79d39a", "#f0c66a", "#ff7b8a", "#6fc3f0"),
    "Light": Palette("#f4f4f6", "#ffffff", "#ffffff", "#c9ccd3", "#1f2329", "#6b7280", "#b8860b", "#8a6508",
                     "#f3e3b5", "#f7f7f9", "#ffffff", "#2e8b3e", "#b7791f", "#c53030", "#2b6cb0"),
    "High contrast": Palette("#000000", "#000000", "#111111", "#ffffff", "#ffffff", "#cccccc", "#ffd700", "#ffd700",
                             "#444444", "#0d0d0d", "#000000", "#00ff66", "#ffd700", "#ff4040", "#40c0ff"),
    # bg, panel, field, border, text, muted, accent, accent_dark, selection, alt_row, on_accent,
    # ready, check, problem, review
    "Nord": Palette("#2e3440", "#3b4252", "#434c5e", "#4c566a", "#eceff4", "#a3abb9", "#88c0d0", "#5e81ac",
                    "#3f4f66", "#353c4a", "#2e3440", "#a3be8c", "#ebcb8b", "#bf616a", "#b48ead"),
    "Dracula": Palette("#21222c", "#282a36", "#343746", "#44475a", "#f8f8f2", "#9aa0c0", "#bd93f9", "#8b6cd6",
                       "#44395e", "#262833", "#21222c", "#50fa7b", "#f1fa8c", "#ff5555", "#8be9fd"),
    "Catppuccin Mocha": Palette("#1e1e2e", "#181825", "#313244", "#45475a", "#cdd6f4", "#9399b2", "#f5c2e7",
                                "#cba6f7", "#45375a", "#24243a", "#1e1e2e", "#a6e3a1", "#f9e2af", "#f38ba8", "#89b4fa"),
    "Gruvbox": Palette("#1d2021", "#282828", "#32302f", "#504945", "#ebdbb2", "#a89984", "#fabd2f", "#d79921",
                       "#4a3f1f", "#252322", "#1d2021", "#b8bb26", "#fe8019", "#fb4934", "#83a598"),
    "Solarized dark": Palette("#002b36", "#073642", "#0b3d4a", "#21505c", "#eee8d5", "#93a1a1", "#b58900", "#8a6800",
                              "#2a4a3a", "#03313c", "#002b36", "#859900", "#cb4b16", "#dc322f", "#268bd2"),
    "Monokai": Palette("#1e1f1c", "#272822", "#33342d", "#49483e", "#f8f8f2", "#a59f85", "#e6db74", "#b8ad4f",
                       "#4a4630", "#24251f", "#1e1f1c", "#a6e22e", "#fd971f", "#f92672", "#66d9ef"),
    "Ocean teal": Palette("#0f1b1e", "#14252a", "#1b3036", "#2a464d", "#dcecef", "#7e9ca2", "#3cc8c0", "#2a9a93",
                          "#1d4646", "#132327", "#071315", "#6fd38f", "#f0c060", "#ff7a7a", "#8fb8ff"),
    "Rose gold": Palette("#1d1719", "#261e21", "#30262a", "#463a3e", "#f3e6e8", "#a38f94", "#e8a49c", "#c07d74",
                         "#4f3437", "#231c1f", "#1d1719", "#8fd39a", "#f0c66a", "#ff6f7f", "#9fb8f0"),
    "Sepia (light)": Palette("#f4ecd8", "#fbf5e6", "#fffaf0", "#d8c9a8", "#3b2f22", "#7d6b55", "#a0522d", "#7a3d20",
                             "#ead7b5", "#f1e7d0", "#ffffff", "#4f7d32", "#a8741a", "#b33a2a", "#3e6a9e"),
    "OLED Black": Palette("#000000", "#0a0a0a", "#141414", "#262626", "#f2ead8", "#8c8678", "#e8a33d", "#b97d24",
                          "#2b2111", "#060606", "#000000", "#5fd17a", "#e8a33d", "#ff5c5c", "#6fb0ff"),
    "Cobblestone": Palette("#3b3b3d", "#454547", "#2e2e30", "#5c5c5f", "#ecebe6", "#a9a8a2", "#8fbf5a", "#6a9440",
                           "#4b5a3a", "#363638", "#1b2410", "#8fd36a", "#e6c15a", "#e8695f", "#7fb2e0",
                           texture="cobblestone"),
    "Wood": Palette("#2a1c13", "#352419", "#22170f", "#5a3e2a", "#f3e6d3", "#b49a7e", "#d9a45b", "#a87736",
                    "#5a3b20", "#2f2016", "#1d130b", "#9ccc6c", "#e6b84f", "#e87461", "#8ab4d8", texture="wood"),
    "Paper (light)": Palette("#eef0f3", "#ffffff", "#ffffff", "#cfd5dd", "#1d2733", "#667283", "#2f6fd6", "#2459ad",
                             "#d9e6fb", "#f6f8fa", "#ffffff", "#2b8a3e", "#b7791f", "#c92a2a", "#7048e8"),
}
DEFAULT_THEME = "Gold (dark)"

_current: Palette = THEMES[DEFAULT_THEME]


def current() -> Palette:
    return _current


def apply(name: str, app: QApplication | None = None) -> None:
    """Switch the whole app to theme `name` (unknown names fall back to the default)."""
    global _current
    _current = THEMES.get(name, THEMES[DEFAULT_THEME])
    app = app or QApplication.instance()
    if app is not None:
        app.setStyleSheet(stylesheet(_current))


def state_color(state: str) -> QColor:
    p = _current
    return QColor({"ready": p.ready, "check": p.check, "unknown": p.problem, "problem": p.problem,
                   "review": p.review}.get(state, p.muted))


STATE_SYMBOLS = {"ready": "✓", "check": "!", "problem": "✕", "unknown": "?", "review": "↪",
                 "sorted": "✓", "skipped": "–"}
_icons: dict[tuple, QIcon] = {}


def state_icon(state: str, size: int = 16) -> QIcon:
    """A round badge in the state's colour with a symbol, so states read at a glance (and without colour)."""
    key = (id(_current), state, size)
    if key not in _icons:
        scale = 2  # draw at 2x for crisp edges on high-DPI screens
        pix = QPixmap(size * scale, size * scale)
        pix.fill(Qt.transparent)
        pix.setDevicePixelRatio(scale)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        color = state_color(state)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QRectF(1, 1, size - 2, size - 2))
        font = QFont("Segoe UI Symbol")
        font.setBold(True)
        font.setPixelSize(int(size * 0.72))
        p.setFont(font)
        p.setPen(QColor(_current.bg) if color.lightness() > 110 else QColor("#ffffff"))
        p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, STATE_SYMBOLS.get(state, "•"))
        p.end()
        _icons[key] = QIcon(pix)
    return _icons[key]


def swatch(name: str, width: int = 150, height: int = 84) -> QPixmap:
    """A small picture of a theme: sidebar strip, title in the accent, text lines, status dots, a button."""
    t = THEMES[name]
    scale = 2
    pix = QPixmap(width * scale, height * scale)
    pix.setDevicePixelRatio(scale)
    pix.fill(QColor(t.bg))
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    if t.texture and (tex := texture_file(t.texture)):
        p.drawTiledPixmap(QRectF(0, 0, width, height), QPixmap(tex))
    p.fillRect(QRectF(0, 0, 34, height), QColor(t.panel))
    p.fillRect(QRectF(33, 0, 1, height), QColor(t.border))
    for i, c in enumerate((t.ready, t.check, t.problem, t.review)):
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c))
        p.drawEllipse(QRectF(11, 10 + i * 17, 11, 11))
    p.setBrush(QColor(t.accent))
    p.drawRoundedRect(QRectF(44, 10, width - 56, 7), 3, 3)
    for i, (c, w) in enumerate(((t.text, 0.85), (t.text, 0.65), (t.muted, 0.75))):
        p.setBrush(QColor(c))
        p.drawRoundedRect(QRectF(44, 26 + i * 11, (width - 56) * w, 5), 2, 2)
    dark = QColor(t.bg).lightness() < 128
    p.setBrush(QColor(_mix(t.accent, t.bg, 0.22) if dark else t.accent_dark))
    p.setPen(QColor(_mix(t.accent, t.bg, 0.65) if dark else t.accent))
    p.drawRoundedRect(QRectF(width - 62, height - 22, 50, 14), 4, 4)
    p.end()
    return pix


def texture_file(kind: str) -> str:
    """A drawn, tileable texture as an image file (the stylesheet needs a file). '' when unknown."""
    if kind == "wood":
        return _wood_texture()
    if kind != "cobblestone":
        return ""
    import random
    import tempfile
    from pathlib import Path
    path = Path(tempfile.gettempdir()) / "booksorter_cobblestone_v1.png"
    if path.is_file():
        return path.as_posix()
    rnd = random.Random(7)  # the same stones every time
    size, cell = 192, 32
    pix = QPixmap(size, size)
    pix.fill(QColor("#2a2a2c"))  # the mortar between the stones
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    for row in range(size // cell + 1):
        offset = (cell // 2) if row % 2 else 0
        for col in range(-1, size // cell + 1):
            w = cell - 3 + rnd.randint(-3, 3)
            h = cell - 4 + rnd.randint(-3, 2)
            x = col * cell + offset + rnd.randint(-2, 2) + 2
            y = row * cell + rnd.randint(-2, 2) + 2
            shade = rnd.randint(52, 74)
            base = QColor(shade, shade, shade + rnd.randint(0, 4))
            for dx in (-size, 0, size):  # draw wrapped copies so the tile repeats seamlessly
                for dy in (-size, 0, size):
                    rect = QRectF(x + dx, y + dy, w, h)
                    p.setPen(Qt.NoPen)
                    p.setBrush(base)
                    p.drawRoundedRect(rect, 9, 9)
                    p.setBrush(QColor(255, 255, 255, 14))  # light from the top left
                    p.drawRoundedRect(QRectF(rect.x() + 2, rect.y() + 2, rect.width() * 0.6, rect.height() * 0.35), 6, 6)
                    p.setBrush(QColor(0, 0, 0, 30))
                    p.drawRoundedRect(QRectF(rect.x() + 3, rect.bottom() - 6, rect.width() - 6, 4), 2, 2)
    for _ in range(900):  # a little grit
        g = rnd.randint(20, 110)
        p.setPen(QColor(g, g, g, rnd.randint(25, 70)))
        p.drawPoint(rnd.randint(0, size - 1), rnd.randint(0, size - 1))
    p.end()
    pix.save(str(path))
    return path.as_posix()


def _wood_texture() -> str:
    """Planks of warm wood with grain lines and a few knots, tileable (like a bookshelf)."""
    import math
    import random
    import tempfile
    from pathlib import Path
    path = Path(tempfile.gettempdir()) / "booksorter_wood_v1.png"
    if path.is_file():
        return path.as_posix()
    rnd = random.Random(11)
    width, height, plank = 256, 256, 64
    pix = QPixmap(width, height)
    pix.fill(QColor("#3a2618"))
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    for i in range(height // plank):
        y0 = i * plank
        base = QColor(rnd.randint(70, 86), rnd.randint(46, 56), rnd.randint(30, 36))
        p.fillRect(QRectF(0, y0, width, plank), base)
        phase, amp = rnd.uniform(0, 6.28), rnd.uniform(1.5, 4.0)
        for line in range(26):  # the grain: wavy lines that wrap around the tile width
            y = y0 + 2 + line * (plank - 4) / 26
            shade = rnd.randint(-22, 14)
            c = QColor(max(0, base.red() + shade), max(0, base.green() + shade), max(0, base.blue() + shade // 2), 150)
            p.setPen(c)
            prev = None
            for x in range(0, width + 1, 4):
                yy = y + amp * math.sin(2 * math.pi * x / width * 2 + phase + line * 0.35)
                if prev:
                    p.drawLine(int(prev[0]), int(prev[1]), x, int(yy))
                prev = (x, yy)
        for _ in range(rnd.randint(0, 2)):  # a knot now and then
            kx, ky = rnd.randint(20, width - 20), y0 + rnd.randint(16, plank - 16)
            for r in range(9, 1, -2):
                p.setPen(QColor(40, 24, 14, 120))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QRectF(kx - r * 1.6, ky - r, r * 3.2, r * 2))
        p.fillRect(QRectF(0, y0 + plank - 2, width, 2), QColor(20, 12, 7))  # the gap between planks
        p.fillRect(QRectF(0, y0, width, 1), QColor(255, 220, 180, 25))
    p.end()
    pix.save(str(path))
    return path.as_posix()


def _mix(a: str, b: str, t: float) -> str:
    """a blended into b: t=0 -> b, t=1 -> a."""
    ca, cb = QColor(a), QColor(b)
    return QColor(round(ca.red() * t + cb.red() * (1 - t)), round(ca.green() * t + cb.green() * (1 - t)),
                  round(ca.blue() * t + cb.blue() * (1 - t))).name()


def _primary(p: Palette) -> str:
    """The main buttons (Sort, Save...). Dark themes: a dark gold-tinted fill with gold text - a solid
    bright fill glares there; it lights up on hover. Light themes: the solid accent."""
    if QColor(p.bg).lightness() < 128:
        return (f"QPushButton#primary {{ background: {_mix(p.accent, p.bg, 0.22)}; color: {p.accent}; "
                f"border: 1px solid {_mix(p.accent, p.bg, 0.65)}; font-weight: bold; }}\n"
                f"QPushButton#primary:hover {{ background: {_mix(p.accent, p.bg, 0.38)}; color: {p.accent}; "
                f"border-color: {p.accent}; }}\n"
                f"QPushButton#primary:pressed {{ background: {_mix(p.accent, p.bg, 0.5)}; }}\n"
                f"QPushButton#primary:disabled {{ background: {p.field}; color: {p.muted}; border-color: {p.border}; }}")
    return (f"QPushButton#primary {{ background: {p.accent_dark}; color: {p.on_accent}; border-color: {p.accent}; "
            f"font-weight: bold; }}\n"
            f"QPushButton#primary:hover {{ background: {p.accent}; color: {p.on_accent}; }}")


def stylesheet(p: Palette) -> str:
    primary = _primary(p)
    texture = texture_file(p.texture)
    textured = (f"QFrame#sidebar, QFrame#sidebar QWidget#sidebody, QFrame#sidebar QWidget#brand, "
                f"QScrollArea#sidescroll, QWidget#toolspage {{ background-color: {p.panel}; "
                f"background-image: url({texture}); }}\n"
                f"QFrame#sidebar QLabel, QWidget#toolspage QLabel {{ background: transparent; }}") if texture else ""
    dark = QColor(p.bg).lightness() < 128
    soft = _mix(p.accent, p.bg, 0.28) if dark else p.accent_dark  # selected badge / switch: calm in dark themes
    soft_text = p.accent if dark else p.on_accent
    soft_border = _mix(p.accent, p.bg, 0.6) if dark else p.accent
    return f"""
QWidget {{ background: {p.bg}; color: {p.text}; font-size: 10pt; }}
QMainWindow::separator {{ background: {p.border}; width: 1px; }}
QToolBar {{ background: {p.panel}; border: none; border-bottom: 1px solid {p.border}; spacing: 6px; padding: 4px; }}
QToolButton, QPushButton {{
    background: {p.field}; border: 1px solid {p.border}; border-radius: 6px; padding: 6px 14px; color: {p.text};
}}
QToolButton:hover, QPushButton:hover {{ border-color: {p.accent}; color: {p.accent}; }}
QToolButton:pressed, QPushButton:pressed {{ background: {p.border}; }}
QToolButton:checked {{ border-color: {p.accent}; color: {p.accent}; background: {p.selection}; }}
QToolButton:disabled, QPushButton:disabled {{ color: {p.muted}; border-color: {p.border}; }}
{primary}
QLineEdit, QComboBox, QPlainTextEdit, QSpinBox {{
    background: {p.field}; border: 1px solid {p.border}; border-radius: 6px; padding: 5px 8px;
    selection-background-color: {p.accent_dark}; selection-color: {p.on_accent};
}}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QSpinBox:focus {{ border-color: {p.accent}; }}
QComboBox {{ combobox-popup: 0; }}  /* the list drops DOWN under the box (not over it) */
QComboBox QAbstractItemView {{ background: {p.field}; selection-background-color: {p.selection}; selection-color: {p.text};
    border: 1px solid {p.accent_dark}; outline: none; padding: 2px; }}
QComboBox QAbstractItemView::item {{ min-height: 24px; padding: 2px 8px; }}
QTableView, QListWidget, QTableWidget {{
    background: {p.panel}; alternate-background-color: {p.alt_row}; border: 1px solid {p.border};
    gridline-color: {p.border}; selection-background-color: {p.selection}; selection-color: {p.text};
}}
QHeaderView::section {{
    background: {p.field}; color: {p.accent}; border: none; border-right: 1px solid {p.border};
    border-bottom: 1px solid {p.border}; padding: 4px 6px; font-weight: bold;
}}
QTabWidget::pane {{ border: 1px solid {p.border}; border-radius: 4px; top: -1px; padding: 8px; }}
QTabBar::tab {{
    background: {p.field}; color: {p.muted}; border: 1px solid {p.border}; border-bottom: none;
    padding: 6px 16px; margin-right: 2px; border-top-left-radius: 4px; border-top-right-radius: 4px;
}}
QTabBar::tab:selected {{ background: {p.panel}; color: {p.accent}; font-weight: bold; }}
QTabBar::tab:hover:!selected {{ color: {p.text}; }}
QGroupBox {{ border: 1px solid {p.border}; border-radius: 10px; margin-top: 14px; padding-top: 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {p.accent}; font-weight: bold; }}
QLabel#heading {{ color: {p.accent}; font-size: 12pt; font-weight: bold; }}
QLabel#muted {{ color: {p.muted}; }}
QLabel#notes {{ color: {p.check}; }}
QLabel#cover {{ border: 1px solid {p.border}; border-radius: 4px; color: {p.muted}; }}
QProgressBar {{ background: {p.field}; border: 1px solid {p.border}; border-radius: 5px; }}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 4px; }}
QStatusBar {{ background: {p.panel}; border-top: 1px solid {p.border}; }}
QMenuBar {{ background: {p.panel}; border-bottom: 1px solid {p.border}; padding: 2px 4px; }}
QMenuBar::item {{ background: transparent; padding: 4px 10px; border-radius: 3px; }}
QMenuBar::item:selected {{ background: {p.selection}; color: {p.accent}; }}
QMenu {{ background: {p.panel}; border: 1px solid {p.border}; padding: 4px 0; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 4px 10px; }}
QMenu::item:disabled {{ color: {p.muted}; }}
QMenu::indicator {{ width: 14px; height: 14px; margin-left: 6px; }}
QMenu::item {{ padding: 5px 18px; }}
QMenu::item:selected {{ background: {p.selection}; color: {p.text}; }}
QCheckBox::indicator, QTableView::indicator {{ width: 14px; height: 14px; }}
QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{ border: 1px solid {p.muted}; background: {p.field}; border-radius: 3px; }}
QCheckBox::indicator:hover {{ border-color: {p.accent}; }}
QCheckBox::indicator:checked {{ background: {p.accent}; border-color: {p.accent}; }}
QCheckBox::indicator:disabled {{ border-color: {p.border}; }}
QScrollBar:vertical {{ background: {p.panel}; width: 10px; }}
QScrollBar:horizontal {{ background: {p.panel}; height: 10px; }}
QScrollBar::handle {{ background: {p.border}; border-radius: 4px; min-height: 24px; min-width: 24px; }}
QScrollBar::handle:hover {{ background: {p.accent_dark}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QSplitter::handle {{ background: {p.border}; }}
QToolTip {{ background: {p.field}; color: {p.text}; border: 1px solid {p.accent_dark}; padding: 4px; }}

/* -- sidebar ------------------------------------------------------------ */
QFrame#sidebar {{ background: {p.panel}; border: none; border-right: 1px solid {p.border}; }}
QFrame#sidebar QWidget#sidebody, QFrame#sidebar QWidget#brand, QScrollArea#sidescroll {{ background: {p.panel}; }}
QWidget#brand {{ border-bottom: 1px solid {p.border}; }}
QLabel#brandtitle {{ color: {p.accent}; font-size: 13pt; font-weight: bold; letter-spacing: 2px; background: transparent; }}
QLabel#brandsub {{ color: {p.muted}; font-size: 8pt; background: transparent; }}
QLabel#navgroup {{ color: {p.muted}; font-size: 8pt; font-weight: bold; letter-spacing: 1px; padding: 2px 6px 1px 6px;
                   background: transparent; }}
QPushButton#nav, QPushButton#navplain {{
    background: transparent; border: none; border-left: 3px solid transparent; border-radius: 6px;
    text-align: left; padding: 3px 12px; color: {p.text};
}}
QPushButton#nav:hover, QPushButton#navplain:hover {{ background: {p.field}; color: {p.accent}; }}
QPushButton#nav:checked {{ background: {p.selection}; border-left: 3px solid {p.accent}; }}
QLabel#navtext {{ color: {p.text}; font-weight: normal; }}
QLabel#navtext[on="true"] {{ color: {p.accent}; font-weight: bold; }}
QPushButton#navplain:disabled {{ color: {p.muted}; }}
QLabel#navtext, QLabel#navicon {{ background: transparent; }}
QLabel#badge {{ background: {p.field}; color: {p.muted}; border-radius: 9px; padding: 1px 8px; font-size: 8pt;
               min-width: 14px; }}
QLabel#badge[on="true"] {{ background: {soft}; color: {soft_text}; }}
QPushButton#segfirst, QPushButton#segmid, QPushButton#seglast {{ background: {p.field}; border: 1px solid {p.border}; padding: 2px 8px; color: {p.muted}; border-radius: 0; }}
QPushButton#pilltoggle {{ background: {p.field}; border: 1px solid {p.border}; border-radius: 7px; padding: 2px 6px; color: {p.muted}; }}
QPushButton#pilltoggle:checked {{ background: {soft}; color: {soft_text}; border-color: {soft_border}; font-weight: bold; }}
QPushButton#pilltoggle:hover:!checked {{ color: {p.accent}; }}
QPushButton#segfirst {{ border-top-left-radius: 7px; border-bottom-left-radius: 7px; }}
QPushButton#seglast {{ border-top-right-radius: 7px; border-bottom-right-radius: 7px; border-left: none; }}
QPushButton#segfirst:checked, QPushButton#segmid:checked, QPushButton#seglast:checked {{ background: {soft}; color: {soft_text}; border-color: {soft_border}; font-weight: bold; }}
QPushButton#segfirst:hover:!checked, QPushButton#segmid:hover:!checked, QPushButton#seglast:hover:!checked {{ color: {p.accent}; }}
QScrollArea#details {{ border: none; }}
QFrame#sidebar QPushButton#primary {{ border-radius: 8px; font-size: 10.5pt; }}
QPushButton#sideaction {{ background: {p.field}; border: 1px solid {p.border}; border-radius: 8px; padding: 4px 12px; text-align: left; color: {p.text}; }}
QPushButton#sideaction:hover {{ border-color: {p.accent}; color: {p.accent}; background: {p.selection}; }}
QPushButton#sideaction:pressed {{ background: {p.border}; }}
QPushButton#sideaction:disabled {{ color: {p.muted}; border-color: {p.border}; background: {p.panel}; }}

/* -- pages & cards ---------------------------------------------------------- */
QWidget#toolspage {{ background: {p.bg}; }}
QLabel#pagetitle {{ color: {p.accent}; font-size: 16pt; font-weight: bold; }}
QListWidget#sidemenu {{ background: {p.panel}; border: 1px solid {p.border}; border-radius: 10px; padding: 6px;
                        outline: none; }}
QListWidget#sidemenu::item {{ padding: 7px 8px; border-radius: 6px; color: {p.text}; }}
QListWidget#sidemenu::item:disabled {{ color: {p.muted}; padding-top: 12px; }}
QListWidget#sidemenu::item:hover:!disabled {{ background: {p.field}; color: {p.accent}; }}
QListWidget#sidemenu::item:selected {{ background: {p.selection}; color: {p.accent}; font-weight: bold; }}
QFrame#card {{ background: {p.panel}; border: 1px solid {p.border}; border-radius: 10px; }}
QFrame#card:hover {{ border-color: {p.accent_dark}; }}
QFrame#card QLabel {{ background: transparent; }}
QLabel#cardtitle {{ color: {p.text}; font-size: 11pt; font-weight: bold; }}
QToolButton#themetile {{ background: {p.panel}; border: 2px solid {p.border}; border-radius: 10px; padding: 6px; }}
QToolButton#themetile:hover {{ border-color: {p.accent_dark}; color: {p.accent}; }}
QToolButton#themetile:checked {{ border-color: {p.accent}; color: {p.accent}; font-weight: bold; background: {p.selection}; }}
QLineEdit#search {{ border-radius: 16px; padding: 6px 14px; }}
QTableView {{ border-radius: 8px; }}
QTableView::item {{ padding: 2px 6px; border: none; }}
QTableView::item:selected {{ background: {p.selection}; color: {p.text}; }}
QTableView::indicator {{ border: 1px solid {p.muted}; background: {p.field}; border-radius: 3px; }}
QTableView::indicator:checked {{ background: {p.accent}; border-color: {p.accent}; }}
QTableView::indicator:hover {{ border-color: {p.accent}; }}\n{textured}
"""

"""The bitwise0x.com dark palette: near-black green-tinted surfaces with the sodium amber signal color.

Fusion style replaces the native macOS widgets, which would otherwise draw checkboxes, combo arrows,
focus rings and selections in the system accent (blue by default)."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

BG = "#0c0f0e"
SURFACE = "#111513"
FIELD = "#161b19"
RAISED = "#1b221f"
LINE = "#1f2623"
LINE_STRONG = "#2e3733"
FG = "#e7ece8"
FG_SOFT = "#d4dad6"
MUTED = "#a2aaa6"
DIM = "#6b7470"
SIGNAL = "#efb146"
SIGNAL_DIM = "#a96a19"
SIGNAL_LIGHT = "#f7d08a"  # setting names in the help text, a lighter amber than headings
ON_SIGNAL = "#0c0f0e"

# log line colors
MATCH = "#4cc38a"
REVIEW = "#f0883e"
ERROR = "#ef5f64"

def stylesheet(check_icon: str) -> str:
    return f"""
QWidget {{ color: {FG}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QLabel {{ background: transparent; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextBrowser {{
    background: {FIELD}; border: 1px solid {LINE_STRONG}; border-radius: 6px; padding: 4px 6px;
    selection-background-color: {SIGNAL}; selection-color: {ON_SIGNAL};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {SIGNAL}; }}
QPlainTextEdit, QTextBrowser {{ background: {SURFACE}; border-color: {LINE}; }}
QComboBox QAbstractItemView {{
    background: {FIELD}; border: 1px solid {LINE_STRONG};
    selection-background-color: {SIGNAL}; selection-color: {ON_SIGNAL};
}}
QPushButton, QToolButton {{
    background: {RAISED}; border: 1px solid {LINE_STRONG}; border-radius: 6px; padding: 6px 14px;
}}
QPushButton:hover, QToolButton:hover {{ border-color: {SIGNAL}; color: {SIGNAL}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {LINE}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {DIM}; border-color: {LINE}; background: {SURFACE}; }}
QPushButton:default {{ border-color: {SIGNAL_DIM}; }}
QToolButton {{ padding: 0; font-weight: 600; }}
QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid {LINE_STRONG}; border-radius: 4px; background: {FIELD};
}}
QCheckBox::indicator:hover {{ border-color: {SIGNAL}; }}
QCheckBox::indicator:checked {{ background: {SIGNAL}; border-color: {SIGNAL}; image: url("{check_icon}"); }}
QGroupBox {{
    border: 1px solid {LINE_STRONG}; border-radius: 8px; margin-top: 12px; padding: 14px 10px 8px 10px;
}}
QGroupBox::title {{
    subcontrol-origin: margin; subcontrol-position: top left; left: 10px; padding: 0 4px;
    color: {SIGNAL}; font-weight: 600;
}}
QRadioButton {{ spacing: 6px; }}
QRadioButton::indicator {{
    width: 16px; height: 16px; border: 1px solid {LINE_STRONG}; border-radius: 8px; background: {FIELD};
}}
QRadioButton::indicator:hover {{ border-color: {SIGNAL}; }}
QRadioButton::indicator:checked {{
    border-color: {SIGNAL};
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
        stop:0 {ON_SIGNAL}, stop:0.32 {ON_SIGNAL}, stop:0.42 {SIGNAL}, stop:1 {SIGNAL});
}}
QProgressBar {{ background: {RAISED}; border: none; border-radius: 3px; max-height: 6px; }}
QProgressBar::chunk {{ background: {SIGNAL}; border-radius: 3px; }}
QMenuBar, QMenu {{ background: {SURFACE}; }}
QMenu::item:selected {{ background: {SIGNAL}; color: {ON_SIGNAL}; }}
QToolTip {{ background: {RAISED}; color: {FG}; border: 1px solid {LINE_STRONG}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {LINE_STRONG}; border-radius: 4px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {DIM}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""


def apply(app: QApplication, check_icon: str) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: BG,
        QPalette.ColorRole.WindowText: FG,
        QPalette.ColorRole.Base: FIELD,
        QPalette.ColorRole.AlternateBase: SURFACE,
        QPalette.ColorRole.Text: FG,
        QPalette.ColorRole.PlaceholderText: DIM,
        QPalette.ColorRole.Button: RAISED,
        QPalette.ColorRole.ButtonText: FG,
        QPalette.ColorRole.BrightText: SIGNAL,
        QPalette.ColorRole.Highlight: SIGNAL,
        QPalette.ColorRole.HighlightedText: ON_SIGNAL,
        QPalette.ColorRole.Link: SIGNAL,
        QPalette.ColorRole.LinkVisited: SIGNAL_DIM,
        QPalette.ColorRole.ToolTipBase: RAISED,
        QPalette.ColorRole.ToolTipText: FG,
        QPalette.ColorRole.Mid: LINE_STRONG,
        QPalette.ColorRole.Dark: LINE,
        QPalette.ColorRole.Light: RAISED,
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(DIM))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet(check_icon))

"""Shared look and small widget helpers."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel

STYLE = '''
QMainWindow, QDialog { background: #f3f5f7; }
QWidget { color: #1e293b; font-family: "Yu Gothic UI", "Segoe UI"; font-size: 9.75pt; }
QFrame#header { background: #172c3b; border-radius: 12px; }
QLabel#heading { color: white; font-size: 17.25pt; font-weight: 600; }
QLabel#subheading { color: #acccd3; }
QLabel#section { font-size: 12pt; font-weight: 600; }
QLabel#muted { color: #64748b; }
QLabel#notice { color: #32666d; background: #e8f3f2; border-radius: 7px; padding: 9px; }
QPushButton { background: white; border: 1px solid #ccd5de; border-radius: 6px; padding: 8px 13px; }
QPushButton:hover { border-color: #16817e; background: #ecf8f5; }
QPushButton#primary { background: #137b77; border-color: #137b77; color: white; font-weight: 600; }
QPushButton:disabled { color: #9ba7b4; background: #edf0f3; border-color: #dde3e8; }
QPushButton#primary:disabled { color: #9ba7b4; background: #edf0f3; border-color: #dde3e8; }
QToolButton { background: white; border: 1px solid #ccd5de; border-radius: 6px; padding: 6px 10px; }
QToolButton:hover { background: #ecf8f5; border-color: #16817e; }
QToolButton:checked, QToolButton:pressed { background: #deefec; color: #133f42; border-color: #16817e; }
QToolButton:focus { border-color: #16817e; }
QLineEdit, QComboBox, QListWidget, QPlainTextEdit { background: white; border: 1px solid #d7dee5; border-radius: 5px; padding: 6px; }
QTableView { background: white; alternate-background-color: #f8fafb; border: 1px solid #d7dee5; border-radius: 7px; gridline-color: #edf1f4; selection-background-color: #deefec; selection-color: #133f42; }
QHeaderView::section { background: #eaf0f3; border: none; border-bottom: 1px solid #d7dee5; padding: 10px 8px; font-weight: 600; }
QListWidget::item { padding: 7px; border-radius: 4px; }
QListWidget::item:selected { background: #deefec; color: #133f42; }
QLabel#cover { background: #e4e9ee; color: #64748b; border-radius: 9px; }
QProgressBar { border: none; background: #dfe7ec; border-radius: 3px; max-height: 6px; }
QProgressBar::chunk { background: #16817e; border-radius: 3px; }
'''


def label(text='', name=None):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        widget.setObjectName(name)
    return widget

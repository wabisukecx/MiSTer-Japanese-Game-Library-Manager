"""A compact image and display-name review shared by adoption and transfer dialogs."""
from pathlib import Path

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon, QImageReader, QPixmap
from PyQt6.QtWidgets import QListWidgetItem


def populate_review(widget, rows):
    widget.clear()
    widget.setIconSize(QSize(80, 100))
    for row in rows:
        image = row.get('image_path')
        text = '%s\n表示名: %s\n%s\n%s' % (row['rom_filename'], row['display_title'],
            row.get('title_note', ''), row.get('image_note', '画像なし'))
        item = QListWidgetItem(text)
        item.setSizeHint(QSize(400, 116))
        if image and Path(image).is_file():
            reader = QImageReader(str(image))
            size = reader.size()
            if size.isValid():
                reader.setScaledSize(size.scaled(QSize(80, 100), Qt.AspectRatioMode.KeepAspectRatio))
                pixmap = QPixmap.fromImage(reader.read())
                item.setIcon(QIcon(pixmap))
        widget.addItem(item)

"""One review and password entry for artwork and confirmed Japanese titles."""
from pathlib import PurePosixPath
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QCheckBox, QLineEdit, QPlainTextEdit,
    QDialogButtonBox, QWidget, QToolButton, QListWidget,
)

from .connection import prefill, remember_checkbox
from .review_list import populate_review


class TransferDialog(QDialog):
    def __init__(self, items, titles, parent=None, *, roms=None, db_path=None, review_rows=None):
        super().__init__(parent)
        self.items, self.mapping = items, titles
        self.rom_items = roms or []
        self.review_rows = review_rows or []
        self.setWindowTitle('画像・日本語ソフト名をMiSTerへ転送')
        self.resize(820, 640)
        layout = QVBoxLayout(self)
        note = QLabel('チェックしたゲームの画像と表示名を確認して転送します。\n画像や日本語名がなくても進めます。実機にあるROM・未変更の画像や別名は保持します。')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        self.review_list = QListWidget()
        self.review_list.setObjectName('transferReview')
        layout.addWidget(self.review_list, 1)
        self.host, self.user, self.password = QLineEdit(), QLineEdit('root'), QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        for title, field in [('MiSTer IP', self.host), ('FTPユーザー', self.user), ('FTPパスワード', self.password)]:
            layout.addWidget(QLabel(title))
            layout.addWidget(field)
        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText('詳細設定')
        self.advanced_toggle.setCheckable(True)
        layout.addWidget(self.advanced_toggle)
        self.advanced = QWidget()
        advanced_layout = QVBoxLayout(self.advanced)
        self.advanced_toggle.toggled.connect(self.advanced.setVisible)
        self.advanced.setVisible(False)
        layout.addWidget(self.advanced)
        self.roms = QCheckBox('実機にないROMをPCから追加（候補%d本・既存ROMはスキップ）' % len(self.rom_items))
        self.roms.setChecked(bool(self.rom_items))
        self.roms.setEnabled(bool(self.rom_items))
        advanced_layout.addWidget(self.roms)
        self.verify_roms = QCheckBox('既存ROMもPCと内容照合する（不一致なら書き込み前に停止・読み戻しに時間がかかります）')
        self.verify_roms.setEnabled(bool(self.rom_items))
        advanced_layout.addWidget(self.verify_roms)
        self.images = QCheckBox('選択済みの画像を転送（%d枚）' % len(items))
        self.images.setChecked(bool(items))
        self.images.setEnabled(bool(items))
        self.titles = QCheckBox('確定済みの日本語ソフト名を転送（%d本）' % len(titles))
        self.titles.setChecked(bool(titles))
        self.titles.setEnabled(bool(titles))
        advanced_layout.addWidget(self.images)
        advanced_layout.addWidget(self.titles)
        note = QLabel('転送は機種ルート直下のみ対応です。未配置ROM→画像→日本語名の順に反映します。\n既存ROMは上書きしません。任意で内容を照合できます。別名は実機のゲームフォルダー全体で衝突を検査します。\n中断・エラー時は反映済みファイルの復旧を試みます。復旧が必要な場合は、メイン画面に「転送を復旧」が表示されます。')
        note.setWordWrap(True)
        advanced_layout.addWidget(note)
        self.remember = remember_checkbox()
        advanced_layout.addWidget(self.remember)
        if db_path is not None:
            prefill(db_path, self.host, self.user, self.password, self.remember)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(140)
        advanced_layout.addWidget(self.preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.submit = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.submit.setText('この内容でMiSTerへ転送')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        for field in (self.images, self.titles, self.roms):
            field.toggled.connect(self.update_preview)
        self.host.textChanged.connect(self.update_preview)
        self.user.textChanged.connect(self.update_preview)
        self.update_preview()
        self.host.setFocus()

    def update_preview(self):
        items = self.items if self.images.isChecked() else []
        titles = self.mapping if self.titles.isChecked() else {}
        roms = self.rom_items if self.roms.isChecked() else []
        self.summary.setText('ROM追加候補 %d本 / 画像 %d枚 / 日本語ソフト名 %d本' % (len(roms), len(items), len(titles)))
        parts = []
        if roms:
            parts.append('【ROM・同名ファイルがなければ追加】\n' + '\n\n'.join(
                i['local_path'] + '\n→ ' + i['remote_path'] for i in roms))
        if items:
            parts.append('【画像】\n' + '\n\n'.join(i['local_path'] + '\n→ ' + i['remote_path'] for i in items))
        if titles:
            parts.append('【日本語ソフト名】実機設定が指定する別名ファイルへ統合\n' +
                         '\n'.join(k + ' → ' + v for k, v in titles.items()))
        self.preview.setPlainText('\n\n'.join(parts))
        rows = []
        for original in self.review_rows:
            row = dict(original)
            if not self.images.isChecked() and original.get('image_path'):
                row.update(image_path=None, image_note='画像は追加しない（実機の画像は保持）')
            if not self.titles.isChecked():
                row['display_title'] = PurePosixPath(row['rom_filename']).stem
                row['title_note'] = '日本語名は追加しない（実機の別名は保持）'
            rows.append(row)
        populate_review(self.review_list, rows)
        self.review_list.setVisible(bool(rows))
        self.submit.setEnabled(bool((items or titles or roms) and self.host.text().strip() and self.user.text().strip()))

    def reject(self):
        self.password.clear()
        super().reject()

    def closeEvent(self, event):
        self.password.clear()
        super().closeEvent(event)

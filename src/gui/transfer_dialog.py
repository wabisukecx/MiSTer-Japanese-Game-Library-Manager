"""One review and password entry for artwork and confirmed Japanese titles."""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QCheckBox, QLineEdit, QPlainTextEdit,
    QDialogButtonBox,
)

from .connection import prefill, remember_checkbox


class TransferDialog(QDialog):
    def __init__(self, items, titles, parent=None, *, roms=None, db_path=None):
        super().__init__(parent)
        self.items, self.mapping = items, titles
        self.rom_items = roms or []
        self.setWindowTitle('画像・日本語ソフト名をMiSTerへ転送')
        self.resize(820, 640)
        layout = QVBoxLayout(self)
        self.roms = QCheckBox('実機にないROMをPCから追加（候補%d本・既存ROMはスキップ）' % len(self.rom_items))
        self.roms.setChecked(bool(self.rom_items))
        self.roms.setEnabled(bool(self.rom_items))
        layout.addWidget(self.roms)
        self.verify_roms = QCheckBox('既存ROMもPCと内容照合する（不一致なら書き込み前に停止・読み戻しに時間がかかります）')
        self.verify_roms.setEnabled(bool(self.rom_items))
        layout.addWidget(self.verify_roms)
        self.images = QCheckBox('選択済みの画像を転送（%d枚）' % len(items))
        self.images.setChecked(bool(items))
        self.images.setEnabled(bool(items))
        self.titles = QCheckBox('確定済みの日本語ソフト名を転送（%d本）' % len(titles))
        self.titles.setChecked(bool(titles))
        self.titles.setEnabled(bool(titles))
        layout.addWidget(self.images)
        layout.addWidget(self.titles)
        note = QLabel('転送は機種ルート直下のみ対応です。未配置ROM→画像→日本語名の順に反映します。\n既存ROMは上書きしません。任意で内容を照合できます。別名は実機のゲームフォルダー全体で衝突を検査します。\n中断・エラー時は反映済みファイルの復旧を試みます。通信断・外部変更で復旧できない場合は、詳細機能の「転送記録から復旧」を使います。')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.host, self.user, self.password = QLineEdit(), QLineEdit('root'), QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        for title, field in [('MiSTer IP', self.host), ('FTPユーザー', self.user), ('FTPパスワード', self.password)]:
            layout.addWidget(QLabel(title))
            layout.addWidget(field)
        self.remember = remember_checkbox()
        layout.addWidget(self.remember)
        if db_path is not None:
            prefill(db_path, self.host, self.user, self.password, self.remember)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        layout.addWidget(self.preview, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.submit = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.submit.setText('バックアップしてまとめて転送')
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
        self.submit.setEnabled(bool((items or titles or roms) and self.host.text().strip() and self.user.text().strip()))

    def reject(self):
        self.password.clear()
        super().reject()

    def closeEvent(self, event):
        self.password.clear()
        super().closeEvent(event)

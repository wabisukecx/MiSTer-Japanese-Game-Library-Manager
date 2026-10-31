"""Launch the separate Skraper UI and keep its output associated with a system.

No undocumented scraping CLI, config rewriting, stored credentials or UI robot.
"""
from pathlib import Path

from PyQt6.QtCore import QProcess, QUrl
from PyQt6.QtWidgets import (QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                            QLineEdit, QPushButton, QFileDialog, QMessageBox)

from systems import system_profile


class SkraperDialog(QDialog):
    def __init__(self, profile, folder, parent=None, detected=None):
        super().__init__(parent)
        self.setWindowTitle('2 · Skraperで画像取得・取り込み')
        self.resize(800, 420)
        self.profile = profile.copy()
        layout = QVBoxLayout(self)
        name = system_profile(profile['system']).name
        layout.addWidget(QLabel(name))
        self.folder = QLineEdit(folder)
        self.folder.setPlaceholderText('展開済みのSkraperUI.exeが入っているフォルダー')
        self.rom_folder = QLineEdit(detected['rom_folder'] if detected else profile['rom_folder'])
        self.rom_folder.setReadOnly(True)
        self.output = QLineEdit(profile['image_folder'])
        for title, field, browse in [
            ('Skraperフォルダー', self.folder, self.choose_folder),
            ('PCのROMフォルダー', self.rom_folder, None),
            ('画像の出力フォルダー', self.output, self.choose_output),
        ]:
            layout.addWidget(QLabel(title))
            row = QHBoxLayout()
            row.addWidget(field)
            copy = QPushButton('コピー')
            copy.clicked.connect(lambda checked=False, f=field: QApplication.clipboard().setText(f.text()))
            row.addWidget(copy)
            if browse:
                button = QPushButton('参照')
                button.clicked.connect(browse)
                row.addWidget(button)
            layout.addLayout(row)
        registered = Path(profile['rom_folder'])
        if detected and Path(detected['rom_folder']) != registered:
            status = QLabel('SkraperのROMフォルダーが登録先と異なります。')
            status.setWordWrap(True)
            layout.addWidget(status)
        note = QLabel('Skraperで画像を取得し、完了後に「取得画像を取り込む」を押してください。')
        note.setWordWrap(True)
        layout.addWidget(note)
        guide_path = Path(__file__).resolve().parents[2] / 'docs' / 'setup.md'
        guide = QLabel('設定方法の詳細は <a href="%s" style="color: #137b77;">docs/setup.md</a> を参照してください。'
                       % QUrl.fromLocalFile(str(guide_path)).toString())
        guide.setOpenExternalLinks(True)
        layout.addWidget(guide)
        layout.addStretch()
        row = QHBoxLayout()
        launch = QPushButton('Skraperを起動')
        launch.clicked.connect(self.launch)
        row.addWidget(launch)
        ready = QPushButton('取得画像を取り込む')
        ready.clicked.connect(self.accept)
        row.addWidget(ready)
        close = QPushButton('保存して閉じる')
        close.clicked.connect(self.reject)
        row.addWidget(close)
        layout.addLayout(row)

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, 'SkraperUI.exeが入っているフォルダー', self.folder.text())
        if path:
            self.folder.setText(path)

    def choose_output(self):
        path = QFileDialog.getExistingDirectory(self, '画像保存先', self.output.text())
        if path:
            self.output.setText(path)

    def launch(self):
        executable = Path(self.folder.text()).expanduser() / 'SkraperUI.exe'
        if not executable.is_file() or executable.name.lower() != 'skraperui.exe':
            QMessageBox.warning(self, 'Skraperの場所', 'SkraperUI.exeが入っているフォルダーを指定してください。')
            return
        try:
            self.validated_profile()
            started, _ = QProcess.startDetached(str(executable.resolve()), [], str(executable.resolve().parent))
            if not started:
                raise OSError()
        except (OSError, ValueError):
            QMessageBox.warning(self, 'Skraperの起動', 'ROM参照元・画像保存先とSkraperの実行ファイルを確認してください。')

    def validated_profile(self):
        source = Path(self.rom_folder.text()).resolve(strict=True)
        output = Path(self.output.text()).expanduser().resolve()
        if not source.is_dir():
            raise ValueError('ROMフォルダーが見つかりません')
        if not self.output.text().strip():
            raise ValueError('画像保存先を指定してください')
        output.mkdir(parents=True, exist_ok=True)
        if not output.is_dir():
            raise ValueError('画像保存先を確認してください')
        return dict(self.profile, image_folder=str(output))

    def accept(self):
        try:
            self.validated_profile()
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, '保存先を確認', str(error))
            return
        super().accept()

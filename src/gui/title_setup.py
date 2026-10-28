"""Read-only title settings probe, separate from image transfer."""
import json
import sys
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication, QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QPlainTextEdit

from database.store import Store
from consolemode.titles import title_mapping, inspect_settings, transfer_titles


class Probe(QThread):
    completed = pyqtSignal(object)

    def __init__(self, credentials, mapping, output, parent, inspection=None, title_targets=()):
        super().__init__(parent)
        self.credentials, self.mapping, self.output = credentials, mapping, output
        self.inspection = inspection
        self.title_targets = title_targets

    def run(self):
        try:
            if self.inspection is None:
                result = inspect_settings(*self.credentials, self.mapping, self.output, title_targets=self.title_targets)
            else:
                result = transfer_titles(*self.credentials, self.inspection, self.output / 'transfers')
            self.completed.emit(result)
        except Exception as error:
            self.completed.emit(dict(status='failed', message=str(error)))
        finally:
            self.credentials.clear()


class TitleSetup(QDialog):
    def __init__(self, db_path, games=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Console Mode 日本語タイトルの確認と転送')
        self.resize(740, 570)
        self.output = Path(db_path).parent / 'title-setup'
        db = Store(db_path)
        try:
            targets = games if games is not None else db.games(device='home-mister')
            self.mapping = title_mapping(targets, all_games=db.games())
            self.title_targets = [g['rom_path'] for g in targets if g['match_status'] == 'confirmed']
        finally:
            db.close()
        self.worker = None
        self.inspection = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('①実機設定を確認 → ②既存の別名をバックアップして日本語名を転送\nROM・画像・設定・フォントは変更しません。転送後はConsole Modeを再起動して表示を確認します。'))
        self.host, self.user, self.password = QLineEdit(), QLineEdit('root'), QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        for title, field in [('MiSTer IP', self.host), ('FTPユーザー', self.user), ('FTPパスワード（保存しません）', self.password)]:
            layout.addWidget(QLabel(title))
            layout.addWidget(field)
        self.button = QPushButton('接続して設定を確認')
        self.button.clicked.connect(self.inspect)
        layout.addWidget(self.button)
        self.transfer_button = QPushButton('確認済みの日本語名をバックアップして転送')
        self.transfer_button.setEnabled(False)
        self.transfer_button.clicked.connect(self.transfer)
        layout.addWidget(self.transfer_button)
        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setPlainText('\n'.join(k + ' → ' + v for k, v in self.mapping.items()))
        layout.addWidget(self.result)
        self.host.textChanged.connect(lambda: self.transfer_button.setEnabled(False))
        try:
            previous = json.loads((self.output / 'inspection.json').read_text(encoding='utf-8'))
            if previous.get('mappings') == self.mapping and previous.get('host') == self.host.text():
                self.done_inspecting(previous)
        except (OSError, ValueError, KeyError):
            pass
        self.host.setFocus()

    def inspect(self):
        if self.worker is not None:
            return
        credentials = [self.host.text().strip(), self.user.text().strip(), self.password.text()]
        self.inspection = None
        for field in (self.host, self.user, self.password, self.button, self.transfer_button): field.setEnabled(False)
        self.result.setPlainText('実機の設定を読み取り中…')
        self.worker = Probe(credentials, self.mapping, self.output, self, title_targets=self.title_targets)
        self.worker.completed.connect(self.done_inspecting)
        self.worker.finished.connect(self.finished_inspecting)
        self.worker.start()

    def transfer(self):
        if self.worker is not None or self.inspection is None:
            return
        credentials = [self.host.text().strip(), self.user.text().strip(), self.password.text()]
        self.password.clear()
        for field in (self.host, self.user, self.password, self.button, self.transfer_button): field.setEnabled(False)
        self.result.setPlainText('別名ファイルをバックアップして転送中…')
        self.worker = Probe(credentials, self.mapping, self.output, self, inspection=self.inspection)
        self.worker.completed.connect(self.done_transfer)
        self.worker.finished.connect(self.finished_inspecting)
        self.worker.start()

    def finished_inspecting(self):
        self.worker.deleteLater()
        self.worker = None
        for field in (self.host, self.user, self.password, self.button): field.setEnabled(True)

    def done_inspecting(self, result):
        ready = (result['status'] == 'inspected' and result.get('alias_path')
                 and result.get('font_present') and result.get('mappings') == self.mapping)
        self.inspection = result if ready else None
        self.transfer_button.setEnabled(bool(ready))
        lines = ['結果: ' + result['status'], '別名保存先: ' + str(result['alias_path']),
                 '日本語フォント: ' + ('存在' if result['font_present'] else '未確認または未配置'),
                 '記録: ' + str(self.output / 'inspection.json')]
        if result.get('preview_path'): lines.append('既存内容を保持したプレビュー: ' + result['preview_path'])
        lines.append('実機への書き込みは行っていません。')
        lines.extend(['', '転送する日本語名（別名は全機種で共有されます）:'])
        lines.extend(k + ' → ' + v for k, v in self.mapping.items())
        self.result.setPlainText('\n'.join(lines))

    def done_transfer(self, result):
        if result.get('status') == 'verified':
            self.result.setPlainText('%d本の別名を転送・照合しました。\n記録: %s\nConsole Modeを終了して入り直し、日本語名の表示とゲーム起動を確認してください。' % (result['count'], result['report']))
        else:
            self.result.setPlainText(result.get('message', '転送できませんでした。設定を再確認してください。'))
        self.inspection = None

    def closeEvent(self, event):
        if self.worker is not None:
            event.ignore()
        else:
            self.password.clear()
            super().closeEvent(event)

    def reject(self):
        if self.worker is None:
            self.password.clear()
            super().reject()


def main():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    dialog = TitleSetup('work/library.sqlite3')
    dialog.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())

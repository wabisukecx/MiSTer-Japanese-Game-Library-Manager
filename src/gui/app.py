import argparse
from pathlib import Path
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QComboBox, QLineEdit, QCheckBox, QSplitter, QTableView, QHeaderView,
    QFrame, QMessageBox, QProgressBar, QPlainTextEdit, QAbstractItemView,
    QScrollArea, QSizePolicy, QToolButton,
)

from systems import SYSTEMS, system_profile
from .detail_panel import DetailMixin
from .import_flow import ImportMixin
from .library_model import LibraryModel, LibraryFilter
from .style import STYLE, label
from .system_choices import populate_systems
from .tasks import Task, with_db, load_library
from .transfer_flow import TransferMixin

ROM_ROOT = Path(__file__).resolve().parents[2] / 'work' / 'Roms'


class MainWindow(TransferMixin, ImportMixin, DetailMixin, QMainWindow):
    def __init__(self, db_path):
        super().__init__()
        self.db_path = str(Path(db_path).resolve())
        self.rom_folders = {system: ROM_ROOT / system for system in SYSTEMS}
        for folder in self.rom_folders.values():
            folder.mkdir(parents=True, exist_ok=True)
        self.task = None
        self.pending_refresh = False
        self.refresh_message = None
        self.after_task = None
        self.closing = False
        self.current_game = None
        self.current_artwork = None
        self.desired_artwork_id = None
        self.pixmap = None
        self.angle = 0
        self.busy_widgets = []
        self.setWindowTitle('MiSTer Japanese Game Library Manager')
        self.resize(1390, 925)
        self.setMinimumSize(1040, 760)
        self.setStyleSheet(STYLE)
        content = QWidget()
        self.setCentralWidget(content)
        page = QVBoxLayout(content)
        page.setContentsMargins(22, 18, 22, 16)
        page.setSpacing(13)

        header = QFrame()
        header.setObjectName('header')
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(20, 15, 20, 15)
        header_layout.addWidget(label('MiSTer 日本語ゲームライブラリ', 'heading'))
        header_layout.addWidget(label('ROM名を保ったまま、日本語タイトルとパッケージを確認', 'subheading'))
        page.addWidget(header)

        toolbar = QHBoxLayout()
        toolbar.addStretch()
        toolbar.addWidget(self.button('再読み込み', self.refresh))
        self.recovery_button = self.button('転送を復旧', self.choose_recovery)
        self.recovery_button.setVisible(False)
        self.recovery_reports = []
        toolbar.addWidget(self.recovery_button)
        page.addLayout(toolbar)

        workflow = QHBoxLayout()
        workflow.addWidget(self.button('1 · PCのROMを登録', self.choose_local_roms))
        self.skraper_button = self.button('2 · Skraperで画像取得・取り込み', self.choose_skraper)
        workflow.addWidget(self.skraper_button)
        self.transfer_button = self.button('3 · MiSTerへ転送', self.choose_transfer)
        workflow.addWidget(self.transfer_button)
        page.addLayout(workflow)

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('ROM名・日本語タイトルで検索')
        self.search.textChanged.connect(self.refilter)
        self.pending_only = QCheckBox('日本語名が未確定のみ')
        self.pending_only.toggled.connect(self.refilter)
        filters.addWidget(self.search)
        self.system = QComboBox()
        populate_systems(self.system, include_all=True, show_unverified=True)
        self.system.currentIndexChanged.connect(self.refilter)
        filters.addWidget(self.system)
        filters.addWidget(self.pending_only)
        self.scope_ids = set()
        self.scope_key = None
        self.scope_only = QCheckBox('登録したフォルダーのROMのみ')
        self.scope_only.setToolTip('直近にPCのROMフォルダーとして登録したROMだけを表示します。外すと登録済みのすべてを表示します。')
        self.scope_only.setVisible(False)
        self.scope_only.toggled.connect(self.refilter)
        filters.addWidget(self.scope_only)
        page.addLayout(filters)

        batch = QHBoxLayout()
        self.check_all = QCheckBox('ALL（表示中をすべて選択）')
        self.check_all.setTristate(True)
        self.check_all.clicked.connect(self.toggle_all)
        batch.addWidget(self.check_all)
        self.check_count = label('0本をチェック', 'muted')
        batch.addWidget(self.check_count)
        batch.addStretch()
        page.addLayout(batch)

        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)
        self.model = LibraryModel(self)
        self.proxy = LibraryFilter(self)
        self.proxy.setSourceModel(self.model)
        self.model.checks_changed.connect(self.update_checks)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(51)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column, width in ((2, 65), (3, 92), (4, 88)):
            self.table.setColumnWidth(column, width)
        self.table.selectionModel().currentRowChanged.connect(self.show_game)
        splitter.addWidget(self.table)

        details = QScrollArea()
        details.setMinimumWidth(380)
        details.setWidgetResizable(True)
        detail_panel = QWidget()
        details.setWidget(detail_panel)
        panel_layout = QVBoxLayout(detail_panel)
        panel_layout.setContentsMargins(10, 10, 10, 10)
        panel_layout.setSpacing(8)
        package_page = QWidget()
        title_page = QWidget()
        panel_layout.addWidget(title_page)
        panel_layout.addWidget(package_page, 1)
        metadata_toggle = QToolButton()
        metadata_toggle.setText('候補の根拠・画像の出典')
        metadata_toggle.setCheckable(True)
        panel_layout.addWidget(metadata_toggle)
        metadata = QWidget()
        metadata_layout = QVBoxLayout(metadata)
        metadata_layout.setContentsMargins(0, 0, 0, 0)
        metadata_toggle.toggled.connect(metadata.setVisible)
        metadata.setVisible(False)
        panel_layout.addWidget(metadata)
        detail = QVBoxLayout(package_page)
        detail.setContentsMargins(0, 0, 0, 0)
        detail.setSpacing(6)
        title_layout = QVBoxLayout(title_page)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(6)
        detail.addWidget(label('パッケージの確認', 'section'))
        self.cover = label('ゲームを選択してください', 'cover')
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setMinimumSize(280, 180)
        self.cover.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        detail.addWidget(self.cover, 1)
        rotations = QHBoxLayout()
        rotations.addWidget(self.button('左へ90°', lambda: self.rotate(-90)))
        rotations.addWidget(self.button('右へ90°', lambda: self.rotate(90)))
        detail.addLayout(rotations)
        self.provenance = label('', 'muted')
        self.provenance.setWordWrap(True)
        metadata_layout.addWidget(self.provenance)
        self.save_button = self.button('名前・画像を保存', self.save_game, True)
        self.save_button.setEnabled(False)
        detail.addWidget(self.save_button)
        title_layout.addWidget(label('日本語タイトル', 'section'))
        self.title_context = label('', 'muted')
        self.title_context.setWordWrap(True)
        title_layout.addWidget(self.title_context)
        self.title_candidates = QComboBox()
        self.title_candidates.currentIndexChanged.connect(self.use_title_candidate)
        title_layout.addWidget(self.title_candidates)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText('日本語タイトルを入力')
        self.title_edit.textChanged.connect(self.update_rotation_controls)
        title_layout.addWidget(self.title_edit)
        self.evidence = QPlainTextEdit()
        self.evidence.setReadOnly(True)
        self.evidence.setMinimumHeight(110)
        self.evidence.setMaximumHeight(180)
        metadata_layout.addWidget(self.evidence)
        splitter.addWidget(details)
        splitter.setSizes([870, 420])
        page.addWidget(splitter, 1)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        page.addWidget(self.progress_bar)
        status = QHBoxLayout()
        self.status_label = label('読み込み中…', 'muted')
        status.addWidget(self.status_label, 1)
        self.cancel_button = QPushButton('中断')
        self.cancel_button.clicked.connect(self.cancel_task)
        self.cancel_button.setEnabled(False)
        status.addWidget(self.cancel_button)
        page.addLayout(status)
        self.refresh()

    def button(self, text, callback, primary=False):
        widget = QPushButton(text)
        if primary:
            widget.setObjectName('primary')
        widget.clicked.connect(callback)
        self.busy_widgets.append(widget)
        return widget

    def start_task(self, operation, on_result, text):
        if self.task is not None:
            return
        for widget in self.busy_widgets:
            widget.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress_bar.setRange(0, 0)
        self.status_label.setText(text)
        task = Task(operation, self)
        self.task = task
        self.update_checks()
        task.result.connect(on_result)
        task.failed.connect(self.task_failed)
        task.progress.connect(self.set_progress)
        task.finished.connect(self.task_finished)
        task.start()

    def set_progress(self, done, total):
        self.progress_bar.setRange(0, max(1, total))
        self.progress_bar.setValue(done)

    def task_failed(self, message):
        self.update_recovery_action()
        if self.recovery_reports:
            message += '\n画面上部の「転送を復旧」から復旧を再実行できます。'
        self.status_label.setText(message)
        if not self.closing and not self.task.cancel.is_set():
            QMessageBox.warning(self, '処理できませんでした', message)

    def task_finished(self):
        self.task.deleteLater()
        self.task = None
        self.update_recovery_action()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        for widget in self.busy_widgets:
            widget.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.update_rotation_controls()
        self.update_checks()
        if self.closing:
            self.close()
        elif self.after_task:
            callback, self.after_task = self.after_task, None
            callback()
        elif self.pending_refresh:
            self.pending_refresh = False
            self.refresh()

    def cancel_task(self):
        if self.task:
            self.task.cancel.set()
            self.status_label.setText('中断しています。完了済みの候補は保持されます。')

    def refresh(self):
        self.start_task(lambda cancelled, progress: load_library(self.db_path, cancelled, progress),
                        self.library_loaded, 'ローカルDBを読み込み中…')

    def library_loaded(self, games):
        selection = getattr(self, 'workflow_selection', None)
        self.workflow_selection = None
        selected = self.current_game['id'] if self.current_game else None
        self.update_scope(games)
        self.model.replace(games)
        if selection is not None:
            self.system.setCurrentIndex(max(0, self.system.findData(selection)))
        self.refilter()
        for row in range(self.proxy.rowCount()):
            game = self.proxy.index(row, 0).data(Qt.ItemDataRole.UserRole)
            if game['id'] == selected:
                self.table.selectRow(row)
                break
        self.status_label.setText(self.refresh_message or '%d本を表示 · 画像は候補から選択できます' % self.proxy.rowCount())
        self.refresh_message = None

    def update_scope(self, games):
        """Limit the list to the ROM folder registered last; it is remembered across restarts."""
        scope = with_db(self.db_path, lambda db: db.setting('ui/scope'))
        ids = set()
        if scope and scope.get('system') not in SYSTEMS:
            scope = None
        if scope:
            folder = Path(scope['rom_folder'])
            members = {scope['system'], *system_profile(scope['system']).siblings}
            ids = {g['id'] for g in games if g['system'] in members
                   and g.get('local_rom_path') and folder in Path(g['local_rom_path']).parents}
        key = (scope['system'], scope['rom_folder']) if scope else None
        self.scope_ids = ids
        self.scope_only.blockSignals(True)
        self.scope_only.setText('登録したフォルダーのROMのみ（%d本）' % len(ids))
        self.scope_only.setVisible(bool(ids))
        if key != self.scope_key:
            self.scope_only.setChecked(bool(ids))
            self.scope_key = key
        elif not ids:
            self.scope_only.setChecked(False)
        self.scope_only.blockSignals(False)

    def refilter(self, *args):
        if not hasattr(self, 'proxy'):
            return
        self.proxy.system = self.system.currentData() or ''
        self.proxy.query = self.search.text()
        self.proxy.pending = self.pending_only.isChecked()
        self.proxy.ids = self.scope_ids if self.scope_only.isChecked() else None
        self.proxy.invalidateFilter()
        if self.proxy.rowCount():
            self.table.selectRow(0)
        else:
            self.current_game = self.current_artwork = None
            self.title_candidates.clear()
            self.title_edit.clear()
            self.title_context.clear()
            self.evidence.clear()
            self.provenance.clear()
            self.pixmap = None
            self.cover.setPixmap(QPixmap())
            self.cover.setText('該当するゲームがありません')
            self.update_rotation_controls()
        self.status_label.setText('%d本を表示' % self.proxy.rowCount())
        self.update_checks()

    def visible_games(self):
        return [self.proxy.index(row, 0).data(Qt.ItemDataRole.UserRole) for row in range(self.proxy.rowCount())]

    def toggle_all(self, checked=False):
        if self.task is not None:
            self.update_checks()
            return
        ids = {game['id'] for game in self.visible_games()}
        self.model.set_checks(ids, self.check_all.checkState() != Qt.CheckState.Unchecked)

    def update_checks(self):
        if not hasattr(self, 'proxy'):
            return
        ids = {game['id'] for game in self.visible_games()}
        count = len(ids & self.model.checked_ids)
        self.check_all.blockSignals(True)
        self.check_all.setCheckState(Qt.CheckState.Unchecked if not count else
            Qt.CheckState.Checked if count == len(ids) else Qt.CheckState.PartiallyChecked)
        self.check_all.blockSignals(False)
        self.check_all.setEnabled(bool(ids) and self.task is None)
        self.check_count.setText('%d / %d本をチェック' % (count, len(ids)))
        self.transfer_button.setEnabled(count > 0 and self.task is None)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'cover'):
            self.draw_cover()

    def saved(self, message):
        self.status_label.setText(message)
        self.refresh_message = message
        self.pending_refresh = True

    def closeEvent(self, event):
        if self.task:
            self.closing = True
            self.task.cancel.set()
            event.ignore()
        else:
            event.accept()


def run(db_path='work/library.sqlite3'):
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName('MiSTer Japanese Game Library Manager')
    app.setFont(QFont('Yu Gothic UI', 10))
    window = MainWindow(db_path)
    window.show()
    return app.exec()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Japanese game library / local Skraper image review')
    parser.add_argument('--db', default='work/library.sqlite3')
    args = parser.parse_args(argv)
    return run(args.db)


if __name__ == '__main__':
    raise SystemExit(main())

import argparse
import json
from pathlib import Path
import sys
import threading
import time

from PyQt6.QtCore import Qt, QAbstractTableModel, QSortFilterProxyModel, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QColor, QFont, QImageReader, QPixmap, QTransform
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QLineEdit, QCheckBox, QSplitter, QTableView, QHeaderView, QListWidget,
    QListWidgetItem, QFrame, QFileDialog, QMessageBox, QProgressBar, QDialog,
    QDialogButtonBox, QPlainTextEdit, QAbstractItemView,
    QTabWidget, QSizePolicy, QMenu,
)

from artwork.skraper_import import plan_import, apply_import, inspect_png
from artwork.skraper_config import system_paths
from database.store import Store
from metadata.approval import approval_plan, apply_approval
from metadata.titles import match_titles
from metadata.title_resolution import group_candidates
from workflow import register_folder, batch_plan
from mister.recovery import recover_transfer, validate_journal
from mister.artwork_transfer import transfer_images
from .transfer_dialog import TransferDialog
from .system_choices import populate_systems
from systems import SYSTEMS, system_profile
from mister.ftp_client import FTPReader

STYLE = '''
QMainWindow, QDialog { background: #f3f5f7; }
QWidget { color: #1e293b; font-family: "Yu Gothic UI", "Segoe UI"; font-size: 13px; }
QFrame#header { background: #172c3b; border-radius: 12px; }
QLabel#heading { color: white; font-size: 23px; font-weight: 600; }
QLabel#subheading { color: #acccd3; }
QLabel#section { font-size: 16px; font-weight: 600; }
QLabel#muted { color: #64748b; }
QLabel#notice { color: #32666d; background: #e8f3f2; border-radius: 7px; padding: 9px; }
QPushButton { background: white; border: 1px solid #ccd5de; border-radius: 6px; padding: 8px 13px; }
QPushButton:hover { border-color: #16817e; background: #ecf8f5; }
QPushButton#primary { background: #137b77; border-color: #137b77; color: white; font-weight: 600; }
QPushButton:disabled { color: #9ba7b4; background: #edf0f3; border-color: #dde3e8; }
QPushButton#primary:disabled { color: #9ba7b4; background: #edf0f3; border-color: #dde3e8; }
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


def with_db(path, operation):
    db = Store(path)
    try:
        return operation(db)
    finally:
        db.close()


def load_library(path, cancelled, progress):
    def load(db):
        db.prune_missing_artworks(cancelled=cancelled)
        games = [game for game in db.games() if game['system'] in SYSTEMS]
        for index, game in enumerate(games):
            if cancelled():
                raise InterruptedError('一覧の読み込みを中断しました')
            game['_titles'] = db.get_candidates(game['id'])
            game['_artworks'] = db.artworks(game['id'])
            progress(index + 1, len(games))
        return games
    return with_db(path, load)


class Task(QThread):
    result = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int, int)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.cancel = threading.Event()

    def run(self):
        try:
            self.result.emit(self.operation(self.cancel.is_set, self.progress.emit))
        except InterruptedError as error:
            self.failed.emit(str(error))
        except Exception as error:
            self.failed.emit(str(error))


class LibraryModel(QAbstractTableModel):
    checks_changed = pyqtSignal()
    headers = ('ROM名', '日本語タイトル', '地域', 'パッケージ', '照合状態')

    def __init__(self, parent=None):
        super().__init__(parent)
        self.games = []
        self.checked_ids = set()

    def replace(self, games):
        self.beginResetModel()
        self.games = games
        self.checked_ids.intersection_update(g['id'] for g in games)
        self.endResetModel()
        self.checks_changed.emit()

    def flags(self, index):
        flags = super().flags(index)
        if index.isValid() and index.column() == 0:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if index.isValid() and index.column() == 0 and role == Qt.ItemDataRole.CheckStateRole:
            game_id = self.games[index.row()]['id']
            if value == Qt.CheckState.Checked or value == Qt.CheckState.Checked.value:
                self.checked_ids.add(game_id)
            else:
                self.checked_ids.discard(game_id)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
            self.checks_changed.emit()
            return True
        return False

    def set_checks(self, ids, checked):
        if checked:
            self.checked_ids.update(ids)
        else:
            self.checked_ids.difference_update(ids)
        if self.games:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.games)-1, 0), [Qt.ItemDataRole.CheckStateRole])
        self.checks_changed.emit()

    def rowCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.games)

    def columnCount(self, parent=None):
        return len(self.headers)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headers[section]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        game = self.games[index.row()]
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            return Qt.CheckState.Checked if game['id'] in self.checked_ids else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.UserRole:
            return game
        if role == Qt.ItemDataRole.ToolTipRole:
            return game['rom_path']
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 4:
            return QColor('#14766a' if game['match_status'] == 'confirmed' else '#8e641a')
        if role == Qt.ItemDataRole.DisplayRole:
            groups = group_candidates(game['_titles'])
            candidate = groups[0]['japanese_title'] if groups else ''
            title = game['japanese_title'] or (candidate + '（候補）' if candidate else '—')
            art = '選択済み' if game['artwork_path'] else ('候補 %d枚' % len(game['_artworks']) if game['_artworks'] else '未取得')
            status = {'confirmed': '確定済み', 'review': '要確認', 'unmatched': '未照合'}.get(game['match_status'], game['match_status'])
            return (game['rom_filename'], title, game['region'] or '不明', art, status)[index.column()]


class LibraryFilter(QSortFilterProxyModel):
    device = ''
    system = ''
    query = ''
    pending = False
    ids = None  # when set, only these game IDs (the folder registered last) are shown

    def filterAcceptsRow(self, row, parent):
        game = self.sourceModel().games[row]
        if self.ids is not None and game['id'] not in self.ids:
            return False
        if self.device and game['device'] != self.device:
            return False
        if self.system and game['system'] != self.system:
            return False
        if self.pending and game['match_status'] == 'confirmed':
            return False
        text = ' '.join([game['rom_filename'], game['japanese_title'] or '',
                         *[c['japanese_title'] for c in game['_titles']]])
        return self.query.casefold() in text.casefold()


class MainWindow(QMainWindow):
    def __init__(self, db_path):
        super().__init__()
        self.db_path = str(Path(db_path).resolve())
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
        self.device = QComboBox()
        self.device.setMinimumWidth(160)
        self.device.currentIndexChanged.connect(self.refilter)
        toolbar.addWidget(label('ライブラリ'))
        toolbar.addWidget(self.device)
        toolbar.addStretch()
        toolbar.addWidget(self.button('再読み込み', self.refresh))
        self.advanced_button = QPushButton('詳細機能')
        advanced = QMenu(self.advanced_button)
        for text, callback in [
            ('日本語名をDBで再照合', self.choose_title_lookup),
            ('最新のDBを取得して確定済みと比較', self.choose_title_refresh),
            ('日本語名DBを書き出す（JSON）', self.choose_title_db_export),
            ('日本語名DBのJSONをマージ', self.choose_title_db_merge),
            ('実機のROM一覧を取得', self.choose_scan),
            ('日本語タイトル設定', self.choose_title_setup),
            ('転送記録から復旧', self.choose_recovery),
            ('保存済みの画像フォルダーを取り込む', self.choose_import),
        ]:
            advanced.addAction(text, callback)
        self.advanced_button.setMenu(advanced)
        self.busy_widgets.append(self.advanced_button)
        toolbar.addWidget(self.advanced_button)
        page.addLayout(toolbar)

        workflow = QHBoxLayout()
        workflow.addWidget(self.button('1 · PCのROMを登録', self.choose_local_roms))
        self.skraper_button = self.button('2 · Skraperで画像取得・取り込み', self.choose_skraper, True)
        workflow.addWidget(self.skraper_button)
        workflow.addWidget(self.button('4 · MiSTerへまとめて転送', self.choose_transfer))
        page.addLayout(workflow)

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('ROM名・日本語タイトルで検索')
        self.search.textChanged.connect(self.refilter)
        self.pending_only = QCheckBox('未照合・要確認のみ')
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
        self.approve_button = self.button('3 · チェックした候補をまとめて承諾', self.approve_checked, True)
        self.approve_button.setEnabled(False)
        batch.addWidget(self.approve_button)
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

        details = QTabWidget()
        details.setMinimumWidth(360)
        package_page = QWidget()
        title_page = QWidget()
        details.addTab(package_page, 'パッケージ画像')
        details.addTab(title_page, '日本語タイトル')
        detail = QVBoxLayout(package_page)
        detail.setContentsMargins(12, 12, 12, 12)
        title_layout = QVBoxLayout(title_page)
        title_layout.setContentsMargins(12, 16, 12, 16)
        title_layout.setSpacing(12)
        detail.addWidget(label('パッケージの確認', 'section'))
        self.cover = label('ゲームを選択してください', 'cover')
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setMinimumSize(300, 245)
        self.cover.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        detail.addWidget(self.cover, 1)
        rotations = QHBoxLayout()
        rotations.addWidget(self.button('左へ90°', lambda: self.rotate(-90)))
        rotations.addWidget(self.button('右へ90°', lambda: self.rotate(90)))
        self.save_rotation_button = self.button('この画像を採用して保存', self.save_rotation, True)
        self.save_rotation_button.setToolTip('回転なしでも採用してDBを更新します。回転時は元画像を残し、補正コピーを保存して採用します。')
        rotations.addWidget(self.save_rotation_button)
        self.save_rotation_button.setEnabled(False)
        detail.addLayout(rotations)
        self.provenance = label('', 'muted')
        self.provenance.setWordWrap(True)
        detail.addWidget(self.provenance)
        self.artworks = QListWidget()
        self.artworks.setMaximumHeight(105)
        self.artworks.currentItemChanged.connect(self.show_artwork)
        detail.addWidget(self.artworks)
        self.select_button = self.button('表示中の画像を採用', self.select_artwork)
        detail.addWidget(self.select_button)
        title_layout.addWidget(label('日本語タイトル', 'section'))
        self.title_context = label('', 'muted')
        self.title_context.setWordWrap(True)
        title_layout.addWidget(self.title_context)
        self.title_candidates = QComboBox()
        self.title_candidates.currentIndexChanged.connect(self.use_title_candidate)
        title_layout.addWidget(self.title_candidates)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText('日本語タイトルを入力')
        title_layout.addWidget(self.title_edit)
        self.evidence = QPlainTextEdit()
        self.evidence.setReadOnly(True)
        self.evidence.setMinimumHeight(110)
        self.evidence.setMaximumHeight(180)
        title_layout.addWidget(self.evidence)
        title_layout.addWidget(self.button('タイトルを確定して保存', self.confirm_title, True))
        title_layout.addWidget(self.button('確定を解除して再照合の対象に戻す', self.unconfirm_title))
        title_layout.addStretch()
        splitter.addWidget(details)
        splitter.setSizes([870, 420])
        page.addWidget(splitter, 1)
        page.addWidget(label('PCのROM登録 → 日本語名のDB照合 → Skraper取得 → 内容・向きを確認して承諾 → 実機にないROM・画像・日本語名を転送', 'notice'))
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
        self.status_label.setText(message)
        if not self.closing and not self.task.cancel.is_set():
            QMessageBox.warning(self, '処理できませんでした', message)

    def task_finished(self):
        self.task.deleteLater()
        self.task = None
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

    def choose_title_setup(self):
        from .title_setup import TitleSetup
        games = self.visible_games()
        if len({(g['device'], g['system']) for g in games}) != 1:
            QMessageBox.warning(self, '実機・機種を選択', '実機と機種をそれぞれ1つに絞り込んでください。')
            return
        try:
            dialog = TitleSetup(self.db_path, games, self)
        except ValueError as error:
            QMessageBox.warning(self, 'タイトルを確認', str(error))
            return
        dialog.exec()

    def choose_transfer(self):
        games = self.visible_games()
        devices = {g['device'] for g in games}
        if len(devices) != 1:
            QMessageBox.warning(self, '実機を選択', '転送する実機を1台に絞り込んでください。')
            return
        checked = [g for g in games if g['id'] in self.model.checked_ids]
        targets = checked or games
        if len({g['system'] for g in targets}) != 1:
            QMessageBox.warning(self, '機種を選択', '転送する機種を1つに絞り込んでください。')
            return
        try:
            plan = with_db(self.db_path, lambda db: batch_plan(db, targets))
            items, titles, roms = plan['images'], plan['titles'], plan['roms']
            if not items and not titles and not roms:
                raise ValueError('転送する選択済み画像・確定済みの日本語ソフト名がありません')
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, '転送内容を確認', str(error))
            return
        dialog = TransferDialog(items, titles, self, roms=roms)
        profile = system_profile(targets[0]['system'])
        dialog.setWindowTitle(profile.name + ' · ROM・画像・日本語名を転送' + ('（実機未検証）' if not profile.verified else ''))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            dialog.password.clear()
            return
        items = items if dialog.images.isChecked() else []
        titles = titles if dialog.titles.isChecked() else {}
        roms = roms if dialog.roms.isChecked() else []
        verify_roms = dialog.verify_roms.isChecked()
        credentials = [dialog.host.text().strip(), dialog.user.text().strip(), dialog.password.text()]
        dialog.password.clear()
        if not items and not titles and not roms:
            return
        if not credentials[0] or not credentials[1]:
            QMessageBox.warning(self, '接続先', 'IPとユーザーを入力してください。')
            return
        def operation(cancelled, progress):
            try:
                return transfer_images(items, *credentials, Path(self.db_path).parent / 'transfer-backups',
                                       cancelled=cancelled, progress=progress, titles=titles, roms=roms,
                                       title_targets=plan['title_targets'], verify_existing_roms=verify_roms)
            finally:
                credentials.clear()
        def completed(result):
            message = '転送・照合済み: 画像 %d/%d枚、日本語ソフト名 %d/%d本。記録: %s' % (
                result['transferred'], len(items), result['titles_transferred'], len(titles), result['report'])
            message = 'ROM追加 %d本・既存スキップ %d本。' % (result['roms_transferred'], result['roms_skipped']) + message
            if result.get('cancelled'):
                message = ('転送を中断し、反映済みファイルを元に戻しました。' if result.get('rolled_back')
                           else '転送を中断しました。自動復旧が未完了です。転送記録から復旧してください。') + message
            self.status_label.setText(message)
            QMessageBox.information(self, '転送の結果', message + '\nConsole Modeを終了して入り直し、日本語名と画像を確認してください。必要に応じ画像を再最適化してください。')
        self.start_task(operation, completed, 'ROM・画像・日本語ソフト名を確認・転送中…')

    def choose_recovery(self):
        report, _ = QFileDialog.getOpenFileName(self, '復旧する転送記録を選択',
            str(Path(self.db_path).parent / 'transfer-backups'), '転送記録 (transfer.json)')
        if not report:
            return
        try:
            journal = json.loads(Path(report).read_text(encoding='utf-8'))
            validate_journal(journal)
        except (OSError, ValueError, KeyError, TypeError):
            QMessageBox.warning(self, '復旧記録', '復旧対応形式の転送記録を選択してください。')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('転送前の状態へ復旧')
        dialog.resize(800, 500)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label('既存ファイルを転送前に戻し、新規追加分は退避します。\n転送後に外部で変更されたファイルは保護し、復旧未完了として記録します。'))
        host = QLineEdit(journal['host'])
        user, password = QLineEdit('root'), QLineEdit()
        password.setEchoMode(QLineEdit.EchoMode.Password)
        for title, field in [('MiSTer IP（記録と同じ実機）', host), ('FTPユーザー', user), ('FTPパスワード（保存しません）', password)]:
            layout.addWidget(label(title))
            layout.addWidget(field)
        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        preview.setPlainText('\n'.join(row['remote_path'] + ' / ' + row['status'] for row in journal['items']))
        layout.addWidget(preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('この記録の反映を元に戻す')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            password.clear()
            return
        credentials = [host.text().strip(), user.text().strip(), password.text()]
        password.clear()
        def operation(cancelled, progress):
            try:
                return recover_transfer(report, *credentials)
            finally:
                credentials.clear()
        def completed(result):
            message = ('転送前の状態へ復旧しました。' if result['recovered']
                       else '復旧が未完了です。実機の変更とバックアップを確認してください。')
            self.status_label.setText(message + ' 記録: ' + result['report'])
            QMessageBox.information(self, '復旧の結果', message + '\n記録: ' + result['report'])
        self.start_task(operation, completed, '転送前の状態へ復旧中…')
        self.cancel_button.setEnabled(False)

    def choose_local_roms(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('PCのROMフォルダーを登録')
        layout = QVBoxLayout(dialog)
        device = QLineEdit(self.device.currentData() or 'home-mister')
        system, root = QComboBox(), QComboBox()
        system.setObjectName('registrationSystem')
        system_note = label('')
        system_note.setWordWrap(True)
        populate_systems(system)
        selected = system.findData(self.system.currentData())
        if selected >= 0:
            system.setCurrentIndex(selected)
        def update_roots():
            root.clear()
            profile = system_profile(system.currentData())
            root.addItems(profile.roots)
            system_note.setText(profile.usage_note)
        system.currentIndexChanged.connect(update_roots)
        update_roots()
        automatic = QCheckBox('登録後に日本語名をDBで照合する（インターネット接続が必要）')
        automatic.setChecked(True)
        for title, field in [('ライブラリ名', device), ('機種', system), ('MiSTerの転送先（機種ごとに固定）', root)]:
            layout.addWidget(label(title))
            layout.addWidget(field)
        layout.addWidget(system_note)
        folder_edit = QLineEdit()
        folder_edit.setObjectName('romFolder')
        folder_edit.setPlaceholderText('PCのROMフォルダー（参照で選択、または貼り付け）')
        def remembered():
            # Offer the folder last registered for this library and system.
            profile = with_db(self.db_path, lambda db: db.setting(
                'workflow/' + device.text().strip() + '/' + str(system.currentData()), {}))
            folder_edit.setText(profile.get('rom_folder', ''))
        def browse():
            chosen = QFileDialog.getExistingDirectory(dialog, 'PCのROMフォルダー', folder_edit.text())
            if chosen:
                folder_edit.setText(chosen)
        system.currentIndexChanged.connect(remembered)
        device.editingFinished.connect(remembered)
        remembered()
        layout.addWidget(label('PCのROMフォルダー'))
        folder_row = QHBoxLayout()
        folder_row.addWidget(folder_edit)
        browse_button = QPushButton('参照')
        browse_button.clicked.connect(browse)
        folder_row.addWidget(browse_button)
        layout.addLayout(folder_row)
        layout.addWidget(label('サブフォルダーも含めて対応ROMと、対象ROMが1本入ったZIPを登録します。\nmedia・隠しフォルダーは除外します。転送先は機種ルート直下へ集約し、同名は最初の1本を登録します。\nZIPは展開せず転送します。再登録では名前・画像を保持します。'))
        layout.addWidget(automatic)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('このフォルダーを登録')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted or not device.text().strip():
            return
        folder = folder_edit.text().strip().strip('"')
        if not folder or not Path(folder).is_dir():
            QMessageBox.warning(self, 'PCのROMフォルダー', 'PCのROMフォルダーを参照ボタンで選ぶか、存在するフォルダーのパスを入力してください。')
            return
        profile = system_profile(system.currentData())
        destination, device_name = root.currentText(), device.text().strip()
        use_lookup = automatic.isChecked()
        def operation(cancelled, progress):
            return with_db(self.db_path, lambda db: register_folder(
                db, folder, device_name, profile.id, destination,
                Path(self.db_path).parent / 'metadata' / 'retronian', lookup=use_lookup,
                cancelled=cancelled, progress=progress, matcher=match_titles))
        def completed(result):
            # A family folder (Game Boy / Color) lists all its members, not only the chosen one.
            self.workflow_selection = (device_name, '' if profile.siblings else profile.id)
            self.scope_key = None  # a new registration switches the folder filter back on
            message = 'PCのROM %d本を登録。転送先: %s' % (result['count'], destination)
            if result['duplicates']:
                message += ' / 別のサブフォルダーに同名のROMがあるため除外 %d本' % len(result['duplicates'])
            if result['skipped']:
                message += ' / %sのROMではないため対象外 %d本（%s%s）' % (profile.name, len(result['skipped']),
                    '、'.join(result['skipped'][:3]), '…' if len(result['skipped']) > 3 else '')
            if result['lookup']:
                message += ' / ' + self.lookup_summary(result['lookup'])
            if result['lookup_error']:
                message += ' / 日本語名の照合は未完了: ' + result['lookup_error']
            self.saved(message)
        self.start_task(operation, completed,
                        'PCのROMを登録中…')

    @staticmethod
    def lookup_summary(result):
        offline = '（取得失敗のため保存済みDBを使用）' if 'offline-cache' in result['modes'].values() else ''
        if result.get('warnings'):
            offline += '（DB取得失敗 %d件・取得済みDBと既存候補で照合）' % len(result['warnings'])
        stamps = [t for t in result.get('fetched_at', {}).values() if t]
        fetched = ' / DB取得日 ' + time.strftime('%Y-%m-%d', time.localtime(min(stamps))) if stamps else ''
        return '日本語名候補 %d本 / 複数候補 %d本 / 該当なし %d本 / 確定済み保持 %d本%s%s' % (
            result['matched'], result['ambiguous'], result['missing'], result['confirmed'], fetched, offline)

    def lookup_finished(self, result, suffix):
        self.saved(self.lookup_summary(result) + suffix)
        if result.get('warnings'):
            QMessageBox.warning(self, '一部の日本語名DBを取得できませんでした',
                                '\n'.join(result['warnings']) + '\n取得できたDBと既存候補で照合しました。')
        if 'offline-cache' in result['modes'].values():
            QMessageBox.warning(self, '最新のDBを取得できませんでした',
                                '通信に失敗したため、保存済みの古い日本語名DBで照合しました。\nネットワークを確認し、もう一度実行してください。')
        if result.get('updates'):
            self.title_updates = result['updates']
            self.after_task = self.open_title_updates

    def choose_title_lookup(self):
        self.run_title_lookup(False)

    def choose_title_db_export(self):
        from metadata.title_catalog import export_catalog, write_catalog
        games = self.visible_games()
        checked = [g for g in games if g['id'] in self.model.checked_ids]
        targets = checked or games
        if not targets:
            QMessageBox.information(self, 'ROMを登録', '書き出すROMを登録・表示してください。')
            return
        default = Path(self.db_path).parent / 'title-db' / ('titles-' + time.strftime('%Y%m%d-%H%M%S') + '.json')
        filename, _ = QFileDialog.getSaveFileName(self, '表示中またはチェックしたROMの日本語名DBを書き出す',
                                                str(default), 'JSON (*.json)')
        if not filename:
            return
        def operation(cancelled, progress):
            document = with_db(self.db_path, lambda db: export_catalog(db, targets, cancelled=cancelled, progress=progress))
            if cancelled():
                raise InterruptedError('書き出しを中断しました')
            return write_catalog(filename, document)
        self.start_task(operation, lambda result: self.saved('日本語名DB %d件を書き出しました: %s' % (
            result['entries'], result['path'])), '日本語名DBを出典・判断履歴付きで書き出し中…')

    def choose_title_db_merge(self):
        from metadata.title_catalog import read_catalog, merge_catalogs, write_catalog
        filenames, _ = QFileDialog.getOpenFileNames(self, 'マージする日本語名DB JSONを選択',
                                                    str(Path(self.db_path).parent / 'title-db'), 'JSON (*.json)')
        if not filenames:
            return
        default = Path(self.db_path).parent / 'title-db' / ('merged-' + time.strftime('%Y%m%d-%H%M%S') + '.json')
        output, _ = QFileDialog.getSaveFileName(self, 'マージ結果を新しいJSONに保存', str(default), 'JSON (*.json)')
        if not output:
            return
        def operation(cancelled, progress):
            document = merge_catalogs((read_catalog(p) for p in filenames), cancelled=cancelled)
            if cancelled():
                raise InterruptedError('マージを中断しました')
            return write_catalog(output, document)
        self.start_task(operation, lambda result: self.saved('日本語名DB %d件をマージ / 競合 %d件: %s' % (
            result['entries'], result['conflicts'], result['path'])), '日本語名DBのJSONをマージ中…')

    def choose_title_refresh(self):
        self.run_title_lookup(True)

    def run_title_lookup(self, latest):
        games = self.visible_games()
        checked = [g for g in games if g['id'] in self.model.checked_ids]
        targets = checked or games
        if not targets:
            QMessageBox.information(self, 'ROMを登録', '先にPCのROMフォルダーを登録してください。')
            return
        self.start_task(lambda cancelled, progress: with_db(self.db_path, lambda db:
            match_titles(db, targets, Path(self.db_path).parent / 'metadata' / 'retronian',
                         cancelled=cancelled, progress=progress, refresh=latest, recheck_confirmed=latest)),
            lambda result: self.lookup_finished(result, '。内容を確認して承諾してください。'),
            ('最新の日本語名DBを取得して確定済みと比較中…' if latest
             else '日本語名を公開DBで照合中…（初回は機種別DBを取得）'))

    def open_title_updates(self):
        if self.closing:
            return
        updates = self.title_updates
        dialog = QDialog(self)
        dialog.setWindowTitle('確定済みタイトルの更新候補')
        dialog.resize(720, 460)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label('最新DBと異なる確定済みタイトルが %d本あります' % len(updates), 'section'))
        layout.addWidget(label('チェックしたものだけ新しいタイトルへ更新します。更新しない確定はそのまま保持されます。', 'muted'))
        items = QListWidget()
        for update in updates:
            item = QListWidgetItem('%s\n  %s  →  %s' % (update['rom_filename'], update['current'], update['proposed']))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, update)
            items.addItem(item)
        layout.addWidget(items)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('チェックしたタイトルを更新')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = [items.item(i).data(Qt.ItemDataRole.UserRole) for i in range(items.count())
                  if items.item(i).checkState() == Qt.CheckState.Checked]
        if chosen:
            def apply(db):
                return sum(db.update_confirmed_title(u['game_id'], u['current'], u['proposed']) for u in chosen)
            self.start_task(lambda cancelled, progress: with_db(self.db_path, apply),
                            lambda count: self.saved('確定済みタイトルを %d本更新しました（%d本は確認後に変更されたため保持）' % (
                                count, len(chosen) - count)), '確定済みタイトルを更新中…')

    def choose_skraper(self):
        from .skraper_dialog import SkraperDialog
        device = self.device.currentData()
        systems = {g['system'] for g in self.visible_games()}
        # Skraper lists Game Boy and Game Boy Color as separate systems, each with its own folders.
        if not device or len(systems) != 1:
            QMessageBox.information(self, 'ライブラリ・機種を選択', 'PCのROMを登録し、ライブラリと機種を1つずつ選んでください。')
            return
        system = next(iter(systems))
        key = 'workflow/' + device + '/' + system
        def settings(db):
            folder = db.setting('skraper/folder', '')
            legacy = db.setting('skraper/executable', '')
            if not folder and legacy:
                folder = str(Path(legacy).parent)
            return db.setting(key), folder
        profile, folder = with_db(self.db_path, settings)
        if not profile:
            QMessageBox.information(self, 'PCのROMを登録', '「1 · PCのROMを登録」でSkraperの参照元を指定してください。')
            return
        # Follow Skraper's own settings (ROM folder, image output) instead of asking the user to change them.
        detected = system_paths(folder, system_profile(system).screenscraper_id) if folder else None
        source_folder = profile['rom_folder']
        image_folder = detected['image_folder'] if detected else profile.get('image_folder', '')
        dialog = SkraperDialog(dict(profile, image_folder=image_folder), folder, self, detected)
        outcome = dialog.exec()
        try:
            profile = dict(dialog.validated_profile(), rom_folder=source_folder)
            def save(db):
                db.set_setting(key, profile)
                db.set_setting('skraper/folder', dialog.folder.text().strip())
            with_db(self.db_path, save)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Skraperの保存先', str(error))
            return
        if outcome == QDialog.DialogCode.Accepted:
            games = [g for g in self.model.games if g['device'] == device and g['system'] == system]
            self.start_task(lambda cancelled, progress: plan_import(profile['image_folder'], games,
                cancelled=cancelled, progress=progress), self.preview_import, 'Skraperの取得画像を照合中…')

    def choose_scan(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('実機のROM一覧を取得（読み取りのみ）')
        layout = QVBoxLayout(dialog)
        host, user, password = QLineEdit(), QLineEdit('root'), QLineEdit()
        password.setEchoMode(QLineEdit.EchoMode.Password)
        device = QLineEdit(self.device.currentData() or 'home-mister')
        system = QComboBox()
        populate_systems(system)
        selected = system.findData(self.system.currentData())
        if selected >= 0:
            system.setCurrentIndex(selected)
        root = QComboBox()
        def update_roots():
            root.clear()
            root.addItems(system_profile(system.currentData()).roots)
        system.currentIndexChanged.connect(update_roots)
        update_roots()
        for title, field in [('MiSTer IP', host), ('FTPユーザー', user), ('FTPパスワード（保存しません）', password),
                             ('ライブラリ名', device), ('機種', system), ('ROMフォルダー', root)]:
            layout.addWidget(label(title))
            layout.addWidget(field)
        layout.addWidget(label('ROMの内容は取得せず、ファイル名をPCの一覧へ登録します。\n転送は機種ルート直下のみ対応です。子フォルダーの項目は一覧登録だけに使えます。'))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('一覧を取得')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        host.setFocus()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            password.clear()
            return
        profile = system_profile(system.currentData())
        rom_root = root.currentText()
        device_name = device.text().strip()
        credentials = [host.text().strip(), user.text().strip(), password.text()]
        password.clear()
        if not credentials[0] or not credentials[1] or not device_name:
            QMessageBox.warning(self, '接続先', 'IP・ユーザー・ライブラリ名を入力してください。')
            return
        def operation(cancelled, progress):
            try:
                with FTPReader(*credentials) as client:
                    inventory = client.scan(rom_root, cancelled=cancelled, extensions=profile.extensions)
                if cancelled():
                    raise InterruptedError('一覧の取得を中断しました')
                def save(db):
                    for path in inventory['roms']:
                        db.upsert_game(device_name, path, system=profile.id)
                with_db(self.db_path, save)
                return len(inventory['roms'])
            finally:
                credentials.clear()
        self.start_task(operation, lambda count: self.saved(profile.name + 'のROM一覧 %d本を登録しました' % count),
                        profile.name + 'の実機一覧を取得中…')

    def cancel_task(self):
        if self.task:
            self.task.cancel.set()
            self.status_label.setText('中断しています。完了済みの候補は保持されます。')

    def refresh(self):
        self.start_task(lambda cancelled, progress: load_library(self.db_path, cancelled, progress),
                        self.library_loaded, 'ローカルDBを読み込み中…')

    def library_loaded(self, games):
        device = self.device.currentData()
        selection = getattr(self, 'workflow_selection', None)
        if selection:
            device = selection[0]
            self.workflow_selection = None
        selected = self.current_game['id'] if self.current_game else None
        self.device.blockSignals(True)
        self.device.clear()
        self.device.addItem('すべての実機', '')
        for name in sorted({g['device'] for g in games}):
            self.device.addItem(name, name)
        preferred = device if device is not None else ('home-mister' if any(g['device'] == 'home-mister' for g in games) else '')
        self.device.setCurrentIndex(max(0, self.device.findData(preferred)))
        self.device.blockSignals(False)
        self.update_scope(games)
        self.model.replace(games)
        if selection:
            self.system.setCurrentIndex(max(0, self.system.findData(selection[1])))
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
            ids = {g['id'] for g in games if g['device'] == scope['device'] and g['system'] in members
                   and g.get('local_rom_path') and folder in Path(g['local_rom_path']).parents}
        key = (scope['device'], scope['system'], scope['rom_folder']) if scope else None
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
        self.proxy.device = self.device.currentData() or ''
        self.proxy.system = self.system.currentData() or ''
        self.proxy.query = self.search.text()
        self.proxy.pending = self.pending_only.isChecked()
        self.proxy.ids = self.scope_ids if self.scope_only.isChecked() else None
        self.proxy.invalidateFilter()
        if self.proxy.rowCount():
            self.table.selectRow(0)
        else:
            self.current_game = self.current_artwork = None
            self.artworks.clear()
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
        self.approve_button.setEnabled(count > 0 and self.task is None)

    def approve_checked(self):
        games = [g for g in self.visible_games() if g['id'] in self.model.checked_ids]
        if not games or self.task is not None:
            return
        plan = approval_plan(games)
        dialog = QDialog(self)
        dialog.setWindowTitle('チェックした候補の一括承諾')
        dialog.resize(860, 560)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label('%d本の候補をまとめて確認' % len(plan), 'section'))
        layout.addWidget(label('確定済み・選択済みは保持します。複数候補は個別確認のため対象外です。', 'muted'))
        titles = QCheckBox('日本語名の候補を確定する')
        titles.setChecked(True)
        images = QCheckBox('画像候補を採用する（補正済み画像があれば優先）')
        images.setChecked(True)
        layout.addWidget(titles)
        layout.addWidget(images)
        report = QPlainTextEdit()
        report.setReadOnly(True)
        layout.addWidget(report, 1)
        summary = label('', 'muted')
        layout.addWidget(summary)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('この内容でまとめて承諾')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        def preview():
            lines = []
            for item in plan:
                title = item['title'] if titles.isChecked() else None
                artwork = item['artwork'] if images.isChecked() else None
                lines.append('%s\n  日本語名: %s\n  画像: %s\n' % (item['rom_filename'],
                    title or (item['title_note'] if titles.isChecked() else '変更しない'),
                    (Path(artwork['local_path']).name + ' / ' + item['image_note']) if artwork else
                    (item['image_note'] if images.isChecked() else '変更しない')))
            report.setPlainText('\n'.join(lines))
            title_count = sum(i['title'] is not None for i in plan) if titles.isChecked() else 0
            image_count = sum(i['artwork'] is not None for i in plan) if images.isChecked() else 0
            summary.setText('日本語名 %d件 / 画像 %d件 · PC内へ保存（MiSTerへの転送なし）' % (title_count, image_count))
            buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(title_count + image_count > 0)
        titles.toggled.connect(preview)
        images.toggled.connect(preview)
        preview()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        use_titles, use_images = titles.isChecked(), images.isChecked()
        def accepted(result):
            self.model.set_checks(set(result['applied_ids']), False)
            self.saved('一括承諾: 日本語名 %d件 / 画像 %d件 / 競合 %d件 / エラー %d件%s' %
                (result['titles'], result['images'], result['conflicts'], result['errors'],
                 ' / 中断' if result['cancelled'] else ''))
        self.start_task(lambda cancelled, progress: with_db(self.db_path, lambda db:
            apply_approval(db, plan, titles=use_titles, images=use_images, cancelled=cancelled, progress=progress)),
            accepted, 'チェックした候補を一括承諾中…')

    def show_game(self, index, previous=None):
        game = index.data(Qt.ItemDataRole.UserRole)
        if not game:
            return
        self.current_game = game
        self.title_context.setText(game['rom_filename'])
        self.title_candidates.blockSignals(True)
        self.title_candidates.clear()
        self.title_candidates.addItem('手動で入力する', None)
        for candidate in group_candidates(game['_titles']):
            suffix = '（推奨・ROM内容一致）' if candidate['recommended'] and candidate['rank'] == 3 else '（候補）'
            self.title_candidates.addItem(candidate['japanese_title'] + suffix, candidate)
        self.title_candidates.blockSignals(False)
        self.title_candidates.setCurrentIndex(0)
        self.title_edit.setText(game['japanese_title'] or '')
        self.evidence.setPlainText('ユーザー確定済み' if game['match_status'] == 'confirmed' else '候補を選ぶか、タイトルを入力して確定してください。')
        self.artworks.clear()
        self.current_artwork = None
        self.pixmap = None
        self.cover.setPixmap(QPixmap())
        self.cover.setText('画像候補がありません')
        self.provenance.setText('')
        artworks = sorted(game['_artworks'], key=lambda a: (a['local_path'] != game['artwork_path'],
                         not a['source'].startswith('skraper'), a['region'] != 'JP', -a['id']))
        for artwork in artworks:
            chosen = '  ✓ 選択済み' if artwork['local_path'] == game['artwork_path'] else ''
            corrected = ' · 補正済み' if artwork.get('rotation_degrees') else ''
            item = QListWidgetItem('%s · %s%s%s' % (artwork['source'], artwork['region'], corrected, chosen))
            item.setData(Qt.ItemDataRole.UserRole, artwork)
            item.setToolTip(artwork['local_path'])
            self.artworks.addItem(item)
        if artworks:
            preferred = next((i for i, a in enumerate(artworks) if a['id'] == self.desired_artwork_id), 0)
            if artworks[preferred]['id'] == self.desired_artwork_id:
                self.desired_artwork_id = None
            self.artworks.setCurrentRow(preferred)
        self.update_rotation_controls()

    def use_title_candidate(self, index):
        candidate = self.title_candidates.itemData(index)
        if candidate:
            self.title_edit.setText(candidate['japanese_title'])
            blocks = []
            labels = {'retronian': 'Retronian GameDB', 'redump': 'Redump',
                      'gamedatabase': 'GameDataBase by PigSaint'}
            for evidence in candidate.get('evidence', [candidate]):
                blocks.append('%s / %s\n表記: %s\n一致度 %s · %s\n出典: %s\n元データ: %s' % (
                    labels.get(evidence.get('provider'), '手動・その他'), evidence.get('license', '利用条件は出典を確認'),
                    evidence['japanese_title'], evidence.get('score', '—'), evidence.get('reason', ''),
                    evidence.get('source', '不明'), evidence.get('upstream_source', '不明')))
                stamp = evidence.get('fetched_at')
                if isinstance(stamp, (int, float)):
                    blocks[-1] += '\nDB取得日: ' + time.strftime('%Y-%m-%d', time.localtime(stamp))
            self.evidence.setPlainText('\n\n'.join(blocks))

    def show_artwork(self, item, previous=None):
        self.current_artwork = None
        self.pixmap = None
        self.angle = 0
        if item is None:
            self.update_rotation_controls()
            return
        artwork = item.data(Qt.ItemDataRole.UserRole)
        self.current_artwork = artwork
        path = Path(artwork['local_path'])
        self.update_provenance()
        self.provenance.setToolTip(artwork['url'])
        try:
            if path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError('画像が大きすぎます')
            reader = QImageReader(str(path))
            size = reader.size()
            if not size.isValid() or max(size.width(), size.height()) > 8192:
                raise ValueError('画像サイズを確認できません')
            reader.setScaledSize(size.scaled(QSize(1600, 1600), Qt.AspectRatioMode.KeepAspectRatio))
            image = reader.read()
            if image.isNull():
                raise ValueError('画像を読み込めません')
            self.pixmap = QPixmap.fromImage(image)
            self.draw_cover()
        except (OSError, ValueError) as error:
            self.cover.setPixmap(QPixmap())
            self.cover.setText(str(error))
        self.update_rotation_controls()

    def draw_cover(self):
        if self.pixmap:
            display = self.pixmap.transformed(QTransform().rotate(self.angle), Qt.TransformationMode.SmoothTransformation)
            self.cover.setPixmap(display.scaled(self.cover.size() - QSize(20, 20),
                                 Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def rotate(self, degrees):
        self.angle = (self.angle + degrees) % 360
        self.draw_cover()
        self.update_provenance()
        self.update_rotation_controls()

    def update_rotation_controls(self):
        self.save_rotation_button.setEnabled(self.task is None and self.current_artwork is not None
                                             and self.pixmap is not None)
        self.save_rotation_button.setText('この向きで保存・採用' if self.angle else 'この画像を採用して保存')

    def update_provenance(self):
        artwork = self.current_artwork
        if artwork:
            state = ' · 回転%d°（未保存）' % self.angle if self.angle else (' · 補正済み' if artwork.get('rotation_degrees') else '')
            self.provenance.setText('%s / 地域 %s%s\n%s' % (artwork['source'], artwork['region'], state,
                                    Path(artwork['local_path']).name))

    def save_rotation(self):
        if not self.current_game or not self.current_artwork or not self.pixmap:
            return
        if self.angle == 0:
            self.select_artwork()
            return
        game_id, artwork, angle = self.current_game['id'], self.current_artwork.copy(), self.angle
        output = Path(self.db_path).parent / 'artwork-edits' / ('game-%d' % game_id)
        def save(cancelled, progress):
            from artwork.orientation import rotate_png
            result = rotate_png(artwork['local_path'], artwork['sha256'], angle, output, cancelled=cancelled)
            def register(db):
                artwork_id = db.register_rotated_artwork(game_id, artwork['id'], result)
                db.select_artwork(game_id, artwork_id)
                return artwork_id
            result['artwork_id'] = with_db(self.db_path, register)
            return result
        def done(result):
            self.desired_artwork_id = result['artwork_id']
            self.saved('補正画像を保存・採用し、DBを更新しました。')
        self.start_task(save, done, '元の解像度で補正PNGを保存中…')

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'cover'):
            self.draw_cover()

    def saved(self, message):
        self.status_label.setText(message)
        self.refresh_message = message
        self.pending_refresh = True

    def confirm_title(self):
        if not self.current_game:
            return
        game_id, title = self.current_game['id'], self.title_edit.text()
        self.start_task(lambda cancelled, progress: with_db(self.db_path, lambda db: db.confirm(game_id, title)),
                        lambda result: self.saved('タイトルをローカルで確定しました'), 'タイトルを保存中…')

    def unconfirm_title(self):
        game = self.current_game
        if not game or game['match_status'] != 'confirmed':
            return
        answer = QMessageBox.question(self, '確定の解除', '「%s」の確定を解除します。\n画像と候補は残り、次の照合で新しい候補が提案されます。' % game['japanese_title'])
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.start_task(lambda cancelled, progress: with_db(self.db_path, lambda db: db.unconfirm(game['id'])),
                        lambda result: self.saved('確定を解除しました'), '確定を解除中…')

    def select_artwork(self):
        if not self.current_game or not self.current_artwork:
            return
        if self.angle:
            self.save_rotation()
            return
        game, artwork = self.current_game.copy(), self.current_artwork.copy()
        if game['artwork_path'] and game['artwork_path'] != artwork['local_path']:
            answer = QMessageBox.question(self, '選択画像の変更', 'このゲームのローカル選択を、表示中の画像に変更しますか？\n画像ファイル自体は変更しません。')
            if answer != QMessageBox.StandardButton.Yes:
                return
        def save(cancelled, progress):
            if inspect_png(Path(artwork['local_path'])) != artwork['sha256']:
                raise ValueError('取得後に画像が変更されています。再度取り込んで確認してください。')
            return with_db(self.db_path, lambda db: db.select_artwork(game['id'], artwork['id']))
        self.start_task(save, lambda result: self.saved('画像をローカルで選択しました'), '画像を検証して選択中…')

    def choose_import(self):
        device = self.device.currentData()
        if not device:
            QMessageBox.information(self, '実機の選択', '取り込み先のライブラリを上部で選んでください。')
            return
        systems = {g['system'] for g in self.visible_games()}
        if len(systems) != 1:
            QMessageBox.warning(self, '機種を選択', '画像を取り込む機種を1つに絞り込んでください。')
            return
        selected_system = next(iter(systems))
        profile = with_db(self.db_path, lambda db: db.setting('workflow/' + device + '/' + selected_system, {}))
        root = QFileDialog.getExistingDirectory(self, 'Skraperの出力画像フォルダー', profile.get('image_folder', str(
            (Path(self.db_path).parent / 'skraper' / selected_system / 'images').resolve())))
        if not root:
            return
        games = [g for g in self.model.games if g['device'] == device and g['system'] == selected_system]
        self.start_task(lambda cancelled, progress: plan_import(root, games, cancelled=cancelled, progress=progress),
                        self.preview_import, '画像とROM名を照合中…')

    def preview_import(self, plan):
        # Present the concrete plan after the scanning thread has stopped.
        self.import_plan = plan
        self.after_task = self.open_import_dialog

    def open_import_dialog(self):
        if self.closing:
            return
        plan = self.import_plan
        dialog = QDialog(self)
        dialog.setWindowTitle('Skraper画像の取り込み確認')
        dialog.resize(750, 470)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label('%d件の画像候補を登録します' % plan['ready_count'], 'section'))
        layout.addWidget(label('画像は管理ライブラリへコピーします。同じ画像の内容が更新された場合は選択を解除し、再確認が必要です。', 'muted'))
        regions = QComboBox()
        for text, value in [('不明（初期値）', 'UNKNOWN'), ('日本 / JP', 'JP'), ('米国 / US', 'US'), ('欧州 / EU', 'EU'), ('世界 / WORLD', 'WORLD')]:
            regions.addItem(text, value)
        layout.addWidget(label('Skraperで指定した画像地域を記録（ファイル名からは推測しません）'))
        layout.addWidget(regions)
        report = QPlainTextEdit()
        report.setReadOnly(True)
        report.setPlainText('\n'.join('%s  %s — %s' % ('✓' if i['status'] == 'ready' else '・', i['filename'], i['reason']) for i in plan['items']) or 'PNG画像がありません')
        layout.addWidget(report)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('候補として取り込む')
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(plan['ready_count'] > 0)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            region = regions.currentData()
            self.start_task(lambda cancelled, progress: with_db(self.db_path,
                            lambda db: apply_import(db, plan, region, library=Path(self.db_path).parent / 'images',
                                                    cancelled=cancelled, progress=progress)),
                            lambda result: self.saved('候補 %d件を登録 / エラー %d件%s%s' %
                                (result['imported'], result['errors'], ' / 中断' if result['cancelled'] else '',
                                 (' / 内容が更新された画像 %d件' % result['updated']) if result['updated'] else '') +
                                (' / 選択中の画像と別の新候補 %d件（個別に採用を確認）' % result['unselected']
                                 if result['unselected'] else '')),
                            '画像候補を登録中…')

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

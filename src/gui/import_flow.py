"""PC ROM registration and Skraper image import actions of the main window."""
from pathlib import Path
import time

from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from artwork.skraper_config import system_paths
from artwork.skraper_import import plan_import, apply_import
from metadata.titles import match_titles
from systems import system_profile
from workflow import register_folder
from .style import label
from .system_choices import populate_systems
from .tasks import with_db


class ImportMixin:
    def choose_local_roms(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('PCのROMフォルダーから一覧に追加')
        layout = QVBoxLayout(dialog)
        system, root = QComboBox(), QComboBox()
        system.setObjectName('registrationSystem')
        root.setObjectName('registrationDestinationChoices')
        fixed_root = label('')
        fixed_root.setObjectName('registrationDestination')
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
            root.setVisible(len(profile.roots) > 1)
            fixed_root.setText(profile.root)
            fixed_root.setVisible(len(profile.roots) == 1)
            system_note.setText(profile.usage_note)
        system.currentIndexChanged.connect(update_roots)
        update_roots()
        automatic = QCheckBox('一覧への追加後に日本語名をDBで照合する（インターネット接続が必要）')
        automatic.setChecked(True)
        for title, field in [('機種', system), ('MiSTerの転送先', root)]:
            layout.addWidget(label(title))
            layout.addWidget(field)
        layout.addWidget(fixed_root)
        layout.addWidget(system_note)
        folder_edit = QLineEdit()
        folder_edit.setObjectName('romFolder')
        folder_edit.setPlaceholderText('PCのROMフォルダー（参照で選択、または貼り付け）')
        def remembered():
            # Offer the folder last registered for this system.
            profile = with_db(self.db_path, lambda db: db.setting(
                'workflow/' + str(system.currentData()), {}))
            folder_edit.setText(profile.get('rom_folder') or str(self.rom_folders[system.currentData()]))
        def browse():
            chosen = QFileDialog.getExistingDirectory(dialog, 'PCのROMフォルダー', folder_edit.text())
            if chosen:
                folder_edit.setText(chosen)
        system.currentIndexChanged.connect(remembered)
        remembered()
        layout.addWidget(label('PCのROMフォルダー'))
        folder_row = QHBoxLayout()
        folder_row.addWidget(folder_edit)
        browse_button = QPushButton('参照')
        browse_button.clicked.connect(browse)
        folder_row.addWidget(browse_button)
        layout.addLayout(folder_row)
        layout.addWidget(label('選んだフォルダー内のROMを検索し、一覧に追加します（サブフォルダーも検索）。\nMiSTerへの転送は、追加後に一覧でゲームを選んで実行します。'))
        layout.addWidget(automatic)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('ROMを一覧に追加')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        folder = folder_edit.text().strip().strip('"')
        if not folder or not Path(folder).is_dir():
            QMessageBox.warning(self, 'PCのROMフォルダー', 'PCのROMフォルダーを参照ボタンで選ぶか、存在するフォルダーのパスを入力してください。')
            return
        profile = system_profile(system.currentData())
        destination = root.currentText()
        use_lookup = automatic.isChecked()
        def operation(cancelled, progress):
            return with_db(self.db_path, lambda db: register_folder(
                db, folder, profile.id, destination,
                Path(self.db_path).parent / 'metadata' / 'retronian', lookup=use_lookup,
                cancelled=cancelled, progress=progress, matcher=match_titles))
        def completed(result):
            # A family folder (Game Boy / Color) lists all its members, not only the chosen one.
            self.workflow_selection = '' if profile.siblings else profile.id
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

    def choose_skraper(self):
        from .skraper_dialog import SkraperDialog
        systems = {g['system'] for g in self.visible_games()}
        # Skraper lists Game Boy and Game Boy Color as separate systems, each with its own folders.
        if len(systems) != 1:
            QMessageBox.information(self, '機種を選択', 'PCのROMを登録し、機種を1つ選んでください。')
            return
        system = next(iter(systems))
        key = 'workflow/' + system
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
            games = [g for g in self.model.games if g['system'] == system]
            self.start_task(lambda cancelled, progress: plan_import(profile['image_folder'], games,
                cancelled=cancelled, progress=progress), self.preview_import, 'Skraperの取得画像を照合中…')

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
        layout.addWidget(label('取り込む画像の地域（Skraper側で指定した地域を選択）'))
        layout.addWidget(regions)
        region_summary = label('', 'muted')
        region_summary.setObjectName('importRegionSummary')
        region_summary.setWordWrap(True)
        layout.addWidget(region_summary)
        def update_region_summary():
            region_summary.setText('取り込み時に%d件の画像候補へ「%s」を記録します。\n'
                '画像や取り込み対象は変わりません。日本版の画像を取得するにはSkraper側で設定して再取得してください。'
                % (plan['ready_count'], regions.currentText()))
        regions.currentIndexChanged.connect(update_region_summary)
        update_region_summary()
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
                            lambda result: self.saved('候補 %d件を登録（画像地域: %s） / エラー %d件%s%s' %
                                (result['imported'], region, result['errors'], ' / 中断' if result['cancelled'] else '',
                                 (' / 内容が更新された画像 %d件' % result['updated']) if result['updated'] else '') +
                                (' / 選択中の画像と別の新候補 %d件（個別に採用を確認）' % result['unselected']
                                 if result['unselected'] else '')),
                            '画像候補を登録中…')

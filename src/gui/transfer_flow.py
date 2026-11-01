"""Transfer and recovery actions of the main window."""
import json
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QLineEdit, QMessageBox, QPlainTextEdit, QVBoxLayout,
)

from mister.artwork_transfer import transfer_images
from mister.recovery import recover_transfer, validate_journal, pending_recovery_reports
from systems import system_profile
from workflow import review_transfer_plan, accept_transfer_review
from .connection import prefill, commit, remember_checkbox
from .style import label
from .tasks import with_db
from .transfer_dialog import TransferDialog


class TransferMixin:
    def choose_transfer(self):
        games = self.visible_games()
        targets = [g for g in games if g['id'] in self.model.checked_ids]
        if not targets:
            QMessageBox.information(self, 'ゲームを選択', '転送するゲームをチェックしてください。')
            return
        if len({g['system'] for g in targets}) != 1:
            QMessageBox.warning(self, '機種を選択', '転送する機種を1つに絞り込んでください。')
            return
        try:
            plan = with_db(self.db_path, lambda db: review_transfer_plan(db, targets))
            items, titles, roms = plan['images'], plan['titles'], plan['roms']
            if not items and not titles and not roms:
                raise ValueError('転送する選択済み画像・確定済みの日本語ソフト名がありません')
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, '転送内容を確認', str(error))
            return
        dialog = TransferDialog(items, titles, self, roms=roms, db_path=self.db_path, review_rows=plan['review_rows'])
        profile = system_profile(targets[0]['system'])
        dialog.setWindowTitle(profile.name + ' · ROM・画像・日本語名を転送')
        if dialog.exec() != QDialog.DialogCode.Accepted:
            dialog.password.clear()
            return
        items = items if dialog.images.isChecked() else []
        titles = titles if dialog.titles.isChecked() else {}
        roms = roms if dialog.roms.isChecked() else []
        verify_roms = dialog.verify_roms.isChecked()
        send_images, send_titles, send_roms = dialog.images.isChecked(), dialog.titles.isChecked(), dialog.roms.isChecked()
        credentials = [dialog.host.text().strip(), dialog.user.text().strip(), dialog.password.text()]
        commit(self.db_path, *credentials, dialog.remember.isChecked())
        dialog.password.clear()
        if not items and not titles and not roms:
            return
        if not credentials[0] or not credentials[1]:
            QMessageBox.warning(self, '接続先', 'IPとユーザーを入力してください。')
            return
        def operation(cancelled, progress):
            try:
                accepted = with_db(self.db_path, lambda db: accept_transfer_review(db, targets, plan,
                    images=send_images, titles=send_titles, roms=send_roms, cancelled=cancelled))
                return transfer_images(items, *credentials, Path(self.db_path).parent / 'transfer-backups',
                                       cancelled=cancelled, progress=progress, titles=titles, roms=roms,
                                       title_targets=accepted['title_targets'], verify_existing_roms=verify_roms)
            finally:
                credentials.clear()
        def completed(result):
            message = '転送・照合済み: 画像 %d/%d枚、日本語ソフト名 %d/%d本。記録: %s' % (
                result['transferred'], len(items), result['titles_transferred'], len(titles), result['report'])
            message = 'ROM追加 %d本・既存スキップ %d本。' % (result['roms_transferred'], result['roms_skipped']) + message
            if result.get('cancelled'):
                message = ('転送を中断し、反映済みファイルを元に戻しました。' if result.get('rolled_back')
                           else '転送を中断しました。自動復旧が未完了です。転送記録から復旧してください。') + message
            self.saved(message)
            self.update_recovery_action()
            if self.recovery_reports:
                message += '\n画面上部の「転送を復旧」から復旧を再実行できます。'
            QMessageBox.information(self, '転送の結果', message + '\nConsole Modeを終了して入り直し、日本語名と画像を確認してください。必要に応じ画像を再最適化してください。')
        self.start_task(operation, completed, 'ROM・画像・日本語ソフト名を確認・転送中…')

    def update_recovery_action(self):
        self.recovery_reports = pending_recovery_reports(Path(self.db_path).parent / 'transfer-backups')
        self.recovery_button.setVisible(bool(self.recovery_reports))
        self.recovery_button.setEnabled(self.task is None and bool(self.recovery_reports))

    def choose_recovery(self):
        report, _ = QFileDialog.getOpenFileName(self, '復旧する転送記録を選択',
            self.recovery_reports[0] if self.recovery_reports else str(Path(self.db_path).parent / 'transfer-backups'),
            '転送記録 (transfer.json)')
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
        for title, field in [('MiSTer IP（記録と同じ実機）', host), ('FTPユーザー', user), ('FTPパスワード', password)]:
            layout.addWidget(label(title))
            layout.addWidget(field)
        remember = remember_checkbox()
        layout.addWidget(remember)
        prefill(self.db_path, host, user, password, remember, only_host=journal['host'])
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
        commit(self.db_path, *credentials, remember.isChecked(), only_host=journal['host'])
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
            self.update_recovery_action()
            QMessageBox.information(self, '復旧の結果', message + '\n記録: ' + result['report'])
        self.start_task(operation, completed, '転送前の状態へ復旧中…')
        self.cancel_button.setEnabled(False)

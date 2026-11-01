import base64
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
HAS_QT = importlib.util.find_spec('PyQt6') is not None


@unittest.skipUnless(HAS_QT, 'optional GUI dependency PyQt6 is not installed')
class GUITests(unittest.TestCase):
    def test_reload_hides_absent_roms_and_restores_metadata_from_configured_folder(self):
        from gui.app import MainWindow
        from database.store import Store
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, current = root / 'old', root / 'current'
            old.mkdir()
            current.mkdir()
            source = old / 'Game.nes'
            source.write_bytes(b'rom')
            image = root / 'Game.png'
            image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='))
            db_path = root / 'db'
            db = Store(db_path)
            ident = db.register_local_rom('NES', source)
            db.confirm(ident, '確定名')
            db.save_artwork(ident, 'test', 'JP', image.as_uri(), image, hashlib.sha256(image.read_bytes()).hexdigest())
            db.set_setting('workflow/NES', dict(rom_folder=str(current)))
            db.close()
            window = MainWindow(db_path)
            try:
                self.wait_idle(window)
                self.assertEqual(window.model.rowCount(), 0)
                self.assertEqual(window.model.columnCount(), 5)
                self.assertFalse(window.transfer_button.isEnabled())
                # Reappearance needs only reload, with no separate registration action.
                replacement = current / 'Game.nes'
                replacement.write_bytes(b'rom')
                window.refresh()
                self.wait_idle(window)
                self.assertEqual(window.model.rowCount(), 1)
                game = window.model.games[0]
                self.assertEqual(game['id'], ident)
                self.assertEqual(game['local_rom_path'], str(replacement.resolve()))
                self.assertEqual(game['japanese_title'], '確定名')
                self.assertEqual(game['artwork_path'], str(image))
                window.model.set_checks([ident], True)
                self.assertTrue(window.transfer_button.isEnabled())
                replacement.unlink()
                image_bytes = image.read_bytes()
                image.unlink()
                window.refresh()
                self.wait_idle(window)
                self.assertEqual(window.model.rowCount(), 0)
                self.assertFalse(window.transfer_button.isEnabled())
                db = Store(db_path)
                try:
                    self.assertEqual(db.game(ident)['japanese_title'], '確定名')
                    self.assertEqual(db.game(ident)['artwork_path'], str(image))
                    self.assertEqual(len(db.artworks(ident)), 1)
                finally:
                    db.close()
                replacement.write_bytes(b'rom')
                image.write_bytes(image_bytes)
                window.refresh()
                self.wait_idle(window)
                self.assertEqual(window.model.rowCount(), 1)
                self.assertEqual(window.model.games[0]['artwork_path'], str(image))
            finally:
                self.wait_idle(window)
                window.close()

    def test_transfer_without_checks_never_falls_back_to_all_games(self):
        from gui.app import MainWindow
        from gui.transfer_dialog import TransferDialog
        from database.store import Store
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'db'
            db = Store(path)
            db.upsert_game('/media/fat/games/NES/Game.nes')
            db.close()
            window = MainWindow(path)
            try:
                self.wait_idle(window)
                self.assertFalse(window.transfer_button.isEnabled())
                with patch('PyQt6.QtWidgets.QMessageBox.information'), patch.object(TransferDialog, 'exec') as dialog:
                    window.choose_transfer()
                dialog.assert_not_called()
            finally:
                window.close()

    def test_transfer_reviews_pending_names_and_allows_missing_images_and_names(self):
        from gui.app import MainWindow
        from gui.transfer_dialog import TransferDialog
        from database.store import Store
        from PyQt6.QtWidgets import QDialog
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'db'
            db = Store(path)
            ids = []
            for name in ('Candidate', 'English'):
                rom = root / (name + '.nes')
                rom.write_bytes(b'rom')
                ids.append(db.register_local_rom('NES', rom))
            db.save_candidates(ids[0], [dict(japanese_title='日本語候補', score=.8, reason='タイトル名一致')])
            db.close()
            window = MainWindow(path)
            transferred = []
            try:
                self.wait_idle(window)
                window.model.set_checks(ids, True)
                def inspect(dialog):
                    self.assertTrue(dialog.advanced.isHidden())
                    self.assertEqual(dialog.review_list.count(), 2)
                    self.assertIn('日本語候補', dialog.review_list.item(0).text())
                    self.assertIn('画像なし', dialog.review_list.item(0).text())
                    self.assertIn('English', dialog.review_list.item(1).text())
                    db = Store(path)
                    self.assertIsNone(db.game(ids[0])['japanese_title'])
                    db.close()
                    dialog.host.setText('192.0.2.10')
                    return QDialog.DialogCode.Accepted
                def transfer(items, *args, **kwargs):
                    self.assertEqual(items, [])
                    self.assertEqual(kwargs['titles'], {'Candidate': '日本語候補'})
                    self.assertEqual(len(kwargs['roms']), 2)
                    transferred.append(True)
                    return dict(transferred=0, titles_transferred=1, roms_transferred=2, roms_skipped=0, report='test-report')
                with patch.object(TransferDialog, 'exec', return_value=QDialog.DialogCode.Rejected):
                    window.choose_transfer()
                with patch.object(TransferDialog, 'exec', inspect), patch('gui.transfer_flow.transfer_images', side_effect=transfer), \
                        patch('PyQt6.QtWidgets.QMessageBox.information'):
                    window.choose_transfer()
                    self.wait_idle(window)
                self.assertEqual(transferred, [True])
                self.assertTrue(set(ids).issubset(window.model.checked_ids))
                db = Store(path)
                self.assertEqual(db.game(ids[0])['japanese_title'], '日本語候補')
                self.assertIsNone(db.game(ids[1])['japanese_title'])
                db.close()
            finally:
                window.close()

    def test_removed_3do_records_and_old_scope_do_not_break_library_loading(self):
        from database.store import Store
        from gui.app import MainWindow
        from metadata.title_catalog import export_catalog
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'db'
            db = Store(path)
            with db.db:
                db.db.execute("INSERT OR IGNORE INTO systems VALUES ('3DO')")
                db.db.execute("""INSERT INTO games(device,system,rom_path,rom_filename,original_title)
                    VALUES ('old','3DO','/media/fat/games/3DO/Game.chd','Game.chd','Game')""")
            db.set_setting('ui/scope', dict(device='old', system='3DO', rom_folder=tmp))
            self.assertEqual(export_catalog(db, db.games())['entries'], [])
            db.close()
            window = MainWindow(path)
            try:
                self.wait_idle(window)
                self.assertEqual(window.system.findData('3DO'), -1)
                self.assertEqual(window.model.rowCount(), 0)
                db = Store(path)
                self.assertEqual(len(db.games()), 1)
                db.close()
            finally:
                window.close()


    def test_grouped_candidates_show_each_source_and_keep_confirmation_explicit(self):
        from gui.app import MainWindow
        from database.store import Store
        from PyQt6.QtCore import Qt
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'db'
            db = Store(path)
            ident = db.upsert_game('/media/fat/games/NES/Game.nes')
            db.save_candidates(ident, [
                dict(japanese_title='ゲーム Ａ', score=1, provider='retronian', source='retronian-url', license='CC BY-SA 4.0'),
                dict(japanese_title='ｹﾞｰﾑA', score=1, provider='gamedatabase', source='csv-url', license='CC BY 4.0'),
            ])
            db.close()
            window = MainWindow(path)
            try:
                self.wait_idle(window)
                window.show_game(window.proxy.index(0, 0))
                self.assertEqual(window.title_candidates.count(), 2)  # manual + one grouped title
                self.assertEqual(window.title_candidates.currentIndex(), 1)
                self.assertEqual(window.title_edit.text(), 'ゲーム Ａ')
                text = window.evidence.toPlainText()
                self.assertIn('ゲーム Ａ', text)
                self.assertIn('ｹﾞｰﾑA', text)
                self.assertIn('retronian-url', text)
                self.assertIn('csv-url', text)
                self.assertIn('CC BY-SA 4.0', text)
                self.assertIn('CC BY 4.0', text)
                self.assertTrue(window.evidence.isReadOnly())
                self.assertNotEqual(window.current_game['match_status'], 'confirmed')
                db = Store(path)
                try:
                    self.assertIsNone(db.game(ident)['japanese_title'])
                    self.assertEqual(db.title_decisions(ident), [])
                finally:
                    db.close()
            finally:
                window.close()

    def test_saved_title_takes_priority_over_default_candidate(self):
        from gui.app import MainWindow
        from database.store import Store
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'db'
            db = Store(path)
            ident = db.upsert_game('/media/fat/games/NES/Game.nes')
            db.save_candidates(ident, [dict(japanese_title='先頭候補', score=1), dict(japanese_title='確定名', score=.8)])
            db.confirm(ident, '確定名')
            db.close()
            window = MainWindow(path)
            try:
                self.wait_idle(window)
                self.assertEqual(window.title_edit.text(), '確定名')
                self.assertEqual(window.title_candidates.currentData()['japanese_title'], '確定名')
                db = Store(path)
                db.confirm(ident, '手入力の確定名')
                db.close()
                window.refresh()
                self.wait_idle(window)
                self.assertEqual(window.title_candidates.currentIndex(), 0)
                self.assertEqual(window.title_edit.text(), '手入力の確定名')
                db = Store(path)
                db.unconfirm(ident)
                db.save_candidates(ident, [])
                db.close()
                window.refresh()
                self.wait_idle(window)
                self.assertEqual(window.title_candidates.currentIndex(), 0)
                self.assertEqual(window.title_edit.text(), '')
            finally:
                self.wait_idle(window)
                window.close()

    def test_added_systems_are_selectable_in_filter_and_registration(self):
        from gui.app import MainWindow
        from gui.system_choices import SYSTEM_GROUPS
        from systems import SYSTEMS
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QComboBox, QDialog, QLabel
        from test_added_systems import ADDITIONS
        with tempfile.TemporaryDirectory() as tmp:
            window = MainWindow(Path(tmp) / 'db')
            try:
                self.wait_idle(window)
                self.assertEqual(window.system.findData('NDS'), -1)
                expected = [identifier for _, group in SYSTEM_GROUPS for identifier in group]
                self.assertEqual(len(expected), len(set(expected)))
                self.assertEqual(set(expected), set(SYSTEMS))
                self.assertEqual([window.system.itemData(i) for i in range(window.system.count())
                                  if window.system.itemData(i)], expected)
                for i in range(window.system.count()):
                    if window.system.itemData(i) is None:
                        self.assertFalse(window.system.model().item(i).flags() & Qt.ItemFlag.ItemIsSelectable)
                for system, _, _ in ADDITIONS:
                    self.assertGreaterEqual(window.system.findData(system), 0)
                def inspect(dialog):
                    combo = dialog.findChild(QComboBox, 'registrationSystem')
                    self.assertIn(combo.currentData(), SYSTEMS)
                    self.assertEqual([combo.itemData(i) for i in range(combo.count()) if combo.itemData(i)], expected)
                    self.assertEqual(combo.findData('NDS'), -1)
                    destination = dialog.findChild(QComboBox, 'registrationDestinationChoices')
                    fixed = dialog.findChild(QLabel, 'registrationDestination')
                    combo.setCurrentIndex(combo.findData('FDS'))
                    self.assertTrue(destination.isHidden())
                    self.assertFalse(fixed.isHidden())
                    self.assertEqual(fixed.text(), SYSTEMS['FDS'].root)
                    combo.setCurrentIndex(combo.findData('MD'))
                    self.assertFalse(destination.isHidden())
                    self.assertTrue(fixed.isHidden())
                    self.assertEqual([destination.itemText(i) for i in range(destination.count())],
                                     list(SYSTEMS['MD'].roots))
                    combo.setCurrentIndex(combo.findData('FDS'))
                    self.assertTrue(destination.isHidden())
                    self.assertEqual(destination.currentText(), SYSTEMS['FDS'].root)
                    for identifier in ('NGP', 'NGPC'):
                        combo.setCurrentIndex(combo.findData(identifier))
                        self.assertTrue(destination.isHidden())
                        self.assertFalse(fixed.isHidden())
                        self.assertEqual(fixed.text(), '/media/fat/games/NGPC')
                        self.assertEqual(destination.count(), 1)
                    for system, _, _ in ADDITIONS:
                        index = combo.findData(system)
                        self.assertGreaterEqual(index, 0)
                        combo.setCurrentIndex(index)
                        self.assertEqual(combo.currentData(), system)
                    return QDialog.DialogCode.Rejected
                with patch('PyQt6.QtWidgets.QDialog.exec', new=inspect):
                    window.choose_local_roms()
            finally:
                window.close()

    def test_refresh_cleans_missing_selection_and_zero_rotation_saves_new_candidate(self):
        from gui.app import MainWindow
        from database.store import Store
        from test_artwork_transfer import PNG
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'db'
            original, replacement = root / 'old.png', root / 'new.png'
            original.write_bytes(PNG)
            replacement.write_bytes(PNG)
            db = Store(path)
            game_id = db.upsert_game('/media/fat/games/NES/Game.nes')
            db.confirm(game_id, 'ゲーム')
            for image in (original, replacement):
                db.save_artwork(game_id, 'skraper', 'JP', image.as_uri(), image,
                                hashlib.sha256(PNG).hexdigest(), select=image == original)
            db.close()
            original.unlink()
            window = MainWindow(path)
            try:
                self.wait_idle(window)
                self.assertEqual(window.angle, 0)
                self.assertEqual(window.current_artwork['local_path'], str(replacement))
                self.assertTrue(window.save_button.isEnabled())
                window.save_button.click()
                self.wait_idle(window)
                db = Store(path)
                try:
                    self.assertEqual(db.game(game_id)['artwork_path'], str(replacement))
                    self.assertEqual(db.game(game_id)['japanese_title'], 'ゲーム')
                    self.assertEqual(len(db.artworks(game_id)), 1)
                    self.assertEqual(replacement.read_bytes(), PNG)
                finally:
                    db.close()
            finally:
                window.close()

    def test_skraper_launch_uses_selected_executable_without_scraping_flags(self):
        from gui.skraper_dialog import SkraperDialog
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            roms = root / 'roms'
            roms.mkdir()
            executable = root / 'SkraperUI.exe'
            executable.write_bytes(b'placeholder-never-executed')
            profile = dict(system='SNES', device='test', rom_folder=str(roms),
                           image_folder=str(root / 'output'))
            dialog = SkraperDialog(profile, str(root))
            try:
                with patch('gui.skraper_dialog.QProcess.startDetached', return_value=(True, 123)) as start:
                    dialog.launch()
                    self.assertEqual(start.call_args.args, (str(executable.resolve()), [], str(root.resolve())))
                self.assertEqual(dialog.validated_profile()['image_folder'], str((root / 'output').resolve()))
                dialog.output.setText(str(roms))
                self.assertEqual(dialog.validated_profile()['image_folder'], str(roms.resolve()))
            finally:
                dialog.close()

    def test_skraper_return_imports_remembered_output_for_selected_library_and_system(self):
        from gui.app import MainWindow
        from gui.skraper_dialog import SkraperDialog
        from database.store import Store
        from PyQt6.QtWidgets import QDialog
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            roms = root / 'roms'
            roms.mkdir()
            path = root / 'db'
            db = Store(path)
            game_id = db.upsert_game('/media/fat/games/SNES/Game.sfc', 'SNES')
            profile = dict(system='SNES', device='home-mister', rom_folder=str(roms),
                           image_folder=str(root / 'output'))
            db.set_setting('workflow/home-mister/SNES', profile)
            db.close()
            window = MainWindow(path)
            try:
                self.wait_idle(window)
                window.system.setCurrentIndex(window.system.findData('SNES'))
                with patch.object(SkraperDialog, 'exec', return_value=QDialog.DialogCode.Accepted), patch(
                        'gui.import_flow.plan_import', return_value=dict(ready_count=0, items=[])) as plan, patch.object(
                        window, 'open_import_dialog'):
                    window.choose_skraper()
                    self.wait_idle(window)
                self.assertEqual(plan.call_args.args[0], str(Path(profile['image_folder']).resolve()))
                self.assertEqual([g['id'] for g in plan.call_args.args[1]], [game_id])
            finally:
                window.close()

    def test_skraper_dialog_follows_skrapers_own_settings(self):
        import json
        from gui.app import MainWindow
        from gui.skraper_dialog import SkraperDialog
        from database.store import Store
        from PyQt6.QtWidgets import QDialog
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            roms, skraper = root / 'dumps' / 'NES', root / 'skraper'
            roms.mkdir(parents=True)
            skraper.mkdir()
            (skraper / 'skraper.config.json').write_text(json.dumps(dict(Systems=[dict(
                SystemId=3, GamesAndRoms=dict(Path=str(roms), Platform='NES'),
                Medias=[dict(Type=0, OutputFolder='%ROMROOTFOLDER%/media/box2dfront')])],
                ScreenScraperAccount=dict(Password='secret'))), encoding='utf-8')
            path = root / 'db'
            db = Store(path)
            db.upsert_game('/media/fat/games/NES/Game.nes')
            db.set_setting('skraper/folder', str(skraper))
            db.set_setting('workflow/home-mister/NES', dict(system='NES', device='home-mister',
                           rom_folder=str(roms), image_folder='stale'))
            db.close()
            window = MainWindow(path)
            shown = []
            def exec_dialog(dialog):
                shown.append((dialog.rom_folder.text(), dialog.output.text()))
                return QDialog.DialogCode.Rejected
            try:
                self.wait_idle(window)
                window.system.setCurrentIndex(window.system.findData('NES'))
                with patch.object(SkraperDialog, 'exec', new=exec_dialog):
                    window.choose_skraper()
                self.assertEqual(shown, [(str(roms), str(roms / 'media' / 'box2dfront'))])
                db = Store(path)
                try:
                    saved = db.setting('workflow/NES')
                finally:
                    db.close()
                self.assertEqual(saved['rom_folder'], str(roms))
            finally:
                window.close()

    def test_registration_uses_legacy_rom_folder_without_library_field(self):
        from gui.app import MainWindow
        from database.store import Store
        from PyQt6.QtWidgets import QDialog, QLineEdit, QCheckBox
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'Game.nes').write_bytes(b'rom')
            path = root / 'library.sqlite3'
            db = Store(path)
            db.set_setting('workflow/home-mister/NES', dict(rom_folder=tmp))
            db.close()
            window = MainWindow(path)
            try:
                self.wait_idle(window)
                window.system.setCurrentIndex(window.system.findData('NES'))
                def accept(dialog):
                    folder = dialog.findChild(QLineEdit, 'romFolder')
                    self.assertEqual(folder.text(), tmp)
                    self.assertIsNone(dialog.findChild(QLineEdit, 'registrationLibrary'))
                    self.assertFalse(hasattr(window, 'device'))
                    dialog.findChild(QCheckBox).setChecked(False)
                    return QDialog.DialogCode.Accepted
                with patch.object(QDialog, 'exec', accept), patch('PyQt6.QtWidgets.QMessageBox.warning') as warning:
                    window.choose_local_roms()
                    self.wait_idle(window)
                    warning.assert_not_called()
                db = Store(path)
                try:
                    self.assertEqual(len(db.games()), 1)
                    self.assertEqual(db.setting('workflow/NES')['rom_folder'], str(root.resolve()))
                finally:
                    db.close()
            finally:
                self.wait_idle(window)
                window.close()

    def test_local_folder_registration_and_rom_only_transfer_dialog(self):
        from gui.app import MainWindow
        from gui.transfer_dialog import TransferDialog
        from database.store import Store
        from PyQt6.QtWidgets import QDialog, QLineEdit, QPushButton
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'Game.sfc').write_bytes(b'local-rom')
            (root / 'Other.nes').write_bytes(b'other-system')
            path = root / 'library.sqlite3'
            window = MainWindow(path)
            captured = []
            def transfer(items, *args, **kwargs):
                captured.extend(kwargs['roms'])
                self.assertEqual(items, [])
                return dict(transferred=0, titles_transferred=0, roms_transferred=1,
                            roms_skipped=0, report='test-report')
            try:
                self.wait_idle(window)
                window.system.setCurrentIndex(window.system.findData('SNES'))
                def fill_folder(dialog):
                    field = dialog.findChild(QLineEdit, 'romFolder')
                    self.assertEqual(field.text(), str(window.rom_folders['SNES']))
                    # The browse button fills the visible path field.
                    with patch('PyQt6.QtWidgets.QFileDialog.getExistingDirectory', return_value=tmp):
                        next(b for b in dialog.findChildren(QPushButton) if b.text() == '参照').click()
                    self.assertEqual(field.text(), tmp)
                    return QDialog.DialogCode.Accepted
                with patch('PyQt6.QtWidgets.QDialog.exec', new=fill_folder), patch(
                        'gui.import_flow.match_titles', return_value=dict(
                            matched=0, ambiguous=0, missing=1, confirmed=0, modes={})) as lookup:
                    window.choose_local_roms()
                    self.wait_idle(window)
                    self.assertEqual(lookup.call_args.args[1][0]['rom_filename'], 'Game.sfc')
                # An earlier registration is hidden while the folder scope is on, and returns when it is off.
                other = Store(path)
                other.upsert_game('/media/fat/games/SNES/Old.sfc', 'SNES')
                other.close()
                window.refresh()
                self.wait_idle(window)
                self.assertTrue(window.scope_only.isChecked())
                self.assertEqual([g['rom_filename'] for g in window.visible_games()], ['Game.sfc'])
                window.scope_only.setChecked(False)
                self.assertEqual(len(window.visible_games()), 2)
                window.scope_only.setChecked(True)
                other = Store(path)
                other.db.execute("DELETE FROM games WHERE rom_filename='Old.sfc'")
                other.db.commit()
                other.close()
                db = Store(path)
                try:
                    games = db.games()
                    self.assertEqual(len(games), 1)
                    self.assertEqual(games[0]['rom_path'], '/media/fat/games/SNES/Game.sfc')
                    self.assertEqual(games[0]['local_rom_path'], str((root / 'Game.sfc').resolve()))
                    self.assertEqual(db.setting('workflow/SNES')['rom_folder'], str(root.resolve()))
                finally:
                    db.close()
                def accept(dialog):
                    dialog.host.setText('192.0.2.10')
                    self.assertTrue(dialog.roms.isChecked())
                    self.assertFalse(dialog.images.isChecked())
                    self.assertIn('/media/fat/games/SNES/Game.sfc', dialog.preview.toPlainText())
                    dialog.roms.setChecked(False)
                    self.assertFalse(dialog.submit.isEnabled())
                    dialog.roms.setChecked(True)
                    self.assertTrue(dialog.submit.isEnabled())
                    return QDialog.DialogCode.Accepted
                with patch.object(TransferDialog, 'exec', accept), patch(
                        'gui.transfer_flow.transfer_images', side_effect=transfer), patch(
                        'PyQt6.QtWidgets.QMessageBox.information'):
                    window.model.set_checks([g['id'] for g in window.visible_games()], True)
                    window.choose_transfer()
                    self.wait_idle(window)
                self.assertEqual([r['remote_path'] for r in captured], ['/media/fat/games/SNES/Game.sfc'])
                self.assertIn('ROM追加 1本', window.status_label.text())
                # After a restart the list is still limited to the last registered folder.
                extra = Store(path)
                extra.upsert_game('/media/fat/games/SNES/Old.sfc', 'SNES')
                extra.close()
                restarted = MainWindow(path)
                try:
                    self.wait_idle(restarted)
                    self.assertEqual([g['rom_filename'] for g in restarted.visible_games()], ['Game.sfc'])
                    restarted.scope_only.setChecked(False)
                    self.assertEqual(len(restarted.visible_games()), 2)
                finally:
                    restarted.close()
            finally:
                window.close()

    def test_registration_searches_subfolders_and_skraper_gets_a_project_side_rom_mirror(self):
        from gui.app import MainWindow
        from database.store import Store
        from PyQt6.QtWidgets import QDialog, QLineEdit
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'dumps'
            (root / 'SNES' / 'deeper').mkdir(parents=True)
            (root / 'media').mkdir()
            (root / 'SNES' / 'A.sfc').write_bytes(b'a')
            (root / 'SNES' / 'deeper' / 'B.sfc').write_bytes(b'b')
            (root / 'SNES' / 'deeper' / 'a.sfc').write_bytes(b'dup')
            (root / 'media' / 'Skip.sfc').write_bytes(b's')
            path = Path(tmp) / 'library.sqlite3'
            window = MainWindow(path)
            def fill(dialog):
                dialog.findChild(QLineEdit, 'romFolder').setText(str(root))
                return QDialog.DialogCode.Accepted
            try:
                self.wait_idle(window)
                window.system.setCurrentIndex(window.system.findData('SNES'))
                with patch('PyQt6.QtWidgets.QDialog.exec', new=fill):
                    window.choose_local_roms()
                    self.wait_idle(window)
                db = Store(path)
                try:
                    self.assertEqual(sorted(g['rom_filename'] for g in db.games()), ['A.sfc', 'B.sfc'])
                finally:
                    db.close()
                self.assertIn('同名のROMがあるため除外 1本', window.status_label.text())
                self.assertEqual(len(window.visible_games()), 2)
            finally:
                window.close()

    def test_game_boy_folder_is_split_into_gb_and_gbc_by_rom_type(self):
        import zipfile
        from gui.app import MainWindow
        from database.store import Store
        from PyQt6.QtWidgets import QDialog, QLineEdit
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'GB'
            root.mkdir()
            for name, member in (('Mono.zip', 'Mono.gb'), ('Color.zip', 'Color.gbc'), ('Advance.zip', 'Advance.gba')):
                with zipfile.ZipFile(root / name, 'w') as z:
                    z.writestr(member, b'rom')
            (root / 'Loose.gbc').write_bytes(b'rom')
            path = Path(tmp) / 'library.sqlite3'
            window = MainWindow(path)
            def fill(dialog):
                dialog.findChild(QLineEdit, 'romFolder').setText(str(root))
                return QDialog.DialogCode.Accepted
            try:
                self.wait_idle(window)
                window.system.setCurrentIndex(window.system.findData('GB'))
                with patch('PyQt6.QtWidgets.QDialog.exec', new=fill), patch(
                        'gui.import_flow.match_titles', return_value=dict(
                            matched=0, ambiguous=0, missing=0, confirmed=0, modes={})):
                    window.choose_local_roms()
                    self.wait_idle(window)
                db = Store(path)
                try:
                    placed = {g['rom_filename']: (g['system'], g['rom_path']) for g in db.games()}
                    workflow = {s: db.setting('workflow/' + s)['remote_root'] for s in ('GB', 'GBC')}
                finally:
                    db.close()
                self.assertEqual(placed, {
                    'Mono.zip': ('GB', '/media/fat/games/GAMEBOY/Mono.zip'),
                    'Color.zip': ('GBC', '/media/fat/games/GBC/Color.zip'),
                    'Loose.gbc': ('GBC', '/media/fat/games/GBC/Loose.gbc')})
                self.assertEqual(workflow, {'GB': '/media/fat/games/GAMEBOY', 'GBC': '/media/fat/games/GBC'})
                self.assertIn('対象外 1本', window.status_label.text())
                self.assertEqual(len(window.visible_games()), 3)
            finally:
                window.close()


    def test_system_filter_all_and_import_scope(self):
        from gui.app import MainWindow
        from database.store import Store
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'library.sqlite3'
            db = Store(path)
            nes = db.upsert_game('/media/fat/games/NES/Game.nes')
            snes = db.upsert_game('/media/fat/games/SNES/Game.sfc', system='SNES')
            db.close()
            window = MainWindow(path)
            try:
                self.wait_idle(window)
                self.assertEqual(window.proxy.rowCount(), 2)
                window.system.setCurrentIndex(window.system.findData('SNES'))
                self.assertEqual([g['id'] for g in window.visible_games()], [snes])
                window.check_all.click()
                self.assertEqual(window.model.checked_ids, {snes})
                window.system.setCurrentIndex(window.system.findData('NES'))
                self.assertEqual([g['id'] for g in window.visible_games()], [nes])
                self.assertIn('0 / 1', window.check_count.text())
            finally:
                window.close()


    def test_combined_transfer_dialog_defaults_and_single_kind_selection(self):
        from gui.transfer_dialog import TransferDialog
        items = [dict(local_path='local.png', remote_path='/media/fat/games/NES/media/Game.png')]
        dialog = TransferDialog(items, {'Game': 'ゲーム'})
        dialog.host.setText('192.0.2.10')
        try:
            self.assertTrue(dialog.images.isChecked())
            self.assertTrue(dialog.advanced.isHidden())
            self.assertTrue(dialog.images.isHidden() or dialog.images.parentWidget().isHidden())
            self.assertTrue(dialog.titles.isChecked())
            self.assertIn('Game → ゲーム', dialog.preview.toPlainText())
            self.assertIn('画像 1枚 / 日本語ソフト名 1本', dialog.summary.text())
            dialog.titles.setChecked(False)
            self.assertNotIn('ゲーム', dialog.preview.toPlainText())
            self.assertTrue(dialog.submit.isEnabled())
            dialog.images.setChecked(False)
            self.assertFalse(dialog.submit.isEnabled())
            dialog.titles.setChecked(True)
            self.assertTrue(dialog.submit.isEnabled())
            dialog.host.clear()
            self.assertFalse(dialog.submit.isEnabled())
            dialog.password.setText('private')
            dialog.reject()
            self.assertEqual(dialog.password.text(), '')
        finally:
            dialog.close()

    def test_combined_transfer_uses_checked_games_for_images_and_titles(self):
        from gui.app import MainWindow
        from gui.transfer_dialog import TransferDialog
        from database.store import Store
        from PyQt6.QtWidgets import QDialog
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            png = path / 'image.png'
            data = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
            png.write_bytes(data)
            db = Store(path / 'library.sqlite3')
            ids = []
            for name in ['Game', 'Other']:
                game = db.upsert_game('/media/fat/games/NES/' + name + '.nes')
                db.confirm(game, '日本語' + name)
                db.save_artwork(game, 'test', 'JP', png.as_uri(), png, hashlib.sha256(data).hexdigest())
                ids.append(game)
            db.close()
            window = MainWindow(path / 'library.sqlite3')
            captured = []
            def accept(dialog):
                dialog.host.setText('192.0.2.10')
                dialog.password.setText('test-password')
                return QDialog.DialogCode.Accepted
            def transfer(items, host, user, password, backup, **kwargs):
                captured.append((items, host, user, password, kwargs['titles']))
                return dict(transferred=1, titles_transferred=1, roms_transferred=0, roms_skipped=0, report='test-report')
            try:
                self.wait_idle(window)
                window.model.set_checks([ids[0]], True)
                with patch.object(TransferDialog, 'exec', accept), patch('gui.transfer_flow.transfer_images', side_effect=transfer), patch('PyQt6.QtWidgets.QMessageBox.information'):
                    window.choose_transfer()
                    self.wait_idle(window)
                self.assertEqual(len(captured), 1)
                self.assertEqual([i['game_id'] for i in captured[0][0]], [ids[0]])
                self.assertEqual(captured[0][4], {'Game': '日本語Game'})
                self.assertIn('画像 1/1枚、日本語ソフト名 1/1本', window.status_label.text())
            finally:
                window.close()


    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.rom_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.rom_directory.cleanup)
        self.rom_root = Path(self.rom_directory.name) / 'work' / 'Roms'
        root_patch = patch('gui.app.ROM_ROOT', self.rom_root)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def test_startup_creates_rom_folders_and_registration_uses_system_defaults(self):
        from gui.app import MainWindow
        from database.store import Store
        from systems import SYSTEMS
        from PyQt6.QtWidgets import QDialog, QComboBox, QLineEdit, QPushButton
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'db'
            external = Path(tmp) / 'external-roms'
            external.mkdir()
            db = Store(path)
            db.set_setting('workflow/NES', dict(rom_folder=str(external)))
            db.close()
            window = MainWindow(path)
            try:
                self.wait_idle(window)
                self.assertEqual({p.name for p in self.rom_root.iterdir() if p.is_dir()},
                                 {p.rom_folder_name for p in SYSTEMS.values()})
                marker = self.rom_root / 'NGPC' / 'existing.ngp'
                marker.write_bytes(b'existing-rom')
                def inspect(dialog):
                    system = dialog.findChild(QComboBox, 'registrationSystem')
                    folder = dialog.findChild(QLineEdit, 'romFolder')
                    system.setCurrentIndex(system.findData('NGP'))
                    self.assertEqual(folder.text(), str(self.rom_root / 'NGPC'))
                    system.setCurrentIndex(system.findData('NGPC'))
                    self.assertEqual(folder.text(), str(self.rom_root / 'NGPC'))
                    system.setCurrentIndex(system.findData('NES'))
                    self.assertEqual(folder.text(), str(external))
                    system.setCurrentIndex(system.findData('FDS'))
                    self.assertEqual(folder.text(), str(self.rom_root / 'FDS'))
                    with patch('PyQt6.QtWidgets.QFileDialog.getExistingDirectory', return_value=str(external)):
                        next(b for b in dialog.findChildren(QPushButton) if b.text() == '参照').click()
                    self.assertEqual(folder.text(), str(external))
                    return QDialog.DialogCode.Rejected
                with patch.object(QDialog, 'exec', inspect):
                    window.choose_local_roms()
            finally:
                self.wait_idle(window)
                window.close()
            restarted = MainWindow(path)
            try:
                self.wait_idle(restarted)
                self.assertEqual(marker.read_bytes(), b'existing-rom')
            finally:
                self.wait_idle(restarted)
                restarted.close()

    def wait_idle(self, window):
        from PyQt6.QtTest import QTest
        deadline = time.monotonic() + 8
        while window.task is not None:
            self.app.processEvents()
            QTest.qWait(10)
            if time.monotonic() > deadline:
                self.fail('GUI background operation did not finish')

    def test_candidate_display_rotation_and_explicit_confirmation(self):
        from gui.app import MainWindow
        from database.store import Store
        from PyQt6.QtCore import Qt
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            png = path / 'Game.png'
            data = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
            png.write_bytes(data)
            db = Store(path / 'library.sqlite3')
            game = db.upsert_game('/games/NES/Game.nes')
            db.save_candidates(game, [dict(japanese_title='日本語候補', score=1,
                                           reason='alias exact', source='test')])
            db.save_artwork(game, 'skraper', 'JP', png.as_uri(), png, hashlib.sha256(data).hexdigest(), select=False)
            db.close()
            window = MainWindow(path / 'library.sqlite3')
            try:
                self.wait_idle(window)
                self.assertEqual(window.proxy.rowCount(), 1)
                self.assertIn('日本語候補', window.proxy.index(0, 1).data())
                self.assertIsNotNone(window.pixmap)
                self.assertEqual(window.current_artwork['region'], 'JP')
                window.rotate(90)
                self.assertEqual(window.angle, 90)
                self.assertEqual(png.read_bytes(), data)
                window.title_candidates.setCurrentIndex(1)
                self.assertEqual(window.title_edit.text(), '日本語候補')
                window.save_button.click()
                self.wait_idle(window)
                db = Store(path / 'library.sqlite3')
                self.assertEqual(db.game(game)['match_status'], 'confirmed')
                self.assertEqual(db.game(game)['japanese_title'], '日本語候補')
                self.assertNotEqual(db.game(game)['artwork_path'], str(png))
                self.assertEqual(window.current_artwork['rotation_degrees'], 90)
                self.assertEqual(png.read_bytes(), data)
                self.assertEqual(db.game(game)['sync_status'], 'blocked')
                db.close()
                window.pending_only.setChecked(True)
                self.assertEqual(window.proxy.rowCount(), 0)
                self.assertIsNone(window.current_game)
            finally:
                self.wait_idle(window)
                window.close()
                self.app.processEvents()

    def test_empty_library_is_usable(self):
        from gui.app import MainWindow
        with tempfile.TemporaryDirectory() as tmp:
            window = MainWindow(Path(tmp) / 'empty.sqlite3')
            try:
                self.wait_idle(window)
                self.assertEqual(window.proxy.rowCount(), 0)
                self.assertIn('該当', window.cover.text())
                self.assertTrue(window.skraper_button.isEnabled())
            finally:
                window.close()
                self.app.processEvents()

    def test_three_step_workflow_and_checks_select_transfer_targets_only(self):
        from gui.app import MainWindow
        from database.store import Store
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QDialog, QPushButton
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Store(root / 'db.sqlite3')
            ids = []
            for name in ('Alpha', 'Beta'):
                game = db.upsert_game('/media/fat/games/NES/' + name + '.nes')
                db.save_candidates(game, [dict(japanese_title=name + '日本語', score=1)])
                ids.append(game)
            db.close()
            window = MainWindow(root / 'db.sqlite3')
            try:
                self.wait_idle(window)
                steps = [button for button in window.findChildren(QPushButton)
                         if button.text().startswith(('1 ·', '2 ·', '3 ·', '4 ·'))]
                self.assertEqual([button.text() for button in steps],
                                 ['1 · PCのROMを登録', '2 · Skraperで画像取得・取り込み', '3 · MiSTerへ転送'])
                self.assertTrue(all(button.objectName() != 'primary' for button in steps))
                self.assertFalse(hasattr(window, 'advanced_button'))
                self.assertTrue(window.recovery_button.isHidden())
                self.assertFalse(window.transfer_button.isEnabled())
                window.check_all.click()
                self.assertEqual(window.model.checked_ids, set(ids))
                self.assertEqual(window.check_all.checkState(), Qt.CheckState.Checked)
                # Individual checkbox supports partial ALL state.
                source = window.proxy.mapToSource(window.proxy.index(0, 0))
                window.model.setData(source, Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
                self.assertEqual(window.check_all.checkState(), Qt.CheckState.PartiallyChecked)
                window.check_all.click()
                self.assertEqual(window.model.checked_ids, set(ids))
                window.search.setText('Alpha')
                self.assertEqual(window.proxy.rowCount(), 1)
                window.check_all.click()
                self.assertNotIn(ids[0], window.model.checked_ids)
                self.assertIn(ids[1], window.model.checked_ids)
                window.check_all.click()
                # Canceling does not approve anything.
                with patch.object(QDialog, 'exec', return_value=QDialog.DialogCode.Rejected):
                    window.choose_transfer()
                db = Store(root / 'db.sqlite3')
                try:
                    self.assertTrue(all(g['match_status'] == 'review' for g in db.games()))
                finally:
                    db.close()
                self.assertIn(ids[0], window.model.checked_ids)
                self.assertIn(ids[1], window.model.checked_ids)
            finally:
                self.wait_idle(window)
                window.close()
                self.app.processEvents()

    def test_save_rotation_keeps_full_resolution_and_persists_selected_copy(self):
        from gui.app import MainWindow
        from database.store import Store
        from PyQt6.QtGui import QImage, QColor
        from PyQt6.QtWidgets import QMessageBox
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'original.png'
            image = QImage(2000, 1000, QImage.Format.Format_ARGB32)
            image.fill(QColor('transparent'))
            image.setPixelColor(0, 0, QColor('red'))
            image.save(str(source), 'PNG')
            original = source.read_bytes()
            db = Store(root / 'db.sqlite3')
            game_id = db.upsert_game('/games/NES/Game.nes')
            db.confirm(game_id, '確定名')
            db.save_artwork(game_id, 'skraper', 'JP', source.as_uri(), source,
                            hashlib.sha256(original).hexdigest())
            db.close()
            window = MainWindow(root / 'db.sqlite3')
            try:
                self.wait_idle(window)
                window.rotate(90)
                self.assertTrue(window.save_button.isEnabled())
                window.save_game()
                self.wait_idle(window)
                self.assertEqual(window.current_artwork['rotation_degrees'], 90)
                corrected = Path(window.current_artwork['local_path'])
                result = QImage(str(corrected))
                self.assertEqual((result.width(), result.height()), (1000, 2000))
                self.assertEqual(result.pixelColor(999, 0), QColor('red'))
                self.assertEqual(source.read_bytes(), original)
                db = Store(root / 'db.sqlite3')
                try:
                    self.assertEqual(db.game(game_id)['artwork_path'], str(corrected))
                    self.assertEqual(db.game(game_id)['japanese_title'], '確定名')
                    self.assertEqual(len(db.artworks(game_id)), 2)
                finally:
                    db.close()
                with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
                    window.save_game()
                self.wait_idle(window)
                self.assertEqual(window.current_artwork['local_path'], str(corrected))
                self.assertEqual(window.angle, 0)
                self.assertTrue(window.save_button.isEnabled())
                window.refresh()
                self.wait_idle(window)
                self.assertEqual(window.current_artwork['local_path'], str(corrected))
                self.assertEqual(source.read_bytes(), original)
            finally:
                self.wait_idle(window)
                window.close()
                self.app.processEvents()

    def test_background_import_preview_to_candidate_registration(self):
        from gui.app import MainWindow
        from artwork.skraper_import import plan_import
        from database.store import Store
        from PyQt6.QtWidgets import QDialog, QComboBox, QLineEdit, QLabel, QPlainTextEdit
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            images = root / 'images'
            images.mkdir()
            (images / 'Game.nes.png').write_bytes(b'invalid')
            (images / 'Game.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='))
            db = Store(root / 'db.sqlite3')
            game = db.upsert_game('/games/NES/Game.nes')
            games = db.games()
            db.close()
            window = MainWindow(root / 'db.sqlite3')
            try:
                self.wait_idle(window)
                def accept(dialog):
                    regions = dialog.findChild(QComboBox)
                    summary = dialog.findChild(QLabel, 'importRegionSummary')
                    report = dialog.findChild(QPlainTextEdit)
                    original_report = report.toPlainText()
                    self.assertIn('1件の画像候補へ「不明（初期値）」', summary.text())
                    regions.setCurrentIndex(regions.findData('JP'))
                    self.assertIn('1件の画像候補へ「日本 / JP」', summary.text())
                    self.assertIn('画像や取り込み対象は変わりません', summary.text())
                    self.assertEqual(report.toPlainText(), original_report)
                    return QDialog.DialogCode.Accepted
                with patch.object(QDialog, 'exec', accept):
                    window.start_task(lambda cancelled, progress: plan_import(images, games,
                        cancelled=cancelled, progress=progress), window.preview_import, 'test')
                    self.wait_idle(window)
                db = Store(root / 'db.sqlite3')
                try:
                    self.assertEqual(len(db.artworks(game)), 1)
                    self.assertEqual(db.artworks(game)[0]['region'], 'JP')
                    self.assertIsNone(db.game(game)['artwork_path'])
                    self.assertIn('画像地域: JP', window.status_label.text())
                finally:
                    db.close()
            finally:
                self.wait_idle(window)
                window.close()
                self.app.processEvents()


    def test_recovery_dialog_previews_record_and_runs_recovery(self):
        import json
        from gui.app import MainWindow
        from PyQt6.QtWidgets import QDialog, QLineEdit, QPlainTextEdit, QCheckBox
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / 'transfer-backups' / ('a' * 32) / 'transfer.json'
            report.parent.mkdir(parents=True)
            report.write_text(json.dumps(dict(schema='mister-jp-transfer/v2',
                run_id='a' * 32, host='test-host', status='rollback_failed', items=[dict(
                    kind='rom', system='NES', remote_path='/media/fat/games/NES/Game.nes',
                    original_existed=False, sha256='b' * 64, status='restore_failed')])) , encoding='utf-8')
            window = MainWindow(root / 'db')
            try:
                self.wait_idle(window)
                self.assertFalse(window.recovery_button.isHidden())
                self.assertEqual(window.recovery_reports, [str(report.resolve())])
                def accept(dialog):
                    self.assertIn('/media/fat/games/NES/Game.nes', dialog.findChild(QPlainTextEdit).toPlainText())
                    fields = dialog.findChildren(QLineEdit)
                    self.assertEqual(fields[0].text(), 'test-host')
                    fields[-1].setText('secret')
                    return QDialog.DialogCode.Accepted
                with patch('PyQt6.QtWidgets.QFileDialog.getOpenFileName', return_value=(str(report), '')), patch(
                        'PyQt6.QtWidgets.QDialog.exec', new=accept), patch(
                        'gui.transfer_flow.recover_transfer', return_value=dict(
                            recovered=True, report=str(report), status='rolled_back')) as recover, patch(
                        'PyQt6.QtWidgets.QMessageBox.information'):
                    window.recovery_button.click()
                    self.wait_idle(window)
                recover.assert_called_once_with(str(report), 'test-host', 'root', 'secret')
                self.assertIn('復旧しました', window.status_label.text())
            finally:
                window.close()

    def test_transfer_dialog_exposes_optional_existing_rom_verification(self):
        from gui.transfer_dialog import TransferDialog
        dialog = TransferDialog([], {}, roms=[dict(local_path='Game.nes', remote_path='/media/fat/games/NES/Game.nes')])
        try:
            self.assertTrue(dialog.verify_roms.isEnabled())
            self.assertFalse(dialog.verify_roms.isChecked())
            dialog.verify_roms.setChecked(True)
            self.assertTrue(dialog.verify_roms.isChecked())
        finally:
            dialog.close()


if __name__ == '__main__':
    unittest.main()

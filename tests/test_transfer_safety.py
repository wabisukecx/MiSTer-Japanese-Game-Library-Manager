import hashlib
import io
import json
from pathlib import Path
import struct
import unittest
from unittest.mock import patch
import zlib

import test_artwork_transfer as fixtures
from artwork.skraper_import import plan_import, apply_import
from metadata.approval import approval_plan, apply_approval
from mister.artwork_transfer import transfer_plan, rom_transfer_plan
from mister.recovery import recover_transfer
from workflow import register_folder, batch_plan
from main import main


def changed_png():
    payload = b'Comment\x00updated'
    chunk = b'tEXt' + payload
    return fixtures.PNG[:-12] + struct.pack('>I', len(payload)) + chunk + struct.pack('>I', zlib.crc32(chunk)) + fixtures.PNG[-12:]


class SafetyTests(unittest.TestCase):
    setUp = fixtures.TransferTests.setUp
    tearDown = fixtures.TransferTests.tearDown
    run_transfer = fixtures.TransferTests.run_transfer
    report = fixtures.TransferTests.report
    enable_titles = fixtures.TransferTests.enable_titles
    local_rom = fixtures.TransferTests.local_rom
    alias_path = fixtures.TransferTests.alias_path

    def test_reimport_changed_selected_library_image_requires_approval(self):
        folder = self.root / 'output'
        folder.mkdir()
        source = folder / 'Game.png'
        source.write_bytes(fixtures.PNG)
        library = self.root / 'library'
        apply_import(self.db, plan_import(folder, self.db.games()), library=library)
        chosen = self.db.artworks(self.game)[-1]
        self.db.select_artwork(self.game, chosen['id'])
        apply_import(self.db, plan_import(folder, self.db.games()), library=library)
        self.assertEqual(self.db.game(self.game)['artwork_path'], chosen['local_path'])
        source.write_bytes(changed_png())
        result = apply_import(self.db, plan_import(folder, self.db.games()), library=library)
        self.assertEqual((result['updated'], result['errors']), (1, 0))
        self.assertIsNone(self.db.game(self.game)['artwork_path'])
        self.assertEqual(transfer_plan(self.db, self.db.games(), allow_empty=True), [])
        self.db.select_artwork(self.game, chosen['id'])
        self.assertEqual(len(transfer_plan(self.db, self.db.games())), 1)

    def corrections(self):
        parent = self.db.artworks(self.game)[0]
        for index in range(2):
            copy = self.root / ('rotated%d.png' % index)
            copy.write_bytes(fixtures.PNG)
            artwork_id = self.db.register_rotated_artwork(self.game, parent['id'], dict(
                path=str(copy), sha256=hashlib.sha256(fixtures.PNG).hexdigest(),
                original_sha256=parent['sha256'], rotation_degrees=90))
            parent = next(a for a in self.db.artworks(self.game) if a['id'] == artwork_id)
        return parent['id']

    def change_original(self):
        self.image.write_bytes(changed_png())
        self.db.save_artwork(self.game, 'test', 'JP', self.image.as_uri(), self.image,
                             hashlib.sha256(changed_png()).hexdigest(), select=False)

    def test_stale_ancestor_blocks_selected_correction_and_reselection(self):
        corrected = self.corrections()
        self.db.select_artwork(self.game, corrected)
        self.change_original()
        self.assertTrue(self.db.artworks(self.game)[-1]['edit_stale'])
        with self.assertRaisesRegex(ValueError, '補正元'):
            transfer_plan(self.db, self.db.games())
        with self.assertRaisesRegex(ValueError, '補正元'):
            self.db.select_artwork(self.game, corrected)

    def test_source_update_after_approval_preview_does_not_adopt_old_correction(self):
        self.corrections()
        self.db.db.execute('UPDATE games SET artwork_path=NULL WHERE id=?', (self.game,))
        self.db.db.commit()
        game = self.db.game(self.game)
        game['_titles'], game['_artworks'] = [], self.db.artworks(self.game)
        plan = approval_plan([game])
        self.assertIsNotNone(plan[0]['artwork'])
        self.change_original()
        self.assertEqual(apply_approval(self.db, plan)['errors'], 1)
        self.assertIsNone(self.db.game(self.game)['artwork_path'])

    def test_live_unregistered_alias_collision_stops_all_writes(self):
        self.enable_titles()
        self.ftp.files['/media/fat/games/SNES/subfolder/Game.sfc'] = b'not-in-local-db'
        with self.assertRaisesRegex(RuntimeError, '別名キーが重複'):
            self.run_transfer(titles={'Game': 'ゲーム'})
        self.assertEqual(self.ftp.writes, [])
        self.assertEqual(self.ftp.files[fixtures.MEDIA + '/Game.png'], b'original')

    def test_explicit_same_title_shared_with_registered_other_system_is_allowed(self):
        self.enable_titles()
        self.db.confirm(self.game, 'ゲーム')
        other_path = '/media/fat/games/SNES/Game.sfc'
        other = self.db.upsert_game(other_path, 'SNES')
        self.db.confirm(other, 'ゲーム')
        self.ftp.files[other_path] = b'other-rom'
        plan = batch_plan(self.db, [self.db.game(self.game)])
        result = self.run_transfer(titles=plan['titles'], title_targets=plan['title_targets'])
        self.assertEqual(result['titles_transferred'], 1)

    def test_alias_collision_created_during_upload_rolls_back_images(self):
        self.enable_titles()
        upload = self.ftp.storbinary
        def racing_upload(command, stream):
            upload(command, stream)
            self.ftp.files['/media/fat/games/GBC/Game.gbc'] = b'external'
        self.ftp.storbinary = racing_upload
        with self.assertRaises(RuntimeError):
            self.run_transfer(titles={'Game': 'ゲーム'})
        self.assertEqual(self.ftp.files[fixtures.MEDIA + '/Game.png'], b'original')
        self.assertEqual(self.ftp.files[self.alias_path], b'Unrelated=Preserve\nGame=Old\n')

    def test_unreadable_live_directory_fails_closed(self):
        self.enable_titles()
        listing = self.ftp.mlsd
        def denied(path):
            if path == '/media/fat/games':
                raise OSError('private server response')
            return listing(path)
        self.ftp.mlsd = denied
        with self.assertRaises(RuntimeError) as error:
            self.run_transfer(titles={'Game': 'ゲーム'})
        self.assertNotIn('private server response', str(error.exception))
        self.assertEqual(self.ftp.writes, [])

    def test_optional_existing_rom_mismatch_stops_before_image_writes(self):
        roms = self.local_rom()
        self.ftp.files[roms[0]['remote_path']] = b'different'
        with self.assertRaisesRegex(RuntimeError, '既存ROMの内容'):
            self.run_transfer(roms=roms, verify_existing_roms=True)
        self.assertEqual(self.ftp.writes, [])

    def test_optional_existing_rom_match_handles_case_and_skips(self):
        roms = self.local_rom()
        self.ftp.files['/media/fat/games/NES/GAME.NES'] = Path(roms[0]['local_path']).read_bytes()
        result = self.run_transfer(roms=roms, verify_existing_roms=True)
        self.assertEqual(result['roms_skipped'], 1)

    def test_stream_hash_does_not_depend_on_python_311_file_digest(self):
        self.local_rom()
        with patch.object(hashlib, 'file_digest', side_effect=AssertionError('not available'), create=True):
            self.assertEqual(len(rom_transfer_plan(self.db.games())), 1)

    def test_cancel_after_rom_upload_removes_new_rom_from_library_by_quarantine(self):
        roms = self.local_rom()
        done = []
        result = self.run_transfer(roms=roms, progress=lambda *args: done.append(args), cancelled=lambda: bool(done))
        self.assertTrue(result['rolled_back'])
        self.assertNotIn(roms[0]['remote_path'], self.ftp.files)
        self.assertEqual(self.ftp.files[fixtures.MEDIA + '/Game.png'], b'original')
        row = self.report()['items'][0]
        self.assertEqual(self.ftp.files[row['rollback_copy']], Path(roms[0]['local_path']).read_bytes())

    def test_lost_publish_reply_is_recovered_without_overwriting_new_file(self):
        rename = self.ftp.rename
        def lose_reply(source, target):
            rename(source, target)
            if source.endswith('.tmp'):
                raise OSError('lost reply')
        self.ftp.rename = lose_reply
        with self.assertRaises(RuntimeError):
            self.run_transfer()
        self.assertEqual(self.ftp.files[fixtures.MEDIA + '/Game.png'], b'original')
        self.assertEqual(self.report()['status'], 'rolled_back')

    def test_disconnected_rollback_can_be_retried_from_journal(self):
        self.items.append(dict(self.items[0], remote_path=fixtures.MEDIA + '/Other.png'))
        listing = self.ftp.mlsd
        disconnected = []
        def maybe_disconnected(path):
            if disconnected:
                raise OSError('offline')
            return listing(path)
        self.ftp.mlsd = maybe_disconnected
        result = self.run_transfer(progress=lambda *args: disconnected.append(True), cancelled=lambda: bool(disconnected))
        self.assertFalse(result['rolled_back'])
        self.assertEqual(self.report()['status'], 'rollback_failed')
        disconnected.clear()
        recovered = recover_transfer(result['report'], 'test', 'root', 'secret', ftp_factory=lambda: self.ftp)
        self.assertTrue(recovered['recovered'])
        self.assertEqual(self.ftp.files[fixtures.MEDIA + '/Game.png'], b'original')
        # Retrying a completed recovery performs no further mutations.
        writes = list(self.ftp.writes)
        recover_transfer(result['report'], 'test', 'root', 'secret', ftp_factory=lambda: self.ftp)
        self.assertEqual(self.ftp.writes, writes)

    def test_external_change_is_preserved_and_other_batch_files_are_restored(self):
        self.items.append(dict(self.items[0], remote_path=fixtures.MEDIA + '/Other.png'))
        def external_change(done, total):
            self.ftp.files[fixtures.MEDIA + '/Game.png'] = b'external-editor'
        result = self.run_transfer(progress=external_change, cancelled=lambda: self.ftp.files[fixtures.MEDIA + '/Game.png'] == b'external-editor')
        self.assertFalse(result['rolled_back'])
        self.assertEqual(self.ftp.files[fixtures.MEDIA + '/Game.png'], b'external-editor')
        self.assertEqual(self.ftp.files[fixtures.MEDIA + '/Other.png'], b'other')
        row = self.report()['items'][0]
        self.assertEqual(self.ftp.files[row['remote_backup']], b'original')
        self.ftp.files[fixtures.MEDIA + '/Game.png'] = fixtures.PNG
        self.assertTrue(recover_transfer(result['report'], 'test', 'root', 'secret', ftp_factory=lambda: self.ftp)['recovered'])

    def test_manual_recovery_rejects_different_host_and_unsafe_paths_before_connect(self):
        result = self.run_transfer()
        with self.assertRaisesRegex(ValueError, '同じ実機'):
            recover_transfer(result['report'], 'wrong-host', 'root', 'secret', ftp_factory=lambda: self.fail('connected'))
        path = Path(result['report'])
        journal = json.loads(path.read_text(encoding='utf-8'))
        journal['items'][0]['remote_path'] = '/media/fat/../other.png'
        path.write_text(json.dumps(journal), encoding='utf-8')
        with self.assertRaises(ValueError):
            recover_transfer(path, 'test', 'root', 'secret', ftp_factory=lambda: self.fail('connected'))

    def test_registration_service_recurses_deduplicates_and_keeps_scope(self):
        folder = self.root / 'roms'
        for name in ('a/deep', 'media', '.hidden'):
            (folder / name).mkdir(parents=True)
        for name in ('a/Game.nes', 'a/deep/GAME.NES', 'media/Skip.nes', '.hidden/Hidden.nes'):
            (folder / name).write_bytes(b'rom')
        result = register_folder(self.db, folder, 'NES', '/media/fat/games/NES', self.root, lookup=False)
        self.assertEqual((result['count'], result['duplicates']), (1, ['GAME.NES']))
        self.assertEqual(self.db.setting('workflow/NES')['rom_folder'], str(folder))

    def test_batch_plan_uses_live_db_selection(self):
        old_view = self.db.games()
        self.db.db.execute('UPDATE games SET artwork_path=NULL')
        self.db.db.commit()
        self.assertEqual(batch_plan(self.db, old_view)['images'], [])

    def test_cli_recovery_does_not_open_database(self):
        with patch('main.getpass.getpass', return_value='secret'), patch(
                'mister.recovery.recover_transfer', return_value=dict(recovered=True)), patch(
                'main.Store', side_effect=AssertionError('opened DB')), patch('sys.stdout', new=io.StringIO()):
            self.assertEqual(main(['recover-transfer', 'transfer.json', '--host', 'test']), 0)

    def test_cli_registration_uses_shared_recursive_workflow_without_network(self):
        folder = self.root / 'cli-roms' / 'sub'
        folder.mkdir(parents=True)
        (folder / 'New.nes').write_bytes(b'rom')
        with patch('sys.stdout', new=io.StringIO()) as output:
            self.assertEqual(main(['--db', str(self.root / 'cli-db'), 'register-roms', str(folder.parent),
                                   '--system', 'NES']), 0)
            self.assertEqual(json.loads(output.getvalue())['count'], 1)

    def test_optional_existing_rom_check_rejects_pc_changes_after_review(self):
        roms = self.local_rom()
        self.ftp.files[roms[0]['remote_path']] = Path(roms[0]['local_path']).read_bytes()
        Path(roms[0]['local_path']).write_bytes(b'updated-pc-rom')
        with self.assertRaisesRegex(RuntimeError, 'PCのROMが変更'):
            self.run_transfer(roms=roms, verify_existing_roms=True)
        self.assertEqual(self.ftp.writes, [])

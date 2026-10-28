import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from database.store import Store
from mister.artwork_transfer import MEDIA, transfer_plan, transfer_images, rom_transfer_plan

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')


class FTP:
    def __init__(self):
        self.files = {MEDIA + '/Game.png': b'original', MEDIA + '/Other.png': b'other',
                      MEDIA + '/Game-BG.png': b'background', MEDIA + '/optimized/cache': b'cache'}
        self.writes = []
        self.fail_publish = False
        self.corrupt = False
        self.logins = 0

    def connect(self, *args, **kwargs): pass
    def login(self, *args): self.logins += 1
    def close(self): pass
    def mlsd(self, path):
        entries = {}
        for p in self.files:
            if p.startswith(path + '/'):
                remainder = p[len(path) + 1:]
                entries[remainder.split('/')[0]] = dict(type='dir' if '/' in remainder else 'file')
        return list(entries.items())
    def retrbinary(self, command, callback): callback(self.files[command[5:]])
    def mkd(self, path): self.writes.append(('mkdir', path))
    def storbinary(self, command, stream):
        self.writes.append(('upload', command[5:]))
        self.files[command[5:]] = b'corrupt' if self.corrupt else stream.read()
    def rename(self, source, target):
        if self.fail_publish and source.endswith('.tmp'):
            raise OSError('secret server response')
        self.writes.append(('rename', source, target))
        self.files[target] = self.files.pop(source)


class TransferTests(unittest.TestCase):
    alias_path = '/media/fat/ConsoleMode/themeconfig/alias.txt'

    def enable_titles(self):
        self.ftp.files.update({
            '/media/fat/ConsoleMode/config.ini': ('[GLOBAL]\naliasPath=' + self.alias_path + '\n').encode(),
            '/media/fat/ConsoleMode/fonts/NotoSansCJK-Regular.otf': b'font',
            self.alias_path: b'Unrelated=Preserve\nGame=Old\n',
        })

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = Store(self.root / 'db')
        self.game = self.db.upsert_game('test', '/media/fat/games/NES/Game.nes')
        self.image = self.root / 'art.png'
        self.image.write_bytes(PNG)
        self.db.save_artwork(self.game, 'test', 'JP', self.image.as_uri(), self.image,
                             hashlib.sha256(PNG).hexdigest())
        self.items = transfer_plan(self.db, self.db.games())
        self.ftp = FTP()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def run_transfer(self, **kwargs):
        return transfer_images(self.items, 'test', 'root', 'password-secret', self.root / 'backups',
                               ftp_factory=lambda: self.ftp, **kwargs)

    def report(self):
        return json.loads(next((self.root / 'backups').glob('*/transfer.json')).read_text(encoding='utf-8'))

    def test_verified_transfer_preserves_original_and_unrelated_files(self):
        result = self.run_transfer()
        self.assertEqual(result['transferred'], 1)
        row = self.report()['items'][0]
        self.assertEqual(Path(row['backup']).read_bytes(), b'original')
        self.assertEqual(self.ftp.files[row['remote_backup']], b'original')
        self.assertEqual(self.ftp.files[MEDIA + '/Game.png'], PNG)
        self.assertEqual(self.ftp.files[MEDIA + '/Game-BG.png'], b'background')
        self.assertEqual(self.ftp.files[MEDIA + '/optimized/cache'], b'cache')
        self.assertNotIn('password-secret', Path(result['report']).read_text())
        self.assertEqual(self.db.game(self.game)['sync_status'], 'blocked')

    def test_publish_failure_restores_target(self):
        self.ftp.fail_publish = True
        with self.assertRaises(RuntimeError) as error:
            self.run_transfer()
        self.assertNotIn('secret server response', str(error.exception))
        self.assertEqual(self.ftp.files[MEDIA + '/Game.png'], b'original')
        self.assertEqual(self.report()['items'][0]['status'], 'restored')

    def test_corrupt_stage_does_not_replace_original(self):
        self.ftp.corrupt = True
        with self.assertRaises(RuntimeError): self.run_transfer()
        self.assertEqual(self.ftp.files[MEDIA + '/Game.png'], b'original')
        self.assertFalse(any(w[0] == 'rename' for w in self.ftp.writes))

    def test_directory_at_second_target_aborts_before_writes(self):
        other = dict(self.items[0], remote_path=MEDIA + '/Missing.png')
        self.items.append(other)
        self.ftp.files[MEDIA + '/Missing.png/child'] = b'directory'
        with self.assertRaises(RuntimeError): self.run_transfer()
        self.assertEqual(self.ftp.writes, [])

    def local_rom(self):
        source = self.root / 'Game.nes'
        source.write_bytes(b'local-rom')
        game = self.db.register_local_rom('test', 'NES', source)
        self.assertEqual(game, self.game)
        return rom_transfer_plan(self.db.games())

    def test_missing_rom_and_image_are_added_before_titles(self):
        self.enable_titles()
        self.ftp.files.pop(MEDIA + '/Game.png')
        result = self.run_transfer(roms=self.local_rom(), titles={'Game': 'ゲーム'})
        self.assertEqual((result['roms_transferred'], result['transferred'], result['titles_transferred']), (1, 1, 1))
        self.assertEqual([r['kind'] for r in self.report()['items']], ['rom', 'image', 'titles'])
        self.assertEqual(self.ftp.files['/media/fat/games/NES/Game.nes'], b'local-rom')
        self.assertEqual(self.ftp.files[MEDIA + '/Game.png'], PNG)

    def test_existing_rom_is_skipped_without_reading_or_overwriting(self):
        target = '/media/fat/games/NES/Game.nes'
        self.ftp.files[target] = b'keep-existing-even-if-different'
        retrieve = self.ftp.retrbinary
        def guarded(command, callback):
            self.assertNotEqual(command, 'RETR ' + target)
            retrieve(command, callback)
        self.ftp.retrbinary = guarded
        result = self.run_transfer(roms=self.local_rom())
        self.assertEqual((result['roms_transferred'], result['roms_skipped']), (0, 1))
        self.assertEqual(self.ftp.files[target], b'keep-existing-even-if-different')

    def test_rom_only_transfer_and_changed_rom_preflight(self):
        roms = self.local_rom()
        self.items = []
        Path(roms[0]['local_path']).write_bytes(b'changed')
        with self.assertRaises(RuntimeError):
            self.run_transfer(roms=roms)
        self.assertEqual(self.ftp.writes, [])

    def test_new_media_directory_and_rom_only_transfer(self):
        roms = self.local_rom()
        self.ftp.files = {'/media/fat/games/NES/Other.nes': b'other'}
        result = self.run_transfer(roms=roms)
        self.assertEqual(result['roms_transferred'], 1)
        self.assertIn(('mkdir', MEDIA), self.ftp.writes)
        self.assertEqual(self.ftp.files[MEDIA + '/Game.png'], PNG)

    def test_rom_appearing_during_staging_is_not_overwritten(self):
        roms = self.local_rom()
        target = roms[0]['remote_path']
        upload = self.ftp.storbinary
        def race(command, stream):
            upload(command, stream)
            self.ftp.files[target] = b'other-client'
        self.ftp.storbinary = race
        with self.assertRaises(RuntimeError):
            self.run_transfer(roms=roms)
        self.assertEqual(self.ftp.files[target], b'other-client')
        self.assertFalse(any(w[0] == 'rename' for w in self.ftp.writes))

    def test_rom_only_success(self):
        roms = self.local_rom()
        self.items = []
        result = self.run_transfer(roms=roms)
        self.assertEqual((result['roms_transferred'], result['transferred']), (1, 0))
        self.assertFalse(any(w[0] == 'mkdir' for w in self.ftp.writes))

    def test_existing_rom_case_difference_is_skipped(self):
        roms = self.local_rom()
        self.ftp.files['/media/fat/games/NES/GAME.NES'] = b'existing'
        result = self.run_transfer(roms=roms)
        self.assertEqual(result['roms_skipped'], 1)
        self.assertNotIn('/media/fat/games/NES/Game.nes', self.ftp.files)

    def test_corrupt_rom_stage_is_not_published(self):
        roms = self.local_rom()
        self.ftp.corrupt = True
        with self.assertRaises(RuntimeError):
            self.run_transfer(roms=roms)
        self.assertNotIn(roms[0]['remote_path'], self.ftp.files)
        self.assertFalse(any(w[0] == 'rename' for w in self.ftp.writes))

    def test_local_registration_preserves_metadata_and_explicit_genesis_root(self):
        self.db.confirm(self.game, 'ゲーム')
        self.local_rom()
        game = self.db.game(self.game)
        self.assertEqual(game['japanese_title'], 'ゲーム')
        self.assertEqual(game['artwork_path'], str(self.image))
        source = self.root / 'Example.md'
        source.write_bytes(b'md-rom')
        game_id = self.db.register_local_rom('test', 'MD', source, '/media/fat/games/Genesis')
        self.assertEqual(self.db.game(game_id)['rom_path'], '/media/fat/games/Genesis/Example.md')
        with self.assertRaises(ValueError):
            self.db.register_local_rom('test', 'SNES', source)

    def test_changed_local_image_aborts_before_writes(self):
        self.image.write_bytes(b'changed')
        with self.assertRaises(RuntimeError): self.run_transfer()
        self.assertEqual(self.ftp.writes, [])

    def test_cancel_before_writes(self):
        result = self.run_transfer(cancelled=lambda: True)
        self.assertTrue(result['cancelled'])
        self.assertEqual(self.ftp.writes, [])

    def test_cancel_between_images_restores_completed_file_and_keeps_second_original(self):
        self.items.append(dict(self.items[0], remote_path=MEDIA + '/Other.png'))
        finished = []
        result = self.run_transfer(cancelled=lambda: bool(finished), progress=lambda done, total: finished.append(done))
        self.assertTrue(result['cancelled'])
        self.assertEqual(result['transferred'], 0)
        self.assertTrue(result['rolled_back'])
        self.assertEqual(self.ftp.files[MEDIA + '/Game.png'], b'original')
        self.assertEqual(self.ftp.files[MEDIA + '/Other.png'], b'other')
        self.assertEqual(Path(self.report()['items'][1]['backup']).read_bytes(), b'other')

    def test_plan_rejects_other_root_and_multiple_devices_same_path(self):
        with self.assertRaises(ValueError):
            transfer_plan(self.db, [dict(self.db.game(self.game), rom_path='/elsewhere/Game.nes')])
        with self.assertRaises(ValueError):
            transfer_plan(self.db, self.db.games() * 2)

    def test_images_and_titles_share_login_and_all_backups_precede_writes(self):
        self.enable_titles()
        original_alias = self.ftp.files[self.alias_path]
        upload = self.ftp.storbinary
        def checked_upload(command, stream):
            report = self.report()
            self.assertEqual(len(report['items']), 2)
            for row in report['items']:
                self.assertEqual(hashlib.sha256(Path(row['backup']).read_bytes()).hexdigest(), row['original_sha256'])
            upload(command, stream)
        self.ftp.storbinary = checked_upload
        result = self.run_transfer(titles={'Game': 'ゲーム'})
        self.assertEqual(self.ftp.logins, 1)
        self.assertEqual((result['transferred'], result['titles_transferred']), (1, 1))
        self.assertEqual(self.ftp.files[MEDIA + '/Game.png'], PNG)
        self.assertEqual(self.ftp.files[self.alias_path], 'Unrelated=Preserve\nGame=ゲーム\n'.encode())
        title = self.report()['items'][1]
        self.assertEqual(title['kind'], 'titles')
        self.assertEqual(self.ftp.files[title['remote_backup']], original_alias)
        self.assertEqual(self.report()['status'], 'verified')

    def test_missing_font_prevents_image_writes_too(self):
        self.enable_titles()
        self.ftp.files.pop('/media/fat/ConsoleMode/fonts/NotoSansCJK-Regular.otf')
        with self.assertRaises(RuntimeError): self.run_transfer(titles={'Game': 'ゲーム'})
        self.assertEqual(self.ftp.writes, [])
        self.assertEqual(self.ftp.files[MEDIA + '/Game.png'], b'original')

    def test_title_only_does_not_access_media_or_create_image_backup_folder(self):
        self.enable_titles()
        self.items = []
        listing = self.ftp.mlsd
        def list_without_media(path):
            self.assertNotEqual(path, MEDIA)
            return listing(path)
        self.ftp.mlsd = list_without_media
        result = self.run_transfer(titles={'Game': 'ゲーム'})
        self.assertEqual((result['transferred'], result['titles_transferred']), (0, 1))
        self.assertFalse(any(w[0] == 'mkdir' for w in self.ftp.writes))

    def test_cancel_after_image_reports_titles_not_transferred(self):
        self.enable_titles()
        finished = []
        result = self.run_transfer(titles={'Game': 'ゲーム'}, cancelled=lambda: bool(finished),
                                   progress=lambda done, total: finished.append((done, total)))
        self.assertTrue(result['cancelled'])
        self.assertEqual((result['transferred'], result['titles_transferred']), (0, 0))
        self.assertEqual(finished, [(1, 2)])
        self.assertEqual(self.ftp.files[self.alias_path], b'Unrelated=Preserve\nGame=Old\n')
        self.assertEqual(self.report()['items'][1]['status'], 'backed_up')

    def test_title_publish_failure_rolls_back_whole_batch(self):
        self.enable_titles()
        rename = self.ftp.rename
        def fail_title_only(source, target):
            if source.endswith('.tmp') and target == self.alias_path:
                raise OSError('secret server response')
            rename(source, target)
        self.ftp.rename = fail_title_only
        with self.assertRaises(RuntimeError): self.run_transfer(titles={'Game': 'ゲーム'})
        self.assertEqual(self.ftp.files[MEDIA + '/Game.png'], b'original')
        self.assertEqual(self.ftp.files[self.alias_path], b'Unrelated=Preserve\nGame=Old\n')
        self.assertEqual([r['status'] for r in self.report()['items']], ['restored', 'restored'])
        self.assertEqual(self.report()['status'], 'rolled_back')

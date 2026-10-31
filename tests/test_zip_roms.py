import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile

from local_rom import rom_info, open_rom
from database.store import Store
from mister.artwork_transfer import rom_transfer_plan, transfer_images
from artwork.skraper_import import plan_import, apply_import
from metadata.retronian import title_candidates
from test_artwork_transfer import FTP, PNG


class ZipROMTests(unittest.TestCase):
    def test_archive_registration_preserves_game_and_uploads_the_zip_as_is(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / 'Archive name.zip'
            data = b'NES' * 10000
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
                z.writestr('folder/Game (Japan).nes', data)
            original = archive.read_bytes()
            db = Store(root / 'db')
            try:
                game_id = db.register_local_rom('NES', archive)
                db.confirm(game_id, 'ゲーム')
                self.assertEqual(db.register_local_rom('NES', archive), game_id)
                game = db.game(game_id)
                self.assertEqual(game['rom_path'], '/media/fat/games/NES/Archive name.zip')
                self.assertEqual(game['japanese_title'], 'ゲーム')
                self.assertEqual(game['archive_member'], 'folder/Game (Japan).nes')
                plan = rom_transfer_plan([game])
                self.assertEqual(plan[0]['sha256'], hashlib.sha256(original).hexdigest())
                self.assertEqual(plan[0]['size'], len(original))
                rows = [dict(id='game', roms=[dict(name='Other', sha256=hashlib.sha256(data).hexdigest())],
                    titles=[dict(text='ゲーム', lang='ja', script='Kana')])]
                self.assertIn('SHA-256', title_candidates(game, rows)[0]['reason'])
                images = root / 'media'
                images.mkdir()
                (images / 'Archive name.png').write_bytes(PNG)
                self.assertEqual(apply_import(db, plan_import(images, [game]), 'JP')['imported'], 1)
                ftp = FTP()
                result = transfer_images([], 'test', 'root', 'secret', root / 'backups',
                    roms=plan, ftp_factory=lambda: ftp)
                self.assertEqual(result['roms_transferred'], 1)
                self.assertEqual(ftp.files[game['rom_path']], original)
                self.assertEqual(archive.read_bytes(), original)
                self.assertFalse((root / 'Game (Japan).nes').exists())
                result = transfer_images([], 'test', 'root', 'secret', root / 'backups',
                    roms=plan, ftp_factory=lambda: ftp)
                self.assertEqual(result['roms_skipped'], 1)
            finally:
                db.close()

    def test_game_registered_under_extracted_name_moves_to_its_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / 'Game (Japan).zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('Game (Japan).nes', b'rom')
            (root / 'Plain.nes').write_bytes(b'x')
            db = Store(root / 'db')
            try:
                old = db.register_local_rom('NES', root / 'Plain.nes')
                legacy = db.upsert_game('/media/fat/games/NES/Game (Japan).nes')
                with db.db:
                    db.db.execute('INSERT INTO local_roms(game_id,local_rom_path) VALUES(?,?)', (legacy, str(archive)))
                db.confirm(legacy, '旧確定')
                db.save_artwork(legacy, 'skraper', 'JP', 'file:///a.png', 'a.png', 'h')
                remote_only = db.upsert_game('/media/fat/games/NES/Other.nes')
                new = db.register_local_rom('NES', archive)
                game = db.game(new)
                self.assertEqual((game['japanese_title'], game['match_status'], game['artwork_path']),
                                 ('旧確定', 'confirmed', 'a.png'))
                self.assertEqual(len(db.artworks(new)), 1)
                self.assertEqual({g['id'] for g in db.games()}, {old, remote_only, new})
            finally:
                db.close()

    def test_multiple_roms_traversal_and_changed_archive_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'Game.zip'
            for entries in [('One.nes', 'Two.nes'), ('../Game.nes',)]:
                with zipfile.ZipFile(archive, 'w') as z:
                    for name in entries:
                        z.writestr(name, b'rom')
                with self.assertRaises(ValueError):
                    rom_info(archive, 'NES')
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('Game.nes', b'rom')
            with self.assertRaises(ValueError):
                with open_rom(archive, 'NES', 'Different.nes'):
                    pass

    def test_archive_changed_after_review_stops_before_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / 'Game.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('Game.nes', b'rom')
            db = Store(root / 'db')
            try:
                db.register_local_rom('NES', archive)
                plan = rom_transfer_plan(db.games())
                with zipfile.ZipFile(archive, 'w') as z:
                    z.writestr('Game.nes', b'changed')
                ftp = FTP()
                with self.assertRaises(RuntimeError):
                    transfer_images([], 'test', 'root', 'secret', root / 'backups',
                        roms=plan, ftp_factory=lambda: ftp)
                self.assertEqual(ftp.writes, [])
            finally:
                db.close()

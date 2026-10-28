from pathlib import Path
import tempfile
import unittest

from database.store import Store
from metadata.retronian import match_titles
from artwork.skraper_import import plan_import, apply_import
from metadata.approval import approval_plan, apply_approval
from consolemode.titles import title_mapping
from mister.artwork_transfer import transfer_plan, rom_transfer_plan, transfer_images
from test_artwork_transfer import FTP, PNG


class WorkflowTests(unittest.TestCase):
    def test_pc_rom_db_title_skraper_output_approval_and_missing_rom_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'Game (Japan).sfc'
            source.write_bytes(b'owned-local-rom')
            images = root / 'images'
            images.mkdir()
            (images / 'Game (Japan).png').write_bytes(PNG)
            db = Store(root / 'db')
            try:
                game_id = db.register_local_rom('test', 'SNES', source)
                def loader(system, path, **kwargs):
                    self.assertEqual(system, 'SNES')
                    return [dict(id='game', roms=[dict(name=source.stem)], titles=[dict(
                        text='ゲーム', lang='ja', script='Kana', source='fixture')])], 'cache'
                match_titles(db, db.games(), root, loader=loader)
                game = db.game(game_id)
                self.assertIsNone(game['japanese_title'])
                imported = apply_import(db, plan_import(images, db.games()), 'JP')
                self.assertEqual(imported['imported'], 1)
                game['_titles'], game['_artworks'] = db.get_candidates(game_id), db.artworks(game_id)
                applied = apply_approval(db, approval_plan([game]))
                self.assertEqual((applied['titles'], applied['images']), (1, 1))
                ftp = FTP()
                alias = '/media/fat/ConsoleMode/themeconfig/alias.txt'
                ftp.files = {
                    '/media/fat/games/SNES/Other.sfc': b'unrelated',
                    '/media/fat/ConsoleMode/config.ini': ('[GLOBAL]\naliasPath=' + alias + '\n').encode(),
                    '/media/fat/ConsoleMode/fonts/NotoSansCJK-Regular.otf': b'font',
                    alias: b'Other=Preserve\n',
                }
                games = db.games()
                result = transfer_images(transfer_plan(db, games), 'test', 'root', 'secret', root / 'backup',
                    titles=title_mapping(games), roms=rom_transfer_plan(games), ftp_factory=lambda: ftp)
                self.assertEqual((result['roms_transferred'], result['transferred'], result['titles_transferred']), (1, 1, 1))
                self.assertEqual(ftp.logins, 1)
                self.assertEqual(ftp.files['/media/fat/games/SNES/' + source.name], source.read_bytes())
                self.assertEqual(ftp.files['/media/fat/games/SNES/media/Game (Japan).png'], PNG)
                self.assertEqual(ftp.files[alias], 'Other=Preserve\nGame (Japan)=ゲーム\n'.encode())
            finally:
                db.close()

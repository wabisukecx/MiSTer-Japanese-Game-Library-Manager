import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from artwork.skraper_config import system_paths
from artwork.skraper_import import plan_import, apply_import
from database.store import Store
from test_skraper_import import PNG


class LibraryTests(unittest.TestCase):
    def test_reads_only_paths_for_the_system_and_expands_rom_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = dict(ScreenScraperAccount=dict(Password='secret'), Systems=[
                dict(SystemId=4, GamesAndRoms=dict(Path='C:/r/SNES', Platform='S'), Medias=[
                    dict(Type=5, OutputFolder='%ROMROOTFOLDER%/manual'),
                    dict(Type=0, OutputFolder='%ROMROOTFOLDER%/media/box2dfront')])])
            (Path(tmp) / 'skraper.config.json').write_text(json.dumps(config), encoding='utf-8-sig')
            found = system_paths(tmp, 4)
            self.assertEqual(Path(found['image_folder']), Path('C:/r/SNES/media/box2dfront'))
            self.assertEqual(set(found), {'rom_folder', 'image_folder'})
            self.assertIsNone(system_paths(tmp, 3))
            self.assertIsNone(system_paths(Path(tmp) / 'missing', 4))

    def test_import_copies_into_library_by_system_and_adopts_earlier_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skraper, library = root / 'rom' / 'media' / 'box2dfront', root / 'images'
            skraper.mkdir(parents=True)
            image = skraper / 'Game.png'
            image.write_bytes(PNG)
            db = Store(root / 'db')
            try:
                game = db.upsert_game('/media/fat/games/NES/Game.nes')
                # Imported before the library existed and already selected.
                db.save_artwork(game, 'skraper', 'JP', image.resolve().as_uri(), image.resolve(),
                                hashlib.sha256(PNG).hexdigest())
                result = apply_import(db, plan_import(skraper, db.games()), 'JP', library=library)
                copy = (library / 'NES' / 'Game.png').resolve()
                self.assertEqual((result['imported'], result['errors']), (1, 0))
                self.assertEqual(copy.read_bytes(), PNG)
                rows = db.artworks(game)
                self.assertEqual([r['local_path'] for r in rows], [str(copy)])
                self.assertEqual(db.game(game)['artwork_path'], str(copy))
                image.unlink()  # Skraper's folder can be cleaned; the library copy stays referenced.
                self.assertTrue(Path(db.game(game)['artwork_path']).is_file())
            finally:
                db.close()


if __name__ == '__main__':
    unittest.main()

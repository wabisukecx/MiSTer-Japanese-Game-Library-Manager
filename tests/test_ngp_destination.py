from pathlib import Path
import sqlite3
import tempfile
import unittest

from database.store import Store


class NGPDestinationTests(unittest.TestCase):
    def test_old_targets_migrate_without_changing_pc_folders_or_confirmed_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'db'
            db = Store(path)
            current = db.upsert_game('/media/fat/games/NGPC/Mono.ngp', 'NGP')
            legacy = db.upsert_game('/media/fat/games/NGP/Mono.ngp', 'NGP')
            db.confirm(legacy, 'モノクロ版')
            color = db.upsert_game('/media/fat/games/NGP/Color.ngc', 'NGPC')
            db.confirm(color, 'カラー版')
            for system in ('NGP', 'NGPC'):
                db.set_setting('workflow/' + system, dict(system=system,
                    rom_folder=str(Path(tmp) / system), image_folder=str(Path(tmp) / (system + '-images')),
                    remote_root='/media/fat/games/NGP'))
            db.close()

            db = Store(path)
            try:
                self.assertEqual(len(db.games()), 2)
                self.assertEqual(db.game(current)['japanese_title'], 'モノクロ版')
                self.assertEqual(db.game(color)['japanese_title'], 'カラー版')
                for game in db.games():
                    self.assertTrue(game['rom_path'].startswith('/media/fat/games/NGPC/'))
                for system in ('NGP', 'NGPC'):
                    profile = db.setting('workflow/' + system)
                    self.assertEqual(profile['remote_root'], '/media/fat/games/NGPC')
                    self.assertEqual(profile['rom_folder'], str(Path(tmp) / system))
                    self.assertEqual(profile['image_folder'], str(Path(tmp) / (system + '-images')))
                self.assertEqual(db.db.execute('PRAGMA foreign_key_check').fetchall(), [])
            finally:
                db.close()
            backup = sqlite3.connect(str(path) + '.before-ngpc-folder.sqlite3')
            try:
                self.assertEqual(backup.execute('SELECT COUNT(*) FROM games').fetchone()[0], 3)
            finally:
                backup.close()
            db = Store(path)
            self.assertEqual(len(db.games()), 2)
            db.close()

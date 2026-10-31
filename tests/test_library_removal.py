from pathlib import Path
import sqlite3
import tempfile
import unittest

from database.store import Store


class LibraryRemovalTests(unittest.TestCase):
    def test_merges_old_groups_with_backup_and_preserves_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'db.sqlite3'
            db = Store(path)
            ids = []
            for group in ('home-mister', 'home'):
                with db.db:
                    ids.append(db.db.execute('''INSERT INTO games
                        (device,system,rom_path,rom_filename,original_title)
                        VALUES (?,'NES','/media/fat/games/NES/Game.nes','Game.nes','Game')''',
                        (group,)).lastrowid)
            db.confirm(ids[1], 'ゲーム')
            db.save_candidates(ids[0], [dict(japanese_title='候補', score=.8)])
            db.save_artwork(ids[0], 'test', 'JP', 'test:image', Path(tmp) / 'first.png', 'a' * 64)
            db.save_artwork(ids[1], 'test', 'US', 'test:image', Path(tmp) / 'second.png', 'b' * 64)
            first_art = db.artworks(ids[0])[0]['id']
            second_art = db.artworks(ids[1])[0]['id']
            with db.db:
                db.db.execute('INSERT INTO artwork_edits VALUES (?,?,90,?)', (second_art, first_art, 'a' * 64))
                db.db.execute('INSERT INTO local_roms VALUES (?,?,NULL)', (ids[1], str(Path(tmp) / 'Game.nes')))
            db.set_setting('workflow/home-mister/NES', dict(system='NES', rom_folder='first', device='home-mister'))
            db.set_setting('workflow/home/NES', dict(system='NES', rom_folder='second', device='home'))
            db.set_setting('ui/scope', dict(system='NES', rom_folder='first', device='home-mister'))
            db.close()

            db = Store(path)
            try:
                self.assertEqual(len(db.games()), 1)
                game = db.games()[0]
                self.assertEqual(game['japanese_title'], 'ゲーム')
                self.assertEqual(game['match_status'], 'confirmed')
                self.assertEqual(game['artwork_path'], str(Path(tmp) / 'first.png'))
                self.assertEqual(len(db.artworks(game['id'])), 2)
                self.assertEqual(db.game(game['id'])['local_rom_path'], str(Path(tmp) / 'Game.nes'))
                self.assertEqual(db.db.execute('SELECT source_artwork_id FROM artwork_edits WHERE artwork_id=?',
                                               (second_art,)).fetchone()[0], first_art)
                self.assertEqual(db.get_candidates(game['id'])[0]['japanese_title'], '候補')
                self.assertTrue(db.title_decisions(game['id']))
                self.assertEqual(db.setting('workflow/NES')['rom_folder'], 'first')
                self.assertNotIn('device', db.setting('workflow/NES'))
                self.assertNotIn('device', db.setting('ui/scope'))
                self.assertEqual(db.upsert_game(game['rom_path']), game['id'])
                self.assertEqual(db.db.execute('PRAGMA foreign_key_check').fetchall(), [])
            finally:
                db.close()
            backup = Path(str(path) + '.before-library-removal.sqlite3')
            original = backup.read_bytes()
            connection = sqlite3.connect(backup)
            try:
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM games').fetchone()[0], 2)
            finally:
                connection.close()
            db = Store(path)
            self.assertEqual(len(db.games()), 1)
            db.close()
            self.assertEqual(backup.read_bytes(), original)

    def test_three_conflicting_confirmed_names_stay_unconfirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'db'
            db = Store(path)
            for index, title in enumerate(('名前A', '名前B', '名前C')):
                with db.db:
                    ident = db.db.execute('''INSERT INTO games
                        (device,system,rom_path,rom_filename,original_title)
                        VALUES (?,'NES','/media/fat/games/NES/Game.nes','Game.nes','Game')''',
                        (str(index),)).lastrowid
                db.confirm(ident, title)
            db.close()
            db = Store(path)
            try:
                game, = db.games()
                self.assertIsNone(game['japanese_title'])
                self.assertEqual(game['match_status'], 'review')
                self.assertEqual({c['japanese_title'] for c in db.get_candidates(game['id'])},
                                 {'名前A', '名前B', '名前C'})
                self.assertEqual(len(db.title_decisions(game['id'])), 3)
            finally:
                db.close()

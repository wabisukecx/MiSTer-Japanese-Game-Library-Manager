import contextlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch, Mock
from pathlib import Path
from main import main
from database.store import Store


class CLITests(unittest.TestCase):
    def test_api_download_records_candidate_and_protects_selected_artwork(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbpath = str(Path(tmp) / 'library.sqlite3')
            db = Store(dbpath)
            game_id = db.upsert_game('/games/NES/Dr. Mario.nes')
            db.confirm(game_id, '手動確定タイトル')
            db.save_artwork(game_id, 'manual', 'JP', 'local:existing', 'existing.png', 'old-hash')
            db.close()
            api = Mock()
            api.download.return_value = dict(source='screenscraper', region='JP',
                url='https://api.screenscraper.fr/api2/mediaJeu.php?jeuid=1340',
                local_path=str(Path(tmp) / 'new.png'), sha256='new-hash', selected=False,
                transfer_allowed=False)
            def run(*args):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
                        patch('main.screenscraper_credentials'), \
                        patch('main.ScreenScraper', return_value=api):
                    return main(['--db', dbpath, 'screenscraper-download', str(game_id),
                                 '--provider-game-id', '1340', '--no-prompt', *args])
            self.assertEqual(run(), 0)
            db = Store(dbpath)
            self.assertEqual(db.game(game_id)['artwork_path'], 'existing.png')
            self.assertEqual(db.db.execute('SELECT count(*) FROM artworks').fetchone()[0], 2)
            db.close()
            self.assertEqual(run('--select'), 2)
            self.assertEqual(api.download.call_count, 1)
            self.assertEqual(run('--select', '--replace'), 0)
            db = Store(dbpath)
            self.assertEqual(db.game(game_id)['artwork_source'], 'screenscraper')
            self.assertEqual(db.game(game_id)['japanese_title'], '手動確定タイトル')
            self.assertEqual(db.game(game_id)['sync_status'], 'blocked')
            db.close()

    def test_api_missing_credentials_fails_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbpath = str(Path(tmp) / 'db.sqlite3')
            db = Store(dbpath)
            game_id = db.upsert_game('/games/NES/Dr. Mario.nes')
            db.close()
            with patch.dict('os.environ', {}, clear=True), \
                    patch('main.ScreenScraper') as api, \
                    contextlib.redirect_stderr(io.StringIO()):
                result = main(['--db', dbpath, 'screenscraper-search', str(game_id), '--no-prompt'])
            self.assertEqual(result, 2)
            api.assert_not_called()

    def test_three_game_workflow_and_protection(self):
        fixture = Path(__file__).parent / 'fixtures'
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / 'library.sqlite3')
            output = str(Path(tmp) / 'review.json')
            def run(*args):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    return main(['--db', db, *args])
            self.assertEqual(run('import-inventory', str(fixture / 'inventory.json')), 0)
            self.assertEqual(run('import-aliases', str(fixture / 'aliases.json')), 0)
            self.assertEqual(run('match'), 0)
            self.assertEqual(run('confirm', '1', '手動の名前'), 0)
            self.assertEqual(run('match'), 0)
            self.assertEqual(run('preview', '--out', output), 0)
            result = json.loads(Path(output).read_text(encoding='utf-8'))
            self.assertEqual(len(result['games']), 3)
            self.assertEqual(result['games'][0]['japanese_title'], '手動の名前')
            self.assertEqual(result['games'][0]['match_status'], 'confirmed')
            self.assertFalse(result['transfer_allowed'])
            old = Path(output).read_bytes()
            self.assertEqual(run('preview', '--out', output), 2)
            self.assertEqual(Path(output).read_bytes(), old)
            self.assertEqual(run('export'), 2)


if __name__ == '__main__':
    unittest.main()

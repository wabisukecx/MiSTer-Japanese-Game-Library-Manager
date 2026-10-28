from pathlib import Path
import tempfile
import unittest

from database.store import Store
from metadata.approval import approval_plan
from metadata.retronian import match_titles
from test_retronian import record


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Store(Path(self.tmp.name) / 'db')

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_recheck_proposes_changed_title_without_applying_it(self):
        game = self.db.upsert_game('test', '/media/fat/games/SNES/Game (Japan).sfc', 'SNES')
        self.db.confirm(game, '旧名')
        loader = lambda system, root, **kw: ([record(title='新名')], 'download')
        plain = match_titles(self.db, self.db.games(), self.tmp.name, loader=loader)
        self.assertEqual(plain['updates'], [])
        result = match_titles(self.db, self.db.games(), self.tmp.name, loader=loader, recheck_confirmed=True)
        self.assertEqual([(u['current'], u['proposed']) for u in result['updates']], [('旧名', '新名')])
        self.assertEqual(self.db.game(game)['japanese_title'], '旧名')
        update = result['updates'][0]
        self.assertTrue(self.db.update_confirmed_title(game, update['current'], update['proposed']))
        self.assertEqual(self.db.game(game)['japanese_title'], '新名')
        # A title changed after review is not overwritten.
        self.assertFalse(self.db.update_confirmed_title(game, '旧名', '別名'))
        self.assertEqual(self.db.game(game)['japanese_title'], '新名')

    def test_recheck_ignores_matching_or_ambiguous_titles(self):
        game = self.db.upsert_game('test', '/media/fat/games/SNES/Game (Japan).sfc', 'SNES')
        self.db.confirm(game, '同じ')
        same = lambda system, root, **kw: ([record(title='同じ')], 'cache')
        self.assertEqual(match_titles(self.db, self.db.games(), self.tmp.name, loader=same,
                                      recheck_confirmed=True)['updates'], [])
        many = lambda system, root, **kw: ([record(title='甲'), record(title='乙')], 'cache')
        self.assertEqual(match_titles(self.db, self.db.games(), self.tmp.name, loader=many,
                                      recheck_confirmed=True)['updates'], [])

    def test_unconfirm_keeps_candidates_and_allows_rematch(self):
        game = self.db.upsert_game('test', '/media/fat/games/SNES/Game (Japan).sfc', 'SNES')
        self.db.confirm(game, '旧名')
        self.db.unconfirm(game)
        row = self.db.game(game)
        self.assertEqual((row['japanese_title'], row['match_status']), (None, 'unmatched'))
        loader = lambda system, root, **kw: ([record(title='新名')], 'cache')
        match_titles(self.db, self.db.games(), self.tmp.name, loader=loader)
        self.assertEqual(self.db.game(game)['match_status'], 'review')

    def test_delete_aliases_by_source(self):
        self.db.import_aliases([dict(system='NES', alias='a', japanese_title='あ', source='old'),
                                dict(system='NES', alias='b', japanese_title='い', source='keep')])
        self.assertEqual(self.db.delete_aliases('old'), 1)
        self.assertEqual([a['source'] for a in self.db.aliases()], ['keep'])

    def test_reimported_source_makes_rotated_copy_stale_and_newer_image_is_flagged(self):
        game = self.db.upsert_game('test', '/media/fat/games/NES/Game (Japan).nes')
        self.db.save_artwork(game, 'skraper', 'JP', 'file:///a.png', 'a.png', 'h1')
        art = self.db.artworks(game)[0]
        self.db.db.execute('INSERT INTO artworks(game_id,source,region,url,local_path,sha256) VALUES(?,?,?,?,?,?)',
                           (game, 'skraper', 'JP', 'file:///r.png', 'r.png', 'r1'))
        rotated = self.db.db.execute('SELECT id FROM artworks WHERE url=?', ('file:///r.png',)).fetchone()[0]
        self.db.db.execute('INSERT INTO artwork_edits VALUES(?,?,?,?)', (rotated, art['id'], 90, 'h1'))
        self.assertFalse(self.db.artworks(game)[1]['edit_stale'])
        self.db.save_artwork(game, 'skraper', 'JP', 'file:///a.png', 'a.png', 'h2')
        self.assertTrue(self.db.artworks(game)[1]['edit_stale'])
        self.db.save_artwork(game, 'skraper', 'JP', 'file:///n.png', 'n.png', 'n1', select=False)
        self.db.save_artwork(game, 'skraper', 'JP', 'file:///a.png', 'a.png', 'h2')
        self.db.select_artwork(game, art['id'])
        item = self.db.games()[0]
        item['_titles'], item['_artworks'] = [], self.db.artworks(game)
        self.assertIn('新しい画像候補あり', approval_plan([item])[0]['image_note'])


if __name__ == '__main__':
    unittest.main()

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from database.store import Store
from metadata import gamedatabase, retronian, redump
from metadata.approval import approval_plan, apply_approval
from metadata.title_resolution import title_key, group_candidates
from metadata.titles import match_titles


def candidate(text, provider='retronian', kind='rom_name', score=1):
    return dict(japanese_title=text, provider=provider, score=score, match_kind=kind,
                source='https://example.com/' + provider, license=provider + '-license')


class TitleResolutionTests(unittest.TestCase):
    def test_width_and_whitespace_grouping_keeps_all_original_evidence(self):
        raw = [candidate('ゲーム Ａ'), candidate('ｹﾞｰﾑA', 'gamedatabase'), candidate('ゲームA', 'redump')]
        groups = group_candidates(raw)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['japanese_title'], 'ゲーム Ａ')
        self.assertEqual(groups[0]['evidence'], raw)
        self.assertEqual({e['source'] for e in groups[0]['evidence']}, {c['source'] for c in raw})
        self.assertNotEqual(title_key('ゲーム①'), title_key('ゲーム1'))
        self.assertNotEqual(title_key('ゲームⅡ'), title_key('ゲームII'))
        for left, right in [('ゲーム・A', 'ゲームA'), ('ゲーム～副題～', 'ゲーム副題'),
                            ('ゲーム-1', 'ゲーム1'), ('ゲーム', '別のゲーム')]:
            self.assertEqual(len(group_candidates([candidate(left), candidate(right)])), 2)

    def test_hash_ranks_above_filename_without_voting_and_hash_conflicts_stay_visible(self):
        raw = [candidate('多数派', 'redump'), candidate('多数派', 'retronian'),
               candidate('内容一致', 'gamedatabase', 'sha256')]
        groups = group_candidates(raw)
        self.assertEqual(groups[0]['japanese_title'], '内容一致')
        self.assertTrue(groups[0]['recommended'])
        self.assertFalse(groups[1]['recommended'])
        groups = group_candidates([candidate('内容一致A', kind='sha256'),
                                   candidate('内容一致B', 'gamedatabase', 'sha256')])
        self.assertTrue(all(not g['recommended'] for g in groups))

    def test_batch_approval_groups_typography_but_skips_any_real_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Store(Path(tmp) / 'db')
            try:
                ident = db.upsert_game('/media/fat/games/NES/Game.nes')
                def snapshot():
                    game = db.game(ident)
                    game.update(_titles=db.get_candidates(ident), _artworks=[])
                    return game
                db.save_candidates(ident, [candidate('ゲーム Ａ'), candidate('ｹﾞｰﾑA', 'gamedatabase')])
                plan = approval_plan([snapshot()])
                self.assertEqual(plan[0]['title'], 'ゲーム Ａ')
                # A new weak conflict after the preview must also prevent approval.
                db.save_candidates(ident, db.get_candidates(ident) + [candidate('副題付き', 'redump', 'title_name', .8)])
                self.assertEqual(apply_approval(db, plan, images=False)['errors'], 1)
                self.assertIsNone(approval_plan([snapshot()])[0]['title'])
                db.save_candidates(ident, [candidate('ゲーム Ａ'), candidate('ｹﾞｰﾑA', 'gamedatabase')])
                self.assertEqual(apply_approval(db, approval_plan([snapshot()]), images=False)['titles'], 1)
                self.assertEqual(db.game(ident)['japanese_title'], 'ゲーム Ａ')
            finally:
                db.close()

    def test_three_providers_are_scanned_without_modifying_confirmed_spelling(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Store(Path(tmp) / 'db')
            try:
                ident = db.upsert_game('/media/fat/games/Saturn/Game.chd', 'SATURN')
                db.confirm(ident, 'ゲーム Ａ')
                with patch.object(retronian, 'load_catalog', return_value=([], 'cache')) as a, \
                        patch.object(redump, 'load_catalog', return_value=([], 'cache')) as b, \
                        patch.object(gamedatabase, 'load_catalog', return_value=([], 'cache')) as c, \
                        patch.object(retronian, 'title_candidates', return_value=[candidate('ゲームA')]), \
                        patch.object(redump, 'title_candidates', return_value=[candidate('ゲーム Ａ', 'redump')]), \
                        patch.object(gamedatabase, 'title_candidates', return_value=[candidate('ｹﾞｰﾑA', 'gamedatabase')]):
                    result = match_titles(db, db.games(), Path(tmp) / 'retronian', recheck_confirmed=True)
                    self.assertTrue(all(p.called for p in (a, b, c)))
                    self.assertEqual(result['updates'], [])
                    self.assertEqual(db.game(ident)['japanese_title'], 'ゲーム Ａ')
                    self.assertEqual(len(db.get_candidates(ident)), 3)
                    self.assertEqual(len(group_candidates(db.get_candidates(ident))), 1)
                    self.assertTrue(all('fetched_at' in row for row in db.get_candidates(ident)))
                self.assertFalse({'PCFX', '64DD', 'LASERACTIVE'} & set(gamedatabase.PLATFORMS))
            finally:
                db.close()

import base64
import hashlib
from pathlib import Path
import tempfile
import unittest

from database.store import Store
from metadata.approval import approval_plan, apply_approval


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = Store(self.root / 'db.sqlite3')
        self.game = self.db.upsert_game('test', '/games/NES/Game.nes')
        self.db.save_candidates(self.game, [dict(japanese_title='日本語名', score=1, reason='alias')])
        self.image = self.root / 'art.png'
        self.image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='))
        self.digest = hashlib.sha256(self.image.read_bytes()).hexdigest()
        self.db.save_artwork(self.game, 'test', 'JP', self.image.as_uri(), self.image, self.digest, select=False)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def snapshot(self):
        game = self.db.game(self.game)
        game['_titles'] = self.db.get_candidates(self.game)
        game['_artworks'] = self.db.artworks(self.game)
        return game

    def test_single_candidate_batch_accepts_both(self):
        plan = approval_plan([self.snapshot()])
        result = apply_approval(self.db, plan)
        self.assertEqual(result['titles'], 1)
        self.assertEqual(result['images'], 1)
        self.assertEqual(self.db.game(self.game)['japanese_title'], '日本語名')
        self.assertEqual(self.db.game(self.game)['artwork_path'], str(self.image))
        self.assertEqual(self.db.game(self.game)['sync_status'], 'blocked')

    def test_title_name_only_candidate_needs_explicit_opt_in(self):
        self.db.save_candidates(self.game, [dict(japanese_title='日本語名', score=0.8, reason='title', match_kind='title_name')])
        self.assertIsNone(approval_plan([self.snapshot()])[0]['title'])
        self.assertIn('名前一致のみ', approval_plan([self.snapshot()])[0]['title_note'])
        plan = approval_plan([self.snapshot()], include_weak=True)
        self.assertTrue(plan[0]['weak'])
        # An opt-in plan is still rechecked at apply time against the live candidates.
        self.db.save_candidates(self.game, [dict(japanese_title='日本語名', score=0.8, reason='title'),
                                            dict(japanese_title='別表記', score=0.8, reason='title')])
        self.assertEqual(apply_approval(self.db, plan, images=False)['errors'], 1)
        self.db.save_candidates(self.game, [dict(japanese_title='日本語名', score=0.8, reason='title', match_kind='title_name')])
        self.assertEqual(apply_approval(self.db, plan, images=False)['titles'], 1)
        self.assertEqual(self.db.game(self.game)['japanese_title'], '日本語名')
        self.assertEqual(self.db.title_decisions(self.game)[-1]['action'], 'batch_confirmed_weak')

    def test_conflicting_weak_candidates_stay_excluded_even_when_opted_in(self):
        self.db.save_candidates(self.game, [dict(japanese_title='A', score=0.8, reason='t'),
                                            dict(japanese_title='B', score=0.8, reason='t')])
        self.assertIsNone(approval_plan([self.snapshot()], include_weak=True)[0]['title'])

    def test_confirmed_information_is_kept(self):
        self.db.confirm(self.game, '手動確定名')
        self.db.select_artwork(self.game, self.db.artworks(self.game)[0]['id'])
        result = apply_approval(self.db, approval_plan([self.snapshot()]))
        self.assertEqual(result['titles'], 0)
        self.assertEqual(result['images'], 0)
        self.assertEqual(self.db.game(self.game)['japanese_title'], '手動確定名')

    def test_ambiguous_titles_and_independent_images_are_skipped(self):
        self.db.save_candidates(self.game, [dict(japanese_title=t, score=1) for t in ('作品A', '作品B')])
        self.db.save_artwork(self.game, 'other', 'US', 'other:uri', self.image, self.digest, select=False)
        plan = approval_plan([self.snapshot()])
        self.assertIsNone(plan[0]['title'])
        self.assertIsNone(plan[0]['artwork'])

    def test_single_correction_chain_prefers_corrected_image(self):
        parent = self.db.artworks(self.game)[0]
        corrected = self.root / 'corrected.png'
        corrected.write_bytes(self.image.read_bytes())
        new_id = self.db.register_rotated_artwork(self.game, parent['id'], dict(path=str(corrected),
                 sha256=self.digest, original_sha256=self.digest, rotation_degrees=90))
        plan = approval_plan([self.snapshot()])
        self.assertEqual(plan[0]['artwork']['id'], new_id)

    def test_preview_conflict_and_corrupt_image_prevent_partial_row_update(self):
        plan = approval_plan([self.snapshot()])
        self.image.write_bytes(b'changed')
        result = apply_approval(self.db, plan)
        self.assertEqual(result['errors'], 1)
        self.assertIsNone(self.db.game(self.game)['japanese_title'])
        self.image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='))
        self.db.confirm(self.game, '別処理で確定')
        result = apply_approval(self.db, plan)
        self.assertEqual(result['conflicts'], 1)
        self.assertEqual(self.db.game(self.game)['japanese_title'], '別処理で確定')

    def test_cancel_and_title_only(self):
        plan = approval_plan([self.snapshot()])
        self.assertTrue(apply_approval(self.db, plan, cancelled=lambda: True)['cancelled'])
        result = apply_approval(self.db, plan, images=False)
        self.assertEqual(result['titles'], 1)
        self.assertIsNone(self.db.game(self.game)['artwork_path'])


if __name__ == '__main__':
    unittest.main()

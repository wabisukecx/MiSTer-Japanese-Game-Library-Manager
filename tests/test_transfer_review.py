import hashlib
from pathlib import Path
import tempfile
import unittest

from database.store import Store
from workflow import review_transfer_plan, accept_transfer_review
from test_artwork_transfer import PNG


class TransferReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = Store(self.root / 'db')
        self.addCleanup(self.db.close)
        self.games = []
        for name in ('Candidate', 'English'):
            path = self.root / (name + '.nes')
            path.write_bytes(b'rom-' + name.encode())
            self.games.append(self.db.register_local_rom('NES', path))
        self.db.save_candidates(self.games[0], [dict(japanese_title='日本語候補', score=.8, reason='タイトル名一致')])
        self.image = self.root / 'image.png'
        self.image.write_bytes(PNG)
        self.db.save_artwork(self.games[0], 'test', 'JP', 'test:image', self.image,
                             hashlib.sha256(PNG).hexdigest(), select=False)

    def test_preview_is_read_only_and_acceptance_includes_missing_metadata_games(self):
        games = self.db.games()
        review = review_transfer_plan(self.db, games)
        self.assertEqual(review['review_rows'][0]['display_title'], '日本語候補')
        self.assertEqual(review['review_rows'][1]['display_title'], 'English')
        self.assertIsNone(review['review_rows'][1]['image_path'])
        self.assertIsNone(self.db.game(self.games[0])['japanese_title'])
        self.assertIsNone(self.db.game(self.games[0])['artwork_path'])
        accepted = accept_transfer_review(self.db, games, review)
        self.assertEqual(len(accepted['roms']), 2)
        self.assertEqual(len(accepted['images']), 1)
        self.assertEqual(accepted['titles'], {'Candidate': '日本語候補'})
        self.assertIn('/media/fat/games/NES/Candidate.nes', accepted['title_targets'])
        self.assertEqual(self.db.game(self.games[0])['japanese_title'], '日本語候補')
        self.assertIsNone(self.db.game(self.games[1])['japanese_title'])
        self.assertEqual(self.db.title_decisions(self.games[0])[0]['action'], 'batch_confirmed_weak')

    def test_changed_candidates_refuse_the_transfer(self):
        games = self.db.games()
        review = review_transfer_plan(self.db, games)
        self.db.save_candidates(self.games[0], [dict(japanese_title='別候補', score=.8)])
        with self.assertRaisesRegex(ValueError, '転送は開始していません'):
            accept_transfer_review(self.db, games, review)
        self.assertIsNone(self.db.game(self.games[0])['japanese_title'])

    def test_changed_rom_folder_is_rejected_before_saving_review(self):
        games = self.db.games()
        review = review_transfer_plan(self.db, games)
        self.db.set_setting('workflow/NES', dict(rom_folder=str(self.root / 'new-folder')))
        with self.assertRaisesRegex(ValueError, '現在のROMフォルダー外'):
            accept_transfer_review(self.db, games, review)
        self.assertIsNone(self.db.game(self.games[0])['japanese_title'])
        self.assertIsNone(self.db.game(self.games[0])['artwork_path'])

    def test_missing_rom_is_rejected_before_saving_review(self):
        games = self.db.games()
        review = review_transfer_plan(self.db, games)
        (self.root / 'Candidate.nes').unlink()
        with self.assertRaisesRegex(ValueError, 'PC ROMが見つかりません'):
            accept_transfer_review(self.db, games, review)
        self.assertIsNone(self.db.game(self.games[0])['japanese_title'])

    def test_advanced_options_can_skip_metadata_without_confirming_it(self):
        games = self.db.games()
        review = review_transfer_plan(self.db, games)
        accepted = accept_transfer_review(self.db, games, review, titles=False, images=False)
        self.assertEqual(accepted['images'], [])
        self.assertEqual(accepted['titles'], {})
        self.assertEqual(len(accepted['roms']), 2)
        self.assertIsNone(self.db.game(self.games[0])['japanese_title'])
        self.assertIsNone(self.db.game(self.games[0])['artwork_path'])

    def test_conflicting_names_are_not_chosen_and_do_not_prevent_rom_transfer(self):
        self.db.save_candidates(self.games[0], [dict(japanese_title=t, score=1) for t in ('名前A', '名前B')])
        review = review_transfer_plan(self.db, self.db.games())
        self.assertEqual(review['titles'], {})
        self.assertEqual(review['review_rows'][0]['display_title'], 'Candidate')
        accepted = accept_transfer_review(self.db, self.db.games(), review)
        self.assertEqual(len(accepted['roms']), 2)
        self.assertIsNone(self.db.game(self.games[0])['japanese_title'])

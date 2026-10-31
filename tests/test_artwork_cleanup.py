from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from database.store import Store


class ArtworkCleanupTests(unittest.TestCase):
    def test_missing_original_detaches_edit_and_keeps_valid_selected_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Store(root / 'db')
            try:
                game = db.upsert_game('/media/fat/games/NES/Game.nes')
                original, edited = root / 'original.png', root / 'edited.png'
                original.write_bytes(b'original')
                edited.write_bytes(b'edited')
                db.save_artwork(game, 'test', 'JP', original.as_uri(), original, 'original-sha')
                source_id = db.artworks(game)[0]['id']
                edit_id = db.register_rotated_artwork(game, source_id, dict(
                    path=str(edited), sha256='edited-sha', rotation_degrees=90, original_sha256='original-sha'))
                db.select_artwork(game, edit_id)
                original.unlink()
                self.assertEqual(db.prune_missing_artworks(), dict(removed=1, cleared=0))
                self.assertEqual(db.game(game)['artwork_path'], str(edited))
                self.assertEqual([a['id'] for a in db.artworks(game)], [edit_id])
                self.assertEqual(db.db.execute('PRAGMA foreign_key_check').fetchall(), [])
                edited.unlink()
                self.assertEqual(db.prune_missing_artworks(), dict(removed=1, cleared=1))
                self.assertIsNone(db.game(game)['artwork_path'])
                self.assertIsNone(db.game(game)['artwork_source'])
            finally:
                db.close()

    def test_unreadable_is_kept_and_cancellation_does_not_remove_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Store(root / 'db')
            try:
                game = db.upsert_game('/media/fat/games/NES/Game.nes')
                missing = root / 'missing.png'
                db.save_artwork(game, 'test', 'JP', missing.as_uri(), missing, 'sha')
                with patch.object(Path, 'stat', side_effect=PermissionError()):
                    self.assertEqual(db.prune_missing_artworks(), dict(removed=0, cleared=0))
                with self.assertRaises(InterruptedError):
                    db.prune_missing_artworks(cancelled=lambda: True)
                self.assertEqual(len(db.artworks(game)), 1)
            finally:
                db.close()

import base64
import tempfile
import unittest
from pathlib import Path

from database.store import Store
from artwork.skraper_import import plan_import, apply_import

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'images'
        self.root.mkdir()
        self.db = Store(Path(self.tmp.name) / 'db.sqlite3')
        self.game = self.db.upsert_game('device', '/games/NES/Game (Japan) (Rev 1).nes')

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_preview_and_import_keep_manual_selection_and_title(self):
        image = self.root / 'Game (Japan) (Rev 1).png'
        image.write_bytes(PNG)
        self.db.confirm(self.game, '手動タイトル')
        self.db.save_artwork(self.game, 'manual', 'JP', 'local:old', 'old.png', 'hash')
        plan = plan_import(self.root, self.db.games(device='device'))
        self.assertEqual(plan['ready_count'], 1)
        self.assertEqual(len(self.db.artworks(self.game)), 1)
        result = apply_import(self.db, plan, region='UNKNOWN')
        self.assertEqual(result['imported'], 1)
        self.assertEqual(self.db.game(self.game)['japanese_title'], '手動タイトル')
        self.assertEqual(self.db.game(self.game)['artwork_path'], 'old.png')
        new = self.db.artworks(self.game)[-1]
        self.assertEqual(new['region'], 'UNKNOWN')
        self.assertEqual(image.read_bytes(), PNG)
        apply_import(self.db, plan, region='UNKNOWN')
        self.assertEqual(len(self.db.artworks(self.game)), 2)

    def test_region_revision_and_background_do_not_match(self):
        for name in ('Game (Japan).png', 'Game (USA) (Rev 1).png', 'Game (Japan) (Rev 1)-BG.png'):
            (self.root / name).write_bytes(PNG)
        plan = plan_import(self.root, self.db.games())
        self.assertEqual(plan['ready_count'], 0)

    def test_ambiguous_rom_extension_and_duplicate_images_are_rejected(self):
        (self.root / 'Game (Japan) (Rev 1).png').write_bytes(PNG)
        self.db.upsert_game('device', '/games/NES/Game (Japan) (Rev 1).zip')
        self.assertEqual(plan_import(self.root, self.db.games())['ready_count'], 0)

    def test_duplicate_subdirectories_are_not_arbitrarily_chosen(self):
        for folder in ('a', 'b'):
            (self.root / folder).mkdir()
            (self.root / folder / 'Game (Japan) (Rev 1).png').write_bytes(PNG)
        self.assertEqual(plan_import(self.root, self.db.games())['ready_count'], 0)

    def test_changed_image_after_preview_is_rejected(self):
        image = self.root / 'Game (Japan) (Rev 1).png'
        image.write_bytes(PNG)
        plan = plan_import(self.root, self.db.games())
        image.write_bytes(b'changed')
        result = apply_import(self.db, plan)
        self.assertEqual(result['imported'], 0)
        self.assertEqual(result['errors'], 1)
        self.assertEqual(self.db.artworks(self.game), [])

    def test_cancel_leaves_no_partial_entry(self):
        (self.root / 'Game (Japan) (Rev 1).png').write_bytes(PNG)
        plan = plan_import(self.root, self.db.games())
        result = apply_import(self.db, plan, cancelled=lambda: True)
        self.assertTrue(result['cancelled'])
        self.assertEqual(self.db.artworks(self.game), [])


if __name__ == '__main__':
    unittest.main()

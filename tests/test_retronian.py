import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from database.store import Store
from metadata.retronian import load_catalog, title_candidates, match_titles


def record(name='Game (Japan)', title='ゲーム', **rom):
    return dict(id='game', roms=[dict(name=name, **rom)], titles=[
        dict(text=title, lang='ja', script='Kana', source='test', verified=True),
        dict(text='Game', lang='ja', script='Latn')])


class RetronianTests(unittest.TestCase):
    def test_exact_rom_name_native_script_and_ambiguity(self):
        game = dict(system='SNES', rom_filename='Game (Japan).sfc')
        rows = [record(), record(title='別のゲーム')]
        matches = title_candidates(game, rows)
        self.assertEqual([c['japanese_title'] for c in matches], ['ゲーム', '別のゲーム'])
        self.assertTrue(all(c['score'] == 1 and c['license'] == 'CC BY-SA 4.0' for c in matches))
        self.assertTrue(all('/sfc/' in c['source'] for c in matches))

    def test_hash_precedes_conflicting_name_and_normalized_match_is_lower_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'Game (Japan).sfc'
            source.write_bytes(b'rom')
            game = dict(system='SNES', rom_filename=source.name, local_rom_path=str(source))
            rows = [record(title='名前一致'), record(name='Different (Japan)', title='ハッシュ一致',
                sha256=hashlib.sha256(b'rom').hexdigest())]
            self.assertEqual([c['japanese_title'] for c in title_candidates(game, rows)], ['ハッシュ一致'])
            game = dict(system='SNES', rom_filename='Game (USA) (Rev 1).sfc')
            self.assertEqual(title_candidates(game, [record()])[0]['score'], 0.8)

    def test_cache_reuse_offline_fallback_and_invalid_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            def opener(request, **kwargs):
                calls.append(request.full_url)
                return io.BytesIO(json.dumps([record()]).encode())
            rows, mode = load_catalog('NES', tmp, opener=opener)
            self.assertEqual(mode, 'download')
            self.assertEqual(len(rows), 1)
            self.assertEqual(load_catalog('NES', tmp, opener=opener)[1], 'cache')
            self.assertEqual(len(calls), 1)
            def offline(*args, **kwargs):
                raise OSError('offline')
            self.assertEqual(load_catalog('NES', tmp, refresh=True, opener=offline)[1], 'offline-cache')
            (Path(tmp) / 'fc.json').write_text('[]')
            with self.assertRaises(ValueError):
                load_catalog('NES', tmp, opener=offline)

    def test_matching_preserves_manual_candidates_confirmations_and_other_systems(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Store(Path(tmp) / 'db')
            try:
                pending = db.upsert_game('test', '/media/fat/games/NES/Game (Japan).nes')
                confirmed = db.upsert_game('test', '/media/fat/games/SNES/Game (Japan).sfc', 'SNES')
                db.confirm(confirmed, '確定名')
                db.save_candidates(pending, [dict(japanese_title='手動候補', source='manual', score=1)])
                def loader(system, root, **kwargs):
                    return [record(title='ファミコン' if system == 'NES' else 'スーパーファミコン')], 'cache'
                result = match_titles(db, db.games(), tmp, loader=loader)
                self.assertEqual((result['matched'], result['ambiguous'], result['confirmed']), (1, 1, 1))
                self.assertEqual(db.game(confirmed)['japanese_title'], '確定名')
                self.assertEqual([c['japanese_title'] for c in db.get_candidates(pending)], ['手動候補', 'ファミコン'])
                match_titles(db, db.games(), tmp, loader=loader)
                self.assertEqual(len(db.get_candidates(pending)), 2)
                self.assertNotEqual(db.game(pending)['match_status'], 'confirmed')
            finally:
                db.close()

    def test_download_stays_in_the_given_folder_with_license_notice(self):
        with tempfile.TemporaryDirectory() as tmp:
            load_catalog('NES', tmp, opener=lambda request, **kwargs: io.BytesIO(json.dumps([record()]).encode()))
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ['NOTICE.txt', 'fc.json'])
            self.assertIn('CC BY-SA 4.0', (Path(tmp) / 'NOTICE.txt').read_text(encoding='utf-8'))
            self.assertEqual(json.loads((Path(tmp) / 'fc.json').read_text(encoding='utf-8'))['license'], 'CC BY-SA 4.0')

    def test_cancelled_download_does_not_write_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(InterruptedError):
                load_catalog('MD', tmp, cancelled=lambda: True)
            self.assertEqual(list(Path(tmp).iterdir()), [])

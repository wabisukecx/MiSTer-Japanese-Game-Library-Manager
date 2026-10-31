import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from database.store import Store
from local_rom import rom_info
from metadata import gamedatabase, redump
from metadata.titles import match_titles
from systems import SYSTEMS, system_profile, transfer_rom
from workflow import register_folder, batch_plan


PLATFORMS = [
    ('FDS', 'FDS', '.fds', 106), ('SG1000', 'SG1000', '.sg', 109),
    ('SMS', 'SMS', '.sms', 2), ('GG', 'GameGear', '.gg', 21),
    ('S32X', 'S32X', '.32x', 19), ('NGP', 'NGPC', '.ngp', 25),
    ('NGPC', 'NGPC', '.ngc', 82), ('VB', 'VirtualBoy', '.vb', 11),
    ('MCD', 'MegaCD', '.chd', 20),
]


def csv_data(**overrides):
    record = dict(zip(['Title', 'Title (exact)', 'Title screen', 'Title screen (exact)', 'Region', 'SHA256'],
                      ['Game', 'ゲーム', 'Game', '=', 'Japan', hashlib.sha256(b'rom').hexdigest()]))
    record.update(overrides)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=record.keys())
    writer.writeheader()
    writer.writerow(record)
    return output.getvalue().encode('utf-8')


class JapanesePlatformTests(unittest.TestCase):
    def test_native_csv_conversion_hash_priority_and_weak_filename(self):
        rows = gamedatabase.convert_csv(csv_data())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'Renamed.gg'
            path.write_bytes(b'rom')
            matches = gamedatabase.title_candidates(dict(system='GG', rom_filename=path.name,
                local_rom_path=str(path)), rows)
            self.assertEqual(matches[0]['score'], 1)
            self.assertEqual(matches[0]['japanese_title'], 'ゲーム')
            self.assertEqual(matches[0]['provider'], 'gamedatabase')
            self.assertEqual(matches[0]['license'], 'CC BY 4.0')
            self.assertIn('console_sega_gamegear.csv', matches[0]['source'])
        matches = gamedatabase.title_candidates(dict(system='GG', rom_filename='Game (Japan).gg'), rows)
        self.assertEqual(matches[0]['score'], .8)
        fallback = gamedatabase.convert_csv(csv_data(**{'Title (exact)': '=', 'Title screen (exact)': 'ゲーム画面'}))
        self.assertEqual(fallback[0]['titles'][0]['text'], 'ゲーム画面')
        self.assertEqual(gamedatabase.convert_csv(csv_data(Region='USA')), [])
        self.assertEqual(gamedatabase.convert_csv(csv_data(**{'Title (exact)': '=', 'Title screen (exact)': '='})), [])

    def test_csv_cache_notice_offline_fallback_and_cancellation(self):
        with tempfile.TemporaryDirectory() as tmp:
            opener = lambda *args, **kwargs: io.BytesIO(csv_data())
            self.assertEqual(gamedatabase.load_catalog('GG', tmp, opener=opener)[1], 'download')
            self.assertEqual(gamedatabase.load_catalog('GG', tmp, opener=lambda *a, **k: self.fail('download'))[1], 'cache')
            cached = json.loads((Path(tmp) / 'gg.json').read_text(encoding='utf-8'))
            self.assertEqual(cached['license'], 'CC BY 4.0')
            self.assertIn('PigSaint', (Path(tmp) / 'NOTICE.txt').read_text(encoding='utf-8'))
            self.assertEqual(gamedatabase.load_catalog('GG', tmp, refresh=True,
                opener=lambda *a, **k: io.BytesIO(b'invalid'))[1], 'offline-cache')
            with self.assertRaises(InterruptedError):
                gamedatabase.load_catalog('GG', tmp, cancelled=lambda: True)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                gamedatabase.load_catalog('GG', tmp, opener=lambda *a, **k: io.BytesIO(b'invalid'))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_ngp_catalog_rejects_color_only_titles(self):
        color = csv_data(Tags='#compatibility:neogeopocket>color')
        mono = csv_data(Tags='#compatibility:neogeopocket>mono')
        both = csv_data(Tags='#compatibility:neogeopocket>mono>color')
        self.assertEqual(gamedatabase.convert_csv(color, system='NGP'), [])
        self.assertTrue(gamedatabase.convert_csv(color, system='NGPC'))
        self.assertTrue(gamedatabase.convert_csv(mono, system='NGP'))
        self.assertTrue(gamedatabase.convert_csv(both, system='NGP'))
        self.assertTrue(gamedatabase.convert_csv(both, system='NGPC'))

    def test_registration_japanese_candidates_and_transfer_for_each_added_system(self):
        for system, folder, extension, scraper_id in PLATFORMS:
            with self.subTest(system=system), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / ('Game (Japan)' + extension)).write_bytes(b'rom')
                db = Store(root / 'db')
                try:
                    profile = system_profile(system)
                    self.assertEqual(profile.root, '/media/fat/games/' + folder)
                    self.assertEqual(profile.screenscraper_id, scraper_id)
                    self.assertFalse(profile.verified)
                    with patch.object(gamedatabase, 'load_catalog', return_value=(gamedatabase.convert_csv(csv_data()), 'cache')), \
                            patch.object(redump, 'load_catalog', return_value=([dict(dat_name='Game (Japan)',
                                local_name='ゲーム', region='Japan', disc_id='123')], 'cache')):
                        result = register_folder(db, root, 'test', system, profile.root, root / 'metadata' / 'retronian')
                    self.assertEqual(result['count'], 1)
                    self.assertEqual(result['lookup']['matched'], 1)
                    game = db.game(result['ids'][0])
                    candidates = db.get_candidates(game['id'])
                    self.assertEqual(candidates[0]['japanese_title'], 'ゲーム')
                    self.assertNotEqual(game['match_status'], 'confirmed')
                    self.assertEqual(batch_plan(db, [game])['roms'][0]['remote_path'], profile.root + '/Game (Japan)' + extension)
                    with self.assertRaises(ValueError):
                        transfer_rom(dict(system=system, rom_path=profile.root + '/nested/Game' + extension))
                finally:
                    db.close()

    def test_3do_is_unsupported_while_cartridge_zip_hashes_are_supported(self):
        self.assertNotIn('3DO', SYSTEMS)
        self.assertNotIn('3DO', redump.PLATFORMS)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / 'Game.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('Game.chd', b'rom')
            with self.assertRaises(ValueError):
                rom_info(archive, '3DO')
            with self.assertRaises(ValueError):
                transfer_rom(dict(system='3DO', rom_path='/media/fat/games/3DO/Game.zip'))
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('Game.gg', b'rom')
            matches = gamedatabase.title_candidates(dict(system='GG', rom_filename='Renamed.zip',
                local_rom_path=str(archive)), gamedatabase.convert_csv(csv_data()))
            self.assertEqual(matches[0]['score'], 1)

    def test_ngp_color_family_routes_mixed_files_to_the_shared_mister_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('Mono.ngp', 'Color.ngc', 'Other.npc'):
                (root / name).write_bytes(b'rom')
            db = Store(root / 'db')
            try:
                result = register_folder(db, root, 'test', 'NGP', '/media/fat/games/NGPC', root, lookup=False)
                self.assertEqual(result['count'], 3)
                self.assertEqual({g['rom_filename']: g['system'] for g in db.games()},
                                 {'Mono.ngp': 'NGP', 'Color.ngc': 'NGPC', 'Other.npc': 'NGPC'})
            finally:
                db.close()

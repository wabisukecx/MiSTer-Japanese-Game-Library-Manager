"""Registration, matching and transfer boundaries for the additional platforms."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from database.store import Store
from local_rom import rom_info
from metadata.retronian import PLATFORMS, load_catalog
from test_retronian import match_titles
from systems import SYSTEMS, ROM_EXTENSIONS, system_profile, transfer_rom
from workflow import register_folder, batch_plan


ADDITIONS = [('PCECD', 'pce', '.chd'), ('PS1', 'ps1', '.chd'), ('N64', 'n64', '.z64'),
             ('NEOGEO', 'neogeo', '.neo'), ('WS', 'ws', '.ws'), ('WSC', 'wsc', '.wsc')]


class AddedSystemsTests(unittest.TestCase):
    def test_ds_is_not_a_supported_system(self):
        self.assertNotIn('NDS', SYSTEMS)
        self.assertNotIn('NDS', PLATFORMS)
        self.assertNotIn('.nds', ROM_EXTENSIONS)
        with self.assertRaisesRegex(ValueError, '未対応'):
            system_profile('NDS')

    def test_register_match_cache_and_transfer_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            root = Path(tmp)
            db = Store(root / 'db')
            stack.callback(db.close)
            for system, platform, extension in ADDITIONS:
                with self.subTest(system=system):
                    folder = root / system
                    folder.mkdir()
                    (folder / ('Game (Japan)' + extension)).write_bytes(b'rom')
                    profile = system_profile(system)
                    result = register_folder(db, folder, system, profile.root, root / 'cache', lookup=False)
                    self.assertEqual(result['count'], 1)
                    game = db.game(result['ids'][0])
                    self.assertEqual(game['system'], system)
                    rows = [dict(id='game', roms=[dict(name='Game (Japan)')],
                                 titles=[dict(text='ゲーム', lang='ja', script='Kana')])]
                    def opener(request, **kwargs):
                        self.assertTrue(request.full_url.endswith('/' + platform + '.json'))
                        return io.BytesIO(json.dumps(rows).encode())
                    load_catalog(system, root / 'cache', opener=opener)
                    self.assertEqual(match_titles(db, [game], root / 'cache')['matched'], 1)
                    self.assertIn('/games/' + platform + '/', db.get_candidates(game['id'])[0]['source'])
                    plan = batch_plan(db, [game])
                    self.assertEqual(plan['roms'][0]['remote_path'], profile.root + '/' + game['rom_filename'])

    def test_wonderswan_family_is_routed_by_extension(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            root = Path(tmp)
            (root / 'Mono.ws').write_bytes(b'ws')
            with zipfile.ZipFile(root / 'Color.zip', 'w') as archive:
                archive.writestr('Color.wsc', b'wsc')
            db = Store(root / 'db')
            stack.callback(db.close)
            result = register_folder(db, root, 'WS', system_profile('WS').root, root, lookup=False)
            self.assertEqual(result['count'], 2)
            self.assertEqual({g['system'] for g in db.games()}, {'WS', 'WSC'})

    def test_large_zip_limits_and_unsupported_multifile_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for system, platform, extension in ADDITIONS:
                with self.subTest(system=system):
                    source = root / (system + '.zip')
                    with zipfile.ZipFile(source, 'w') as archive:
                        archive.writestr('Game' + extension, b'rom')
                    # Simulate a large advertised size without allocating hundreds of MiB.
                    entry = zipfile.ZipInfo('Game' + extension)
                    entry.file_size = system_profile(system).max_zip_rom_bytes
                    with patch.object(zipfile.ZipFile, 'infolist', return_value=[entry]):
                        self.assertEqual(rom_info(source, system)['archive_member'], entry.filename)
                        entry.file_size += 1
                        with self.assertRaisesRegex(ValueError, 'MiB'):
                            rom_info(source, system)
            for system, extension in [('PCECD', '.cue'), ('PCECD', '.bin'), ('PCECD', '.pce'),
                                      ('PCE', '.chd'), ('PS1', '.cue'), ('PS1', '.bin'), ('N64', '.nds'), ('NEOGEO', '.bin')]:
                with self.assertRaises(ValueError):
                    transfer_rom(dict(system=system, rom_path=system_profile(system).root + '/Game' + extension))
            with zipfile.ZipFile(root / 'Mame.zip', 'w') as archive:
                archive.writestr('game-p1.p1', b'p')
                archive.writestr('game-c1.c1', b'c')
            with self.assertRaises(ValueError):
                rom_info(root / 'Mame.zip', 'NEOGEO')


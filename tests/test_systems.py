import base64
import contextlib
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from artwork.libretro import artwork_url
from artwork.skraper_import import plan_import, apply_import
from consolemode.titles import title_mapping
from database.store import Store
from main import main
from metadata.matcher import candidates
from mister.artwork_transfer import transfer_plan, transfer_images
from mister.ftp_client import FTPReader
from systems import system_profile, transfer_rom
from test_artwork_transfer import FTP, PNG


class SystemsTests(unittest.TestCase):
    def test_default_pc_rom_folder_names_match_mister(self):
        expected = {
            'MD': 'MegaDrive', 'GB': 'GAMEBOY', 'PCE': 'TGFX16',
            'PCECD': 'TGFX16-CD', 'PS1': 'PSX', 'SATURN': 'Saturn',
            'WS': 'WonderSwan', 'WSC': 'WonderSwanColor', 'GG': 'GameGear',
            'MCD': 'MegaCD', 'NGP': 'NGPC', 'NGPC': 'NGPC', 'VB': 'VirtualBoy',
        }
        for system, folder in expected.items():
            with self.subTest(system=system):
                self.assertEqual(system_profile(system).rom_folder_name, folder)
        self.assertEqual(system_profile('FDS').rom_folder_name, 'FDS')
        self.assertEqual(system_profile('SG1000').rom_folder_name, 'SG1000')

    def test_megadrive_roots_extensions_and_provider_catalog(self):
        profile = system_profile('MD')
        self.assertEqual(profile.screenscraper_id, 1)
        self.assertIn('Sega%20-%20Mega%20Drive%20-%20Genesis', artwork_url('Game (Japan)', 'MD'))
        for root in profile.roots:
            for ext in ('.bin', '.md', '.gen'):
                _, rom = transfer_rom(dict(system='MD', rom_path=root + '/Game (Japan)' + ext))
                self.assertEqual(str(rom.parent), root)
                aliases = [dict(system='MD', alias='Game', japanese_title='メガドライブ版', source='test'),
                           dict(system='SNES', alias='Game', japanese_title='スーファミ版', source='test')]
                self.assertEqual([c['japanese_title'] for c in candidates(rom.name, 'MD', aliases)], ['メガドライブ版'])
        for path in ['/media/fat/games/SNES/Game.bin', profile.root + '/Child/Game.md', profile.root + '/Child/Game.zip']:
            with self.assertRaises(ValueError): transfer_rom(dict(system='MD', rom_path=path))

    def test_megadrive_transfer_follows_rom_parent_for_both_roots(self):
        profile = system_profile('MD')
        for rom_root in profile.roots:
            with self.subTest(root=rom_root), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                db = Store(root / 'db')
                game = db.upsert_game(rom_root + '/Game (Japan).md', system='MD')
                image = root / 'image.png'
                image.write_bytes(PNG)
                db.save_artwork(game, 'test', 'JP', image.as_uri(), image, hashlib.sha256(PNG).hexdigest())
                db.confirm(game, 'メガドライブ作品')
                items = transfer_plan(db, db.games())
                expected = rom_root + '/media/Game (Japan).png'
                self.assertEqual(items[0]['remote_path'], expected)
                ftp = FTP()
                alias = '/media/fat/ConsoleMode/themeconfig/alias.txt'
                ftp.files.update({expected: b'md-original', '/media/fat/ConsoleMode/config.ini': ('[GLOBAL]\naliasPath=' + alias).encode(),
                                  '/media/fat/ConsoleMode/fonts/NotoSansCJK-Regular.otf': b'font', alias: b'Other=Keep\n',
                                  '/media/fat/games/SNES/media/Game (Japan).png': b'snes-original'})
                result = transfer_images(items, 'test', 'root', 'secret', root / 'backup', titles=title_mapping(db.games()), ftp_factory=lambda: ftp)
                self.assertEqual((result['transferred'], result['titles_transferred']), (1, 1))
                self.assertEqual(ftp.files[expected], PNG)
                self.assertEqual(ftp.files['/media/fat/games/SNES/media/Game (Japan).png'], b'snes-original')
                self.assertEqual(ftp.logins, 1)
                db.close()

    def test_mixed_megadrive_roots_refuse_batch_before_connecting(self):
        from unittest.mock import Mock
        factory = Mock()
        items = [dict(remote_path=r + '/media/Game.png') for r in system_profile('MD').roots]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                transfer_images(items, 'test', 'root', 'secret', tmp, ftp_factory=factory)
        factory.assert_not_called()

    def test_megadrive_api_search_passes_system_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'db'
            db = Store(path)
            game = db.upsert_game('/media/fat/games/Genesis/Game.gen', system='MD')
            db.close()
            api = MagicMock()
            api.search.return_value = []
            with patch('main.screenscraper_credentials'), patch('main.ScreenScraper', return_value=api), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['--db', str(path), 'screenscraper-search', str(game), '--no-prompt']), 0)
            api.search.assert_called_once_with('game', system_id=1, refresh=False)

    def test_utf8_alias_file_survives_cli_import_and_matching(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Store(root / 'db')
            game = db.upsert_game('/media/fat/games/SNES/Star Fox (Japan).sfc', system='SNES')
            db.close()
            source = root / 'aliases.json'
            source.write_text(json.dumps([dict(system='SNES', alias='Star Fox (Japan)',
                japanese_title='スターフォックス', source='test')], ensure_ascii=False), encoding='utf-8')
            for command in [['import-aliases', str(source)], ['match']]:
                result = subprocess.run([sys.executable, '-m', 'main', '--db', str(root / 'db'), *command],
                                        capture_output=True, encoding='utf-8')
                self.assertEqual(result.returncode, 0, result.stderr)
            db = Store(root / 'db')
            title = db.get_candidates(game)[0]['japanese_title']
            self.assertEqual(title, 'スターフォックス')
            self.assertNotIn('?', title)
            db.close()

    def test_existing_database_gets_new_system_without_changing_nes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'db'
            db = Store(path)
            old_id = db.upsert_game('/media/fat/games/NES/Game.nes')
            db.confirm(old_id, 'ファミコン版')
            db.db.execute("DELETE FROM systems WHERE id='SNES'")
            db.db.commit()
            db.close()
            db = Store(path)
            new_id = db.upsert_game('/media/fat/games/SNES/Game.sfc', system='SNES')
            self.assertNotEqual(old_id, new_id)
            self.assertEqual(db.game(old_id)['japanese_title'], 'ファミコン版')
            self.assertEqual(db.game(new_id)['system'], 'SNES')
            db.close()

    def test_matching_and_libretro_are_scoped_by_system(self):
        aliases = [dict(system=s, alias='Game', japanese_title=name, source='test')
                   for s, name in [('NES', 'ファミコン版'), ('SNES', 'スーファミ版')]]
        result = candidates('Game (Japan).sfc', 'SNES', aliases)
        self.assertEqual([r['japanese_title'] for r in result], ['スーファミ版'])
        self.assertIn('Super%20Nintendo%20Entertainment%20System', artwork_url('Game', 'SNES'))
        self.assertNotIn('Super%20Nintendo', artwork_url('Game', 'NES'))

    def test_scan_uses_snes_extensions_and_does_not_read_roms(self):
        ftp = MagicMock()
        ftp.mlsd.return_value = [('Game.sfc', {'type': 'file'}), ('Other.SMC', {'type': 'file'}),
                                 ('NES.nes', {'type': 'file'}), ('Archive.zip', {'type': 'file'})]
        with FTPReader('host', 'user', 'secret', ftp_factory=lambda: ftp) as reader:
            result = reader.scan(system_profile('SNES').root, extensions=system_profile('SNES').extensions)
        self.assertEqual(len(result['roms']), 3)
        self.assertTrue(all(not r.endswith('.nes') for r in result['roms']))
        ftp.retrbinary.assert_not_called()

    def test_snes_artwork_and_title_transfer_uses_only_snes_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Store(root / 'db')
            game_id = db.upsert_game('/media/fat/games/SNES/Game (Japan).sfc', system='SNES')
            db.confirm(game_id, 'スーファミ作品')
            image = root / 'image.png'
            image.write_bytes(PNG)
            db.save_artwork(game_id, 'test', 'JP', image.as_uri(), image, hashlib.sha256(PNG).hexdigest())
            items = transfer_plan(db, db.games())
            mapping = title_mapping(db.games())
            media = system_profile('SNES').media
            ftp = FTP()
            alias = '/media/fat/ConsoleMode/themeconfig/alias.txt'
            ftp.files.update({media + '/Game (Japan).png': b'snes-original',
                              '/media/fat/ConsoleMode/config.ini': ('[GLOBAL]\naliasPath=' + alias).encode(),
                              '/media/fat/ConsoleMode/fonts/NotoSansCJK-Regular.otf': b'font',
                              alias: b'Other=Keep\n'})
            result = transfer_images(items, 'test', 'root', 'secret', root / 'backups', titles=mapping,
                                     ftp_factory=lambda: ftp)
            self.assertEqual((result['transferred'], result['titles_transferred']), (1, 1))
            self.assertEqual(ftp.files[media + '/Game (Japan).png'], PNG)
            self.assertEqual(ftp.files['/media/fat/games/NES/media/Game.png'], b'original')
            record = json.loads(Path(result['report']).read_text(encoding='utf-8'))
            self.assertEqual(record['media'], media)
            self.assertTrue(all('/NES/' not in str(w) for w in ftp.writes))
            db.close()

    def test_cross_system_alias_collision_is_rejected(self):
        nes = dict(device='home', system='NES', rom_path='/media/fat/games/NES/Game.nes',
                   match_status='confirmed', japanese_title='ファミコン版')
        snes = dict(device='home', system='SNES', rom_path='/media/fat/games/SNES/Game.sfc',
                    match_status='confirmed', japanese_title='スーファミ版')
        with self.assertRaises(ValueError): title_mapping([snes], all_games=[nes, snes])
        with self.assertRaises(ValueError):
            title_mapping([snes], all_games=[dict(nes, device='other'), snes])

    def test_import_inventory_and_api_select_snes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            inventory = path / 'scan.json'
            inventory.write_text(json.dumps(dict(device='home', system='SNES', roms=['/media/fat/games/SNES/Game.smc'])))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['--db', str(path / 'db'), 'import-inventory', str(inventory)]), 0)
            db = Store(path / 'db')
            game = db.games()[0]
            self.assertEqual(game['system'], 'SNES')
            db.close()
            api = MagicMock()
            api.search.return_value = []
            with patch('main.screenscraper_credentials'), patch('main.ScreenScraper', return_value=api), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['--db', str(path / 'db'), 'screenscraper-search', str(game['id']), '--no-prompt']), 0)
            api.search.assert_called_once_with('game', system_id=4, refresh=False)

    def test_skraper_snes_import_and_cross_system_ambiguity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / 'Game.png'
            image.write_bytes(PNG)
            db = Store(root / 'db')
            snes = db.upsert_game('/media/fat/games/SNES/Game.sfc', system='SNES')
            plan = plan_import(root, db.games())
            self.assertEqual(apply_import(db, plan)['imported'], 1)
            db.upsert_game('/media/fat/games/NES/Game.nes')
            self.assertEqual(plan_import(root, db.games())['ready_count'], 0)
            db.close()


class GameBoyAdvanceTests(unittest.TestCase):
    def test_gba_rom_and_zip_go_to_the_gba_folder_only(self):
        profile = system_profile('GBA')
        self.assertEqual((profile.root, profile.screenscraper_id, profile.siblings), ('/media/fat/games/GBA', 12, ()))
        for name in ('Game.gba', 'Game.zip'):
            _, rom = transfer_rom(dict(system='GBA', rom_path='/media/fat/games/GBA/' + name))
            self.assertEqual(str(rom.parent), '/media/fat/games/GBA')
        for path in ('/media/fat/games/GBA/Game.gb', '/media/fat/games/GBA/Sub/Game.gba', '/media/fat/games/GBC/Game.gba'):
            with self.assertRaises(ValueError):
                transfer_rom(dict(system='GBA', rom_path=path))


class PCEngineTests(unittest.TestCase):
    def test_pce_hucards_go_to_the_tgfx16_folder_only(self):
        profile = system_profile('PCE')
        self.assertEqual((profile.root, profile.transfer_extensions), ('/media/fat/games/TGFX16', ('.pce', '.sgx')))
        for name in ('Game.pce', 'Game.sgx', 'Game.zip'):
            _, rom = transfer_rom(dict(system='PCE', rom_path='/media/fat/games/TGFX16/' + name))
            self.assertEqual(str(rom.parent), '/media/fat/games/TGFX16')
        for path in ('/media/fat/games/TGFX16-CD/Game.pce', '/media/fat/games/TGFX16/Game.cue', '/media/fat/games/TGFX16/S/Game.pce'):
            with self.assertRaises(ValueError):
                transfer_rom(dict(system='PCE', rom_path=path))

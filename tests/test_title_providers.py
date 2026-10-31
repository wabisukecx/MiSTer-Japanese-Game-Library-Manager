import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from database.store import Store
from metadata import redump, retronian, gamedatabase
from metadata.titles import match_titles
from metadata.approval import approval_plan
from artwork.libretro import artwork_url
from artwork.skraper_config import system_paths
from systems import system_profile, transfer_rom
from workflow import register_folder, batch_plan


def local_name(title='ゲーム', name='Game (Japan) (Disc 1)', **kwargs):
    return dict(dat_name=name, local_name=title, disc_id='1426', region='Japan', **kwargs)


def native(title='ゲーム'):
    return dict(id='game', roms=[dict(name='Game (Japan) (Disc 1)')],
                titles=[dict(text=title, lang='ja', script='Jpan', source='test')])


class TitleProviderTests(unittest.TestCase):
    def setUp(self):
        extra = patch.object(gamedatabase, 'load_catalog', return_value=([], 'cache'))
        extra.start()
        self.addCleanup(extra.stop)

    def test_redump_cache_provenance_offline_and_malformed_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            opener = lambda *args, **kwargs: io.BytesIO(json.dumps(dict(data=[local_name()], dat_version='test')).encode())
            rows, mode = redump.load_catalog('SATURN', tmp, opener=opener)
            self.assertEqual(mode, 'download')
            self.assertEqual(redump.load_catalog('SATURN', tmp, opener=lambda *a, **k: self.fail('unexpected download'))[1], 'cache')
            cache = json.loads((Path(tmp) / 'saturn.json').read_text(encoding='utf-8'))
            self.assertEqual(cache['source'], redump.SOURCE)
            self.assertEqual(cache['dat_version'], 'test')
            self.assertIn('redump.info/about', cache['license_url'])
            self.assertIn('third-party', (Path(tmp) / 'NOTICE.txt').read_text(encoding='utf-8'))
            self.assertEqual(redump.load_catalog('SATURN', tmp, refresh=True,
                opener=lambda *a, **k: io.BytesIO(b'{}'))[1], 'offline-cache')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                redump.load_catalog('SATURN', tmp, opener=lambda *a, **k: io.BytesIO(b'{}'))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_cancelled_read_never_falls_back_to_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            def opener(*args, **kwargs):
                raise InterruptedError('cancel')
            with self.assertRaises(InterruptedError):
                redump.load_catalog('SATURN', tmp, opener=opener)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_redump_exact_weak_and_non_japanese_names(self):
        rows = [local_name(), local_name(title='別のゲーム')]
        rows += [dict(dat_name='Game (Japan) (Disc 1)', local_name='Game', disc_id='2', region='USA')]
        game = dict(system='SATURN', rom_filename='Game (Japan) (Disc 1).chd')
        candidates = redump.title_candidates(game, rows)
        self.assertEqual(len(candidates), 2)
        self.assertTrue(all(c['score'] == 1 and c['provider'] == 'redump' for c in candidates))
        self.assertTrue(all(c['source'].startswith('https://redump.info/disc/') for c in candidates))
        game['rom_filename'] = 'Game (Japan) (Rev A).chd'
        self.assertTrue(all(c['score'] == .8 for c in redump.title_candidates(game, rows)))

    def test_export_unicode_escapes_are_decoded_without_corrupting_japanese(self):
        game = dict(system='SATURN', rom_filename='Game (Japan) (Disc 1).chd')
        result = redump.title_candidates(game, [local_name(title=r'ルナ２\u3000～エターナル・ブルー～')])
        self.assertEqual(result[0]['japanese_title'], 'ルナ２　～エターナル・ブルー～')
        self.assertEqual(redump.title_candidates(game, [local_name(title=r'ゲーム\u000a')]), [])

    def test_coexistence_conflicts_rechecks_and_failed_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Store(Path(tmp) / 'db')
            try:
                game_id = db.upsert_game('/media/fat/games/Saturn/Game (Japan) (Disc 1).chd', 'SATURN')
                db.save_candidates(game_id, [dict(japanese_title='手動候補', source='manual', score=.8)])
                with patch.object(retronian, 'load_catalog', return_value=([native()], 'cache')), \
                        patch.object(redump, 'load_catalog', return_value=([local_name(title='別のゲーム')], 'cache')):
                    result = match_titles(db, db.games(), Path(tmp) / 'retronian')
                    self.assertEqual((result['matched'], result['ambiguous']), (1, 1))
                    match_titles(db, db.games(), Path(tmp) / 'retronian')
                    self.assertEqual(len(db.get_candidates(game_id)), 3)
                    game = db.game(game_id)
                    game.update(_titles=db.get_candidates(game_id), _artworks=[])
                    self.assertIsNone(approval_plan([game])[0]['title'])
                    db.confirm(game_id, '確定名')
                    result = match_titles(db, db.games(), Path(tmp) / 'retronian', recheck_confirmed=True)
                    self.assertEqual(result['updates'], [])
                    self.assertEqual(db.game(game_id)['japanese_title'], '確定名')
                with patch.object(retronian, 'load_catalog', return_value=([native(title='更新候補')], 'cache')), \
                        patch.object(redump, 'load_catalog', side_effect=ValueError('offline')):
                    result = match_titles(db, db.games(), Path(tmp) / 'retronian', recheck_confirmed=True)
                    self.assertTrue(result['warnings'])
                    self.assertEqual(result['updates'], [])
                    titles = {c['japanese_title'] for c in db.get_candidates(game_id)}
                    self.assertEqual(titles, {'手動候補', '更新候補', '別のゲーム'})
                    self.assertEqual(db.game(game_id)['japanese_title'], '確定名')
                db.save_candidates(game_id, [])
                with patch.object(retronian, 'load_catalog', return_value=([native(title='更新候補')], 'cache')), \
                        patch.object(redump, 'load_catalog', return_value=([local_name(title='更新候補')], 'cache')):
                    result = match_titles(db, db.games(), Path(tmp) / 'retronian', recheck_confirmed=True)
                    self.assertEqual(result['updates'][0]['proposed'], '更新候補')
                    self.assertEqual(db.game(game_id)['japanese_title'], '確定名')
            finally:
                db.close()

    def test_saturn_chd_registration_and_transfer_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'Game.chd').write_bytes(b'chd')
            db = Store(root / 'db')
            try:
                profile = system_profile('SATURN')
                self.assertFalse(profile.verified)
                result = register_folder(db, root, 'SATURN', profile.root, root / 'cache', lookup=False)
                game = db.game(result['ids'][0])
                self.assertEqual(batch_plan(db, [game])['roms'][0]['remote_path'], '/media/fat/games/Saturn/Game.chd')
                with self.assertRaises(ValueError):
                    transfer_rom(dict(system='SATURN', rom_path=profile.root + '/Game.cue'))
                with self.assertRaises(ValueError):
                    transfer_rom(dict(system='SATURN', rom_path=profile.root + '/nested/Game.chd'))
            finally:
                db.close()

    def test_pcecd_registration_matching_and_separate_media_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'Game (Japan) (Disc 1).chd').write_bytes(b'chd')
            (root / 'HuCard.pce').write_bytes(b'card')
            db = Store(root / 'db')
            try:
                profile = system_profile('PCECD')
                self.assertEqual(profile.root, '/media/fat/games/TGFX16-CD')
                self.assertFalse(profile.verified)
                with patch.object(retronian, 'load_catalog', return_value=([native()], 'cache')) as retro, \
                        patch.object(redump, 'load_catalog', return_value=([local_name()], 'cache')) as red:
                    result = register_folder(db, root, 'PCECD', profile.root,
                                             root / 'metadata' / 'retronian')
                    self.assertEqual(result['count'], 1)
                    self.assertEqual(retro.call_args.args[0], 'PCECD')
                    self.assertEqual(red.call_args.args[0], 'PCECD')
                game = db.game(result['ids'][0])
                self.assertEqual({c['provider'] for c in db.get_candidates(game['id'])}, {'retronian', 'redump'})
                self.assertEqual(game['match_status'], 'review')
                plan = batch_plan(db, [game])
                self.assertEqual(plan['roms'][0]['remote_path'], profile.root + '/Game (Japan) (Disc 1).chd')
                self.assertNotEqual(profile.media, system_profile('PCE').media)
                self.assertIn('PC%20Engine%20CD', artwork_url('Game', 'PCECD'))
                for path in (profile.root + '/Game.cue', profile.root + '/Game.pce',
                             system_profile('PCE').root + '/Game.chd', profile.root + '/nested/Game.chd'):
                    with self.assertRaises(ValueError):
                        transfer_rom(dict(system='PCECD', rom_path=path))
                config = dict(Systems=[dict(SystemId=114, GamesAndRoms=dict(Path=str(root)),
                    Medias=[dict(Type=0, OutputFolder='%ROMROOTFOLDER%/media')])])
                (root / 'skraper.config.json').write_text(json.dumps(config), encoding='utf-8')
                self.assertEqual(system_paths(root, profile.screenscraper_id)['rom_folder'], str(root))
            finally:
                db.close()

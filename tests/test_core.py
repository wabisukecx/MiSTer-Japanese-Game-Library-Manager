import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from metadata.matcher import normalize, regions, candidates
from database.store import Store
from mister.ftp_client import FTPReader, ConnectionFailure
from consolemode.export import review_manifest, export_consolemode, SpecificationUnverified
from artwork.cache import ImageCache, fetch_image
from artwork.libretro import artwork_url, prioritize


class MetadataTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize('Ｓｕｐｅｒ Mario Bros. (Japan) [!].nes'), 'super mario bros')

    def test_regions(self):
        self.assertEqual(regions('Mario (Japan, USA) (Rev 1).nes'), ['JP', 'US'])
        self.assertEqual(regions('Japan Adventure.nes'), [])
        self.assertEqual(regions('Mario (J).zip'), ['JP'])

    def test_cross_system_and_sequel(self):
        entries = [dict(system='SNES', alias='Mario', japanese_title='別機種'),
                   dict(system='NES', alias='Mario 2', japanese_title='続編')]
        self.assertEqual(candidates('Mario (Japan).nes', 'NES', entries), [])

    def test_ambiguous_titles_are_all_retained(self):
        entries = [dict(system='NES', alias='Mario', japanese_title=t) for t in ['マリオ', '別作品']]
        result = candidates('Mario.nes', 'NES', entries)
        self.assertEqual(len(result), 2)
        self.assertTrue(all(x['reason'] for x in result))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Store(Path(self.tmp.name) / 'db.sqlite3')

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_game_review_saves_title_and_image_together_and_rolls_back_failure(self):
        game = self.db.upsert_game('/media/fat/games/NES/Game.nes')
        image = Path(self.tmp.name) / 'image.png'
        self.db.save_artwork(game, 'test', 'JP', 'test:image', image, 'a' * 64, select=False)
        artwork = self.db.artworks(game)[0]['id']
        with self.assertRaises(ValueError):
            self.db.save_game_review(game, '不正\nタイトル', artwork)
        self.assertIsNone(self.db.game(game)['artwork_path'])
        self.assertIsNone(self.db.game(game)['japanese_title'])
        self.assertEqual(self.db.title_decisions(game), [])
        self.db.save_game_review(game, '日本語名', artwork)
        self.assertEqual(self.db.game(game)['artwork_path'], str(image))
        self.assertEqual(self.db.game(game)['japanese_title'], '日本語名')
        self.assertEqual(len(self.db.title_decisions(game)), 1)
        self.db.save_game_review(game, '日本語名', artwork)
        self.assertEqual(len(self.db.title_decisions(game)), 1)

    def test_game_review_allows_title_only_or_image_only(self):
        title_game = self.db.upsert_game('/media/fat/games/NES/Title.nes')
        self.db.save_game_review(title_game, '名前のみ')
        self.assertEqual(self.db.game(title_game)['japanese_title'], '名前のみ')
        image_game = self.db.upsert_game('/media/fat/games/NES/Image.nes')
        self.db.save_artwork(image_game, 'test', 'JP', 'test:image', 'image.png', 'a' * 64, select=False)
        self.db.save_game_review(image_game, '', self.db.artworks(image_game)[0]['id'])
        self.assertIsNone(self.db.game(image_game)['japanese_title'])
        self.assertEqual(self.db.game(image_game)['artwork_path'], 'image.png')

    def test_manual_title_survives_rescan_and_candidates(self):
        game = self.db.upsert_game('/media/fat/games/NES/Mario.nes')
        self.db.confirm(game, 'マリオ')
        self.db.upsert_game('/media/fat/games/NES/Mario.nes')
        self.db.save_candidates(game, [dict(japanese_title='誤り', score=1, reason='alias')])
        row = self.db.games()[0]
        self.assertEqual(row['japanese_title'], 'マリオ')
        self.assertEqual(row['match_status'], 'confirmed')

    def test_repeated_registration_is_deduplicated_and_revisions_are_separate(self):
        for name in ['X (Japan).nes', 'X (Japan) (Rev 1).nes', 'X (Japan).nes']:
            self.db.upsert_game('/games/NES/' + name)
        self.assertEqual(len(self.db.games()), 2)

    def test_unknown_id_rejected(self):
        with self.assertRaises(ValueError):
            self.db.confirm(99, '不明')

    def test_japanese_round_trip_and_pending_filter(self):
        game = self.db.upsert_game('/games/NES/日本語 (Japan).nes')
        self.db.confirm(game, '日本語タイトル')
        self.assertEqual(self.db.games()[0]['japanese_title'], '日本語タイトル')
        self.assertEqual(self.db.games(pending=True), [])


class FTPTests(unittest.TestCase):
    def make_reader(self, rows):
        ftp = Mock()
        ftp.mlsd.return_value = rows
        return FTPReader('192.0.2.1', 'root', 'secret', ftp_factory=lambda: ftp), ftp

    def test_scan_never_downloads_rom(self):
        reader, ftp = self.make_reader([('Mario.nes', {'type': 'file'}), ('names.txt', {'type': 'file'}),
                                        ('media', {'type': 'dir'}), ('link', {'type': 'OS.unix=slink'})])
        with reader:
            result = reader.scan('/media/fat/games/NES')
        self.assertEqual(result['roms'], ['/media/fat/games/NES/Mario.nes'])
        self.assertIn('/media/fat/games/NES/names.txt', result['existing_names'])
        self.assertIn('/media/fat/games/NES/media', result['media_directories'])
        ftp.retrbinary.assert_not_called()
        ftp.storbinary.assert_not_called()

    def test_credentials_redacted(self):
        reader, ftp = self.make_reader([])
        ftp.login.side_effect = OSError('secret root password')
        with self.assertRaises(ConnectionFailure) as caught:
            with reader:
                pass
        self.assertNotIn('secret', str(caught.exception))
        ftp.close.assert_called_once()

    def test_permission_failure_not_treated_as_empty(self):
        reader, ftp = self.make_reader([])
        ftp.mlsd.side_effect = OSError('denied')
        with reader:
            with self.assertRaises(ConnectionFailure):
                reader.scan('/games/NES')

    def test_path_traversal_rejected(self):
        reader, ftp = self.make_reader([('../escape.nes', {'type': 'file'})])
        with reader:
            with self.assertRaises(ValueError):
                reader.scan('/games/NES')

    def test_cancel(self):
        reader, _ = self.make_reader([])
        with reader:
            with self.assertRaises(InterruptedError):
                reader.scan('/games/NES', cancelled=lambda: True)

    def test_network_timeout(self):
        reader, ftp = self.make_reader([])
        ftp.connect.side_effect = TimeoutError('network timeout')
        with self.assertRaises(ConnectionFailure):
            with reader:
                pass
        ftp.close.assert_called_once()

    def test_interrupted_scan_closes_connection_and_can_restart(self):
        reader, ftp = self.make_reader([('first.nes', {'type': 'file'})])
        checks = iter([False, True])
        with self.assertRaises(InterruptedError):
            with reader:
                reader.scan('/games/NES', cancelled=lambda: next(checks))
        ftp.close.assert_called_once()
        reader, ftp = self.make_reader([('first.nes', {'type': 'file'})])
        with reader:
            self.assertEqual(len(reader.scan('/games/NES')['roms']), 1)
        ftp.storbinary.assert_not_called()


class ExportTests(unittest.TestCase):
    def test_unverified_export_refused(self):
        with self.assertRaises(SpecificationUnverified):
            export_consolemode([])

    def test_review_is_utf8_and_cannot_be_transferred(self):
        result = review_manifest([dict(rom_path='/games/NES/a.nes', japanese_title='日本語')])
        self.assertFalse(result['transfer_allowed'])
        self.assertEqual(result['files'], [])
        self.assertEqual(json.loads(json.dumps(result, ensure_ascii=False))['games'][0]['japanese_title'], '日本語')


class ArtworkTests(unittest.TestCase):
    def test_url_escaping(self):
        url = artwork_url('A & B (Japan)')
        self.assertIn('A%20_%20B%20%28Japan%29.png', url)

    def test_priority(self):
        values = [dict(source='libretro', region='US'), dict(source='libretro', region='JP'),
                  dict(source='screenscraper', region='JP')]
        self.assertEqual([x['source'] for x in prioritize(values)], ['screenscraper', 'libretro', 'libretro'])

    def test_cache_avoids_download_and_detects_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Small valid 1x1 PNG fixture; no copyrighted artwork.
            import base64
            png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
            fetch = Mock(return_value=png)
            cache = ImageCache(Path(tmp), fetch=fetch)
            url = artwork_url('Example (Japan)')
            first = cache.get(url)
            self.assertEqual(first, cache.get(url))
            self.assertEqual(fetch.call_count, 1)
            first.write_bytes(b'broken')
            cache.get(url)
            self.assertEqual(fetch.call_count, 2)

    def test_non_image_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = ImageCache(Path(tmp), fetch=lambda _: b'<html>error</html>')
            with self.assertRaises(ValueError):
                cache.get(artwork_url('bad'))

    def test_foreign_host_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                ImageCache(Path(tmp)).get('https://example.com/image.png')

    def test_transient_network_retry(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        response.read.return_value = b'payload'
        opener = Mock()
        opener.open.side_effect = [TimeoutError(), response]
        with patch('urllib.request.build_opener', return_value=opener), patch('time.sleep'):
            self.assertEqual(fetch_image(artwork_url('test')), b'payload')
        self.assertEqual(opener.open.call_count, 2)

    def test_rate_limit_not_retried(self):
        from urllib.error import HTTPError
        opener = Mock()
        url = artwork_url('test')
        opener.open.side_effect = HTTPError(url, 429, 'rate limit', {}, None)
        with patch('urllib.request.build_opener', return_value=opener), patch('time.sleep'):
            with self.assertRaises(RuntimeError):
                fetch_image(url)
        self.assertEqual(opener.open.call_count, 1)


if __name__ == '__main__':
    unittest.main()

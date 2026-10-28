import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from consolemode.titles import title_mapping, merge_aliases, inspect_settings, transfer_titles, FONT


class TitleFTP:
    path = '/media/fat/ConsoleMode/themeconfig/alias.txt'
    original = b'Unrelated=Keep\nGame=Original\n'

    def __init__(self):
        self.files = {self.path: self.original}
        self.writes = []
        self.fail_publish = False
    def connect(self, *args, **kwargs): pass
    def login(self, *args): pass
    def close(self): pass
    def mlsd(self, path):
        if path.endswith('/fonts'): return [(Path(FONT).name, {'type': 'file'})]
        return [(Path(k).name, {'type': 'file'}) for k in self.files]
    def retrbinary(self, cmd, callback):
        callback(('[GLOBAL]\naliasPath=' + self.path + '\n').encode() if cmd.endswith('config.ini') else self.files[cmd[5:]])
    def storbinary(self, cmd, stream):
        self.writes.append(cmd)
        self.files[cmd[5:]] = stream.read()
    def rename(self, old, new):
        if self.fail_publish and old.endswith('.tmp'): raise OSError('private server response')
        self.writes.append((old, new))
        self.files[new] = self.files.pop(old)


class TitlesTests(unittest.TestCase):
    def game(self, **changes):
        return dict(dict(system='NES', rom_path='/media/fat/games/NES/Dr. Mario (Japan, USA) (En) (Rev 1).nes',
                         match_status='confirmed', japanese_title='ドクターマリオ'), **changes)

    def test_exact_rom_stem_keeps_region_revision_and_japanese(self):
        self.assertEqual(title_mapping([self.game()]), {'Dr. Mario (Japan, USA) (En) (Rev 1)': 'ドクターマリオ'})
        with self.assertRaises(ValueError): title_mapping([self.game(match_status='review')])
        with self.assertRaises(ValueError): title_mapping([self.game(japanese_title='名前\n別行')])
        with self.assertRaises(ValueError): title_mapping([self.game(rom_path='/elsewhere/Game.nes')])

    def test_merge_preserves_unrelated_lines_and_replaces_duplicate_keys(self):
        original = b'#comment\r\nOther=Another\r\nGame=Old\r\nGame=Last\r\nNoEquals'
        data = merge_aliases(original, {'Game': 'ゲーム', 'New': '新作'})
        self.assertEqual(data.decode('utf-8'), '#comment\r\nOther=Another\r\nGame=ゲーム\nNoEquals\nNew=新作\n')
        self.assertEqual(merge_aliases(data, {'Game': 'ゲーム', 'New': '新作'}), data)

    def test_utf8_bom_and_invalid_encoding_require_review(self):
        with self.assertRaises(ValueError): merge_aliases(b'\xef\xbb\xbfGame=Title', {'Game': '名'})
        with self.assertRaises(UnicodeDecodeError): merge_aliases(b'Game=\xff', {'Game': '名'})

    def test_inspection_exports_preview_without_persisting_scraper_credentials(self):
        class FakeFTP:
            def connect(self, *args, **kw): pass
            def login(self, *args): pass
            def close(self): pass
            def mlsd(self, path):
                if path.endswith('/fonts'): return [('NotoSansCJK-Regular.otf', {'type': 'file'})]
                return [('config.ini', {'type': 'file'}), ('alias.txt', {'type': 'file'}), ('fonts', {'type': 'dir'})]
            def retrbinary(self, cmd, callback):
                callback(b'[GLOBAL]\naliasPath=/media/fat/ConsoleMode/alias.txt\n[SCRAPER]\npassword=very-secret\n' if cmd.endswith('config.ini') else b'Other=Other\n')
        with tempfile.TemporaryDirectory() as tmp:
            result = inspect_settings('host', 'root', 'ftp-secret', {'Game': 'ゲーム'}, tmp, ftp_factory=FakeFTP)
            self.assertEqual(result['status'], 'inspected')
            self.assertTrue(result['font_present'])
            self.assertEqual(result['alias_path'], '/media/fat/ConsoleMode/alias.txt')
            self.assertFalse(result['transfer_allowed'])
            self.assertEqual((Path(tmp) / 'alias-preview.txt').read_bytes(), 'Other=Other\nGame=ゲーム\n'.encode())
            saved = ''.join(p.read_text(encoding='utf-8') for p in Path(tmp).iterdir())
            self.assertNotIn('very-secret', saved)
            self.assertNotIn('ftp-secret', saved)

    def test_failed_login_does_not_disclose_server_response(self):
        class FakeFTP:
            def connect(self, *args, **kwargs): pass
            def login(self, *args): raise OSError('sensitive-password')
            def close(self): pass
        with tempfile.TemporaryDirectory() as tmp:
            result = inspect_settings('host', 'root', 'password', {'Game': 'ゲーム'}, tmp, ftp_factory=FakeFTP)
            self.assertEqual(result['status'], 'failed')
            self.assertNotIn('sensitive-password', json.dumps(result))

    def inspection(self):
        return dict(host='host', status='inspected', alias_path=TitleFTP.path, font_present=True,
                    mappings={'Game': 'ゲーム'}, alias_existed=True,
                    original_sha256=hashlib.sha256(TitleFTP.original).hexdigest())

    def test_transfer_preserves_existing_aliases_and_backups(self):
        ftp = TitleFTP()
        with tempfile.TemporaryDirectory() as tmp:
            result = transfer_titles('host', 'root', 'secret', self.inspection(), tmp, ftp_factory=lambda: ftp)
            report = json.loads(Path(result['report']).read_text(encoding='utf-8'))
            self.assertEqual(result['status'], 'verified')
            self.assertEqual(ftp.files[ftp.path], 'Unrelated=Keep\nGame=ゲーム\n'.encode())
            self.assertEqual(ftp.files[report['remote_backup']], ftp.original)
            self.assertEqual((Path(result['report']).parent / 'alias-original.txt').read_bytes(), ftp.original)
            self.assertNotIn('secret', Path(result['report']).read_text(encoding='utf-8'))

    def test_changed_remote_alias_stops_without_writes(self):
        ftp = TitleFTP()
        ftp.files[ftp.path] = b'Edited=Externally\n'
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                transfer_titles('host', 'root', 'secret', self.inspection(), tmp, ftp_factory=lambda: ftp)
            self.assertEqual(ftp.writes, [])

    def test_failed_publish_restores_original(self):
        ftp = TitleFTP()
        ftp.fail_publish = True
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError) as error:
                transfer_titles('host', 'root', 'secret', self.inspection(), tmp, ftp_factory=lambda: ftp)
            self.assertNotIn('private server response', str(error.exception))
            self.assertEqual(ftp.files[ftp.path], ftp.original)

    def test_different_host_and_missing_font_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for inspection in [dict(self.inspection(), host='other'), dict(self.inspection(), font_present=False)]:
                with self.assertRaises(ValueError):
                    transfer_titles('host', 'root', 'secret', inspection, tmp)

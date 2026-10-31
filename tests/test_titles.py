import unittest

from consolemode.titles import title_mapping, merge_aliases


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

import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from database.store import Store
from main import main
from metadata.approval import approval_plan, apply_approval
from metadata.title_catalog import export_catalog, merge_catalogs, read_catalog, write_catalog


class TitleCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = Store(self.root / 'db')
        self.addCleanup(self.db.close)
        self.rom = self.root / 'Game (Japan).nes'
        self.rom.write_bytes(b'rom')
        self.ident = self.db.register_local_rom('private-device', 'NES', self.rom)
        self.db.save_candidates(self.ident, [dict(japanese_title='ゲーム', score=1, provider='fixture',
            source='https://user:password@example.com/record?token=secret&id=1', license='CC BY 4.0',
            reason='ROM名一致', secret='do-not-export', local_path='private-path')])

    def test_export_history_provenance_and_privacy_without_claiming_a_public_license(self):
        self.db.confirm(self.ident, 'ゲーム')
        self.db.update_confirmed_title(self.ident, 'ゲーム', '確認名')
        self.db.unconfirm(self.ident)
        self.db.confirm(self.ident, '最終名')
        document = export_catalog(self.db, self.db.games())
        entry = document['entries'][0]
        self.assertEqual(entry['confirmed_titles'], ['最終名'])
        self.assertEqual([h['action'] for h in entry['history']], ['confirmed', 'updated', 'unconfirmed', 'confirmed'])
        self.assertTrue(all(h['recorded_at'] for h in entry['history']))
        self.assertEqual(entry['candidates'][0]['source'], 'https://example.com/record?id=1')
        text = json.dumps(document)
        for private in ('private-device', str(self.root), 'do-not-export', 'private-path', 'password', 'token=secret'):
            self.assertNotIn(private, text)
        self.assertIn('CC BY 4.0', document['licenses'])
        self.assertIn('no automatic public distribution license', document['purpose'])
        self.assertEqual(entry['hashes'], {})

    def test_hash_export_and_idempotent_merge_keeps_conflicting_confirmations(self):
        self.db.confirm(self.ident, 'ゲーム')
        first = export_catalog(self.db, self.db.games(), hash_roms=True)
        self.assertEqual(first['entries'][0]['hashes']['sha256'], hashlib.sha256(b'rom').hexdigest())
        second = copy.deepcopy(first)
        second['entries'][0]['rom_names'] = ['Renamed.nes']
        second['entries'][0]['confirmed_titles'] = ['別の名前']
        merged = merge_catalogs([first, second, first])
        entry = merged['entries'][0]
        self.assertEqual(len(merged['entries']), 1)
        self.assertEqual(len(entry['candidates']), 1)
        self.assertEqual(len(entry['history']), 1)
        self.assertEqual(set(entry['rom_names']), {'Game (Japan).nes', 'Renamed.nes'})
        self.assertTrue(entry['confirmation_conflict'])
        self.assertEqual(set(entry['confirmed_titles']), {'ゲーム', '別の名前'})
        self.assertEqual(merge_catalogs([merged, merged])['entries'], merged['entries'])

    def test_identity_never_fuzzy_merges_revisions_systems_or_different_hashes(self):
        first = export_catalog(self.db, self.db.games())
        revision = copy.deepcopy(first)
        revision['entries'][0]['rom_names'] = ['Game (Japan) (Rev 1).nes']
        different_system = copy.deepcopy(first)
        different_system['entries'][0]['system'] = 'SNES'
        hashed = export_catalog(self.db, self.db.games(), hash_roms=True)
        other_hash = copy.deepcopy(hashed)
        other_hash['entries'][0]['hashes']['sha256'] = 'a' * 64
        self.assertEqual(len(merge_catalogs([first, revision, different_system, hashed, other_hash])['entries']), 5)

    def test_zip_hash_is_of_the_inner_rom_and_conflicts_are_not_resolved_by_merge(self):
        zipped = self.root / 'Zipped.zip'
        with zipfile.ZipFile(zipped, 'w') as archive:
            archive.writestr('Inner.nes', b'rom')
        other = self.db.register_local_rom('private-device', 'NES', zipped)
        self.db.confirm(self.ident, 'ゲーム Ａ')
        self.db.confirm(other, 'ｹﾞｰﾑA')
        document = export_catalog(self.db, self.db.games(), hash_roms=True)
        self.assertEqual(len(document['entries']), 1)
        self.assertEqual(len(document['entries'][0]['confirmed_titles']), 2)
        self.assertFalse(document['entries'][0]['confirmation_conflict'])

    def test_new_output_required_and_cancelled_export_does_not_write(self):
        document = export_catalog(self.db, self.db.games())
        path = self.root / 'export.json'
        write_catalog(path, document)
        original = path.read_bytes()
        self.assertEqual(read_catalog(path), document)
        with self.assertRaises(FileExistsError):
            write_catalog(path, document)
        self.assertEqual(path.read_bytes(), original)
        with self.assertRaises(InterruptedError):
            export_catalog(self.db, self.db.games(), cancelled=lambda: True)
        malformed = copy.deepcopy(document)
        malformed['entries'][0]['rom_names'] = ['C:\\private\\Game.nes']
        with self.assertRaises(ValueError):
            merge_catalogs([malformed])

    def test_batch_decision_is_logged_only_after_success_and_legacy_confirmation_has_no_invented_date(self):
        game = self.db.game(self.ident)
        game.update(_titles=self.db.get_candidates(self.ident), _artworks=[])
        result = apply_approval(self.db, approval_plan([game]), images=False)
        self.assertEqual(result['titles'], 1)
        self.assertEqual(self.db.title_decisions(self.ident)[0]['action'], 'batch_confirmed')
        with self.db.db:
            self.db.db.execute('DELETE FROM title_decisions')
        entry = export_catalog(self.db, self.db.games())['entries'][0]
        self.assertEqual(entry['confirmed_titles'], ['ゲーム'])
        self.assertEqual(entry['history'], [])

    def test_decision_history_rolls_back_with_failed_approval(self):
        game = self.db.game(self.ident)
        game.update(_titles=self.db.get_candidates(self.ident), _artworks=[])
        original = self.db.record_title_decision
        def fail(*args):
            original(*args)
            raise ValueError('synthetic failure after history insertion')
        with patch.object(self.db, 'record_title_decision', side_effect=fail):
            result = apply_approval(self.db, approval_plan([game]), images=False)
        self.assertEqual(result['errors'], 1)
        self.assertEqual(self.db.title_decisions(self.ident), [])
        self.assertIsNone(self.db.game(self.ident)['japanese_title'])

    def test_cli_export_filter_and_offline_merge_need_no_library_for_merge(self):
        self.db.confirm(self.ident, 'ゲーム')
        output = self.root / 'cli.json'
        merged = self.root / 'merged.json'
        unused_db = self.root / 'unused.sqlite3'
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['--db', str(self.root / 'db'), 'export-title-db', '--system', 'NES',
                                   '--confirmed-only', '--out', str(output)]), 0)
            self.assertEqual(main(['--db', str(unused_db), 'merge-title-db', str(output), str(output),
                                   '--out', str(merged)]), 0)
            self.assertEqual(main(['--db', str(unused_db), 'merge-title-db', str(output), '--out', str(merged)]), 2)
        self.assertFalse(unused_db.exists())
        self.assertEqual(len(read_catalog(merged)['entries']), 1)

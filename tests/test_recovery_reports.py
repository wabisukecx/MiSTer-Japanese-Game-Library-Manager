import json
from pathlib import Path
import tempfile
import unittest

from mister.recovery import pending_recovery_reports


class RecoveryReportTests(unittest.TestCase):
    def test_only_incomplete_transfers_need_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected = []
            cases = [('verified', 'verified', False), ('rolled_back', 'restored', False),
                     ('rollback_failed', 'restore_failed', True), ('preflight', 'publishing', True),
                     ('preflight', 'backed_up', False), ('preflight', 'staging', True)]
            for index, (status, item_status, pending) in enumerate(cases):
                run_id = '%032x' % index
                report = root / run_id / 'transfer.json'
                report.parent.mkdir()
                report.write_text(json.dumps(dict(schema='mister-jp-transfer/v2', run_id=run_id,
                    host='test', status=status, items=[dict(kind='rom', system='NES',
                    remote_path='/media/fat/games/NES/Game.nes', original_existed=False,
                    sha256='b' * 64, status=item_status)])), encoding='utf-8')
                if pending:
                    expected.append(str(report.resolve()))
            corrupt = root / 'corrupt' / 'transfer.json'
            corrupt.parent.mkdir()
            corrupt.write_text('{', encoding='utf-8')
            self.assertEqual(pending_recovery_reports(root), expected)

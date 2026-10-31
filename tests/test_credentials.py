import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest

from database.credentials import ConnectionStore, KEY, protect, unprotect
from database.store import Store

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
HAS_QT = importlib.util.find_spec('PyQt6') is not None


def fake_protect(data):
    return b'enc:' + data[::-1]


def fake_unprotect(data):
    if not data.startswith(b'enc:'):
        raise ValueError('bad blob')
    return data[4:][::-1]


class ConnectionStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Store(Path(self.tmp.name) / 'db')
        self.addCleanup(self.db.close)
        self.store = ConnectionStore(self.db, protector=fake_protect, unprotector=fake_unprotect)

    def test_defaults_when_nothing_saved(self):
        self.assertEqual(self.store.load(), dict(host='', user='root', password=''))

    def test_remembers_connection_and_never_stores_clear_password(self):
        self.store.save('192.0.2.10', 'root', 'secret-pass', True)
        self.assertEqual(self.store.load(), dict(host='192.0.2.10', user='root', password='secret-pass'))
        raw = self.db.db.execute('SELECT value FROM settings WHERE key=?', (KEY,)).fetchone()[0]
        self.assertNotIn('secret-pass', raw)
        self.assertTrue(self.store.has_password())

    def test_password_is_dropped_when_not_remembered(self):
        self.store.save('192.0.2.10', 'root', 'secret-pass', True)
        self.store.save('192.0.2.10', 'root', 'secret-pass', False)
        self.assertEqual(self.store.load(), dict(host='192.0.2.10', user='root', password=''))
        self.assertFalse(self.store.has_password())

    def test_unreadable_password_falls_back_to_asking_again(self):
        self.store.save('192.0.2.10', 'admin', 'secret-pass', True)
        self.db.set_setting(KEY, dict(host='192.0.2.10', user='admin', password='AAAA'))
        self.assertEqual(self.store.load(), dict(host='192.0.2.10', user='admin', password=''))

    def test_without_dpapi_only_host_and_user_are_remembered(self):
        store = ConnectionStore(self.db, protector=fake_protect, unprotector=fake_unprotect)
        store.can_save_password = False
        store.save('192.0.2.10', 'root', 'secret-pass', True)
        self.assertEqual(store.load(), dict(host='192.0.2.10', user='root', password=''))

    @unittest.skipUnless(sys.platform == 'win32', 'Windows DPAPI')
    def test_windows_dpapi_round_trip(self):
        blob = protect('パスワード1'.encode('utf-8'))
        self.assertNotIn('パスワード1'.encode('utf-8'), blob)
        self.assertEqual(unprotect(blob).decode('utf-8'), 'パスワード1')


@unittest.skipUnless(HAS_QT, 'optional GUI dependency PyQt6 is not installed')
class ConnectionDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_transfer_dialog_prefills_saved_connection(self):
        from gui.connection import commit
        from gui.transfer_dialog import TransferDialog
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'db'
            Store(path).close()
            commit(path, '192.0.2.10', 'admin', 'secret-pass', True)
            dialog = TransferDialog([], {}, roms=[], db_path=path)
            try:
                self.assertEqual(dialog.host.text(), '192.0.2.10')
                self.assertEqual(dialog.user.text(), 'admin')
                if sys.platform == 'win32':
                    self.assertEqual(dialog.password.text(), 'secret-pass')
                    self.assertTrue(dialog.remember.isChecked())
            finally:
                dialog.password.clear()
                dialog.close()

    def test_commit_ignores_blank_host(self):
        from gui.connection import commit
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'db'
            Store(path).close()
            commit(path, '', 'root', 'x', True)
            db = Store(path)
            try:
                self.assertIsNone(db.setting(KEY))
            finally:
                db.close()


if __name__ == '__main__':
    unittest.main()

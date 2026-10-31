"""Shared helpers that prefill and remember the MiSTer FTP connection in the GUI dialogs."""
import sqlite3

from PyQt6.QtWidgets import QCheckBox

from database.credentials import ConnectionStore, dpapi_available
from database.store import Store


def remember_checkbox():
    box = QCheckBox('パスワードを保存する（Windowsのアカウントで暗号化。IPとユーザーは常に記憶します）')
    if not dpapi_available():
        box.setText('この環境ではパスワードを保存できません（IPとユーザーは記憶します）')
        box.setEnabled(False)
    return box


def prefill(db_path, host, user, password, remember, *, only_host=None):
    """Fill the fields from the saved connection. With only_host, use it only for that device."""
    try:
        db = Store(db_path)
        try:
            store = ConnectionStore(db)
            saved = store.load()
            if only_host is not None and saved['host'] != only_host:
                return
            if only_host is None and saved['host']:
                host.setText(saved['host'])
            user.setText(saved['user'])
            password.setText(saved['password'])
            remember.setChecked(bool(saved['password']))
        finally:
            db.close()
    except (OSError, sqlite3.Error):
        pass


def commit(db_path, host, user, password, remember, *, only_host=None):
    """Remember what the user just used. A failure here must never block the connection itself."""
    if not host or not user:
        return
    try:
        db = Store(db_path)
        try:
            store = ConnectionStore(db)
            if only_host is not None and store.load()['host'] != only_host:
                return
            store.save(host, user, password, remember)
        finally:
            db.close()
    except (OSError, sqlite3.Error):
        pass

"""Remember the MiSTer FTP connection between sessions.

The IP and user name are ordinary settings. The password is stored only as a Windows DPAPI blob
bound to the current Windows account (never in clear text) and only when the user opts in.
Without DPAPI (non-Windows) the password is simply not remembered."""
import base64
import sys

KEY = 'ftp/connection'
ENTROPY = b'mister-jp-library/ftp-password'


def dpapi_available():
    return sys.platform == 'win32'


def _dpapi(protect, data):
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]

    def blob(value):
        buffer = ctypes.create_string_buffer(value, len(value))
        return Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer

    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    source, keep_source = blob(data)
    entropy, keep_entropy = blob(ENTROPY)
    result = Blob()
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    pointer = ctypes.POINTER(Blob)
    function.argtypes = [pointer, ctypes.c_void_p, pointer, ctypes.c_void_p, ctypes.c_void_p,
                         wintypes.DWORD, pointer]
    if not function(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 0, ctypes.byref(result)):
        raise OSError('Windowsの資格情報保護に失敗しました')
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


def protect(data):
    return _dpapi(True, data)


def unprotect(data):
    return _dpapi(False, data)


class ConnectionStore:
    def __init__(self, db, *, protector=None, unprotector=None):
        self.db = db
        self.protect, self.unprotect = protector or protect, unprotector or unprotect
        self.can_save_password = dpapi_available() if protector is None else True

    def load(self):
        """Return host, user and password ('' when none or undecryptable)."""
        saved = self.db.setting(KEY, None)
        if not isinstance(saved, dict):
            return dict(host='', user='root', password='')
        password = ''
        if saved.get('password') and self.can_save_password:
            try:
                password = self.unprotect(base64.b64decode(saved['password'])).decode('utf-8')
            except (OSError, ValueError):
                # Another Windows account or a damaged value: ask again instead of failing.
                password = ''
        return dict(host=str(saved.get('host', '')), user=str(saved.get('user') or 'root'), password=password)

    def save(self, host, user, password, remember_password):
        entry = dict(host=host, user=user, password=None)
        if remember_password and password and self.can_save_password:
            entry['password'] = base64.b64encode(self.protect(password.encode('utf-8'))).decode('ascii')
        self.db.set_setting(KEY, entry)

    def has_password(self):
        saved = self.db.setting(KEY, None)
        return bool(isinstance(saved, dict) and saved.get('password'))

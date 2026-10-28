"""Read-only transport. No upload, rename, delete, or ROM download methods."""
import ftplib
from pathlib import PurePosixPath


class ConnectionFailure(RuntimeError):
    pass


def validate_path(path):
    if not path.startswith('/') or '..' in path.split('/') or any(ord(c) < 32 for c in path):
        raise ValueError('Remote path must be an absolute POSIX path without traversal or controls')
    return str(PurePosixPath(path))


class FTPReader:
    def __init__(self, host, username, password, port=21, timeout=15, ftp_factory=ftplib.FTP):
        self.host, self.port, self.timeout = host, port, timeout
        self._username, self._password = username, password
        self._factory = ftp_factory
        self._ftp = None

    def __enter__(self):
        self._ftp = self._factory()
        try:
            self._ftp.connect(self.host, self.port, timeout=self.timeout)
            self._ftp.login(self._username, self._password)
            self._ftp.encoding = 'utf-8'
            return self
        except (OSError, EOFError, ftplib.Error) as error:
            self._ftp.close()
            # Server responses may repeat credentials. Do not expose the original exception.
            raise ConnectionFailure('FTP connection/login failed; check host, credentials and timeout') from None

    def __exit__(self, *args):
        self._ftp.close()
        self._password = None

    def list_directory(self, path):
        path = validate_path(path)
        try:
            rows = list(self._ftp.mlsd(path))
        except (OSError, EOFError, ftplib.Error):
            raise ConnectionFailure('FTP directory listing failed; check access and MLSD support') from None
        result = []
        for name, facts in rows:
            if facts.get('type') in ('cdir', 'pdir'):
                continue
            if not name or name in ('.', '..') or '/' in name or '\\' in name or any(ord(c) < 32 for c in name):
                raise ValueError('Unsafe directory entry received from FTP server')
            result.append(dict(name=name, type=facts.get('type', 'unknown'), size=facts.get('size')))
        return result

    def scan(self, root, recursive=False, cancelled=lambda: False, extensions=('.nes', '.zip')):
        root = validate_path(root)
        result = dict(root=root, roms=[], existing_names=[], media_directories=[], directories=[],
                      inspection='listing only; media naming and image association unverified')
        pending = [root]
        visited = set()
        while pending:
            if cancelled():
                raise InterruptedError('Scan cancelled')
            directory = pending.pop()
            if directory in visited:
                continue
            visited.add(directory)
            for entry in self.list_directory(directory):
                if cancelled():
                    raise InterruptedError('Scan cancelled')
                path = str(PurePosixPath(directory) / entry['name'])
                if entry['type'] == 'file':
                    if PurePosixPath(path).suffix.lower() in extensions:
                        result['roms'].append(path)
                    elif entry['name'].lower() == 'names.txt':
                        result['existing_names'].append(path)
                elif entry['type'] == 'dir':
                    result['directories'].append(path)
                    if entry['name'].lower() == 'media':
                        result['media_directories'].append(path)
                    elif recursive:
                        pending.append(path)
        for key in ('roms', 'existing_names', 'media_directories', 'directories'):
            result[key].sort()
        return result

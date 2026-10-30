"""Read a plain ROM or a single-ROM ZIP without extracting into the user's folder.

A ZIP is transferred as the ZIP itself (name and size are the archive's); the inner ROM is only
validated and read for matching hashes."""
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
import stat
import zipfile
import hashlib

from systems import system_profile

def stream_sha256(stream):
    """Hash a stream on Python 3.10 as well as newer versions."""
    sha = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        sha.update(chunk)
    return sha.hexdigest()


def rom_info(path, system, member=None):
    path = Path(path)
    profile = system_profile(system)
    extensions = profile.transfer_extensions
    if path.suffix.lower() != '.zip':
        if member is not None or path.suffix.lower() not in extensions or not path.is_file():
            raise ValueError('機種に対応するROMファイルではありません: ' + path.name)
        return dict(name=path.name, size=path.stat().st_size, archive_member=None)
    try:
        with zipfile.ZipFile(path) as archive:
            candidates = []
            for entry in archive.infolist():
                name = PurePosixPath(entry.filename)
                if (name.is_absolute() or '..' in name.parts or '\\' in entry.filename
                        or ':' in entry.filename or any(ord(c) < 32 for c in entry.filename)
                        or stat.S_ISLNK(entry.external_attr >> 16)):
                    raise ValueError('ZIPに使用できないファイル名があります: ' + path.name)
                if not entry.is_dir() and name.suffix.lower() in extensions:
                    candidates.append(entry)
            if len(candidates) != 1:
                raise ValueError('ZIPには選択機種のROMを1本だけ入れてください: ' + path.name)
            entry = candidates[0]
            if entry.flag_bits & 1 or entry.file_size > profile.max_zip_rom_bytes:
                raise ValueError('暗号化ZIP・%d MiBを超えるROMは対象外です: %s' % (
                    profile.max_zip_rom_bytes // (1024 * 1024), path.name))
            if member is not None and entry.filename != member:
                raise ValueError('登録後にZIP内のROM名が変更されました: ' + path.name)
            return dict(name=path.name, size=path.stat().st_size, archive_member=entry.filename,
                        inner_name=PurePosixPath(entry.filename).name)
    except (zipfile.BadZipFile, NotImplementedError) as error:
        raise ValueError('ZIPを読み取れません: ' + path.name) from error


@contextmanager
def open_rom(path, system, member=None, raw=False):
    """Stream the ROM (inner ROM for a ZIP); raw=True streams the file itself, e.g. the ZIP for transfer."""
    info = rom_info(path, system, member)
    if info['archive_member'] is None or raw:
        with Path(path).open('rb') as stream:
            yield stream
    else:
        try:
            with zipfile.ZipFile(path) as archive, archive.open(info['archive_member']) as stream:
                yield stream
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as error:
            raise ValueError('ZIPの展開・CRC検証に失敗しました: ' + Path(path).name) from error

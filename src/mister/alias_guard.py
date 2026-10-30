"""Inspect live game files before changing globally shared aliases."""
from pathlib import PurePosixPath

from .ftp_client import validate_path


def check_alias_collisions(ftp, mapping, allowed_paths=(), *, cancelled=lambda: False):
    allowed = {validate_path(p) for p in allowed_paths}
    pending, seen = ['/media/fat/games'], set()
    conflicts = []
    while pending:
        if cancelled():
            raise InterruptedError('別名の衝突検査を中断しました')
        directory = pending.pop()
        if directory in seen:
            continue
        seen.add(directory)
        for name, facts in ftp.mlsd(directory):
            if facts.get('type') in ('cdir', 'pdir'):
                continue
            if (not name or name in ('.', '..') or '/' in name or '\\' in name
                    or any(ord(c) < 32 for c in name)):
                raise ValueError('実機の一覧に不正な名前があります')
            if name.startswith('.') or (name.casefold() == 'media' and facts.get('type') == 'dir'):
                continue
            if facts.get('type') not in ('dir', 'file'):
                raise ValueError('リンクまたは不明な種類の項目があり、別名の衝突を確認できません: ' + name)
            path = str(PurePosixPath(directory) / name)
            if facts.get('type') == 'dir':
                pending.append(path)
            elif PurePosixPath(name).stem in mapping and path not in allowed:
                conflicts.append(path)
    if conflicts:
        raise ValueError('実機の別ROMと別名キーが重複します: ' + '、'.join(sorted(conflicts)))

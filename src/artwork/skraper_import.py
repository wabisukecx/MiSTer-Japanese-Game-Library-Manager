"""Import user-selected Skraper PNG exports as local candidates, never ROMs."""
from collections import defaultdict
import hashlib
from pathlib import Path, PurePosixPath
import shutil

from .cache import MAX_BYTES, valid_png
from systems import SYSTEMS


def image_stems(game):
    stems = {PurePosixPath(game['rom_filename']).stem}
    if game.get('archive_member') and game.get('local_rom_path'):
        stems.add(Path(game['local_rom_path']).stem)
        stems.add(PurePosixPath(game['archive_member']).stem)
    return stems


def inspect_png(path):
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('画像が16 MiBを超えています')
    with path.open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    if not valid_png(data):
        raise ValueError('PNGの構造またはチェックサムが不正です')
    return hashlib.sha256(data).hexdigest()


def plan_import(root, games, *, cancelled=lambda: False, progress=lambda done, total: None):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError('画像フォルダーが見つかりません')
    by_stem = defaultdict(list)
    for game in games:
        if game['system'] in SYSTEMS:
            for stem in image_stems(game):
                by_stem[stem].append(game['id'])
    files = []
    for path in root.rglob('*'):
        if cancelled():
            raise InterruptedError('画像の確認を中断しました')
        if path.suffix.lower() == '.png' and path.is_file():
            files.append(path)
    counts = defaultdict(int)
    for path in files:
        counts[path.stem] += 1
    items = []
    for index, path in enumerate(sorted(files)):
        if cancelled():
            raise InterruptedError('画像の確認を中断しました')
        ids = by_stem.get(path.stem, [])
        item = dict(path=str(path.resolve()), filename=path.name, status='skipped', reason='対応するROMなし')
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            item['reason'] = 'フォルダー外へのリンクは対象外'
        elif len(ids) > 1 or (ids and counts[path.stem] > 1):
            item['reason'] = '同名ROMまたは画像が複数あるため要確認'
        elif len(ids) == 1:
            try:
                item.update(status='ready', reason='拡張子を除くROM名が完全一致', game_id=ids[0],
                            sha256=inspect_png(path))
            except (ValueError, OSError) as error:
                item.update(status='error', reason=str(error))
        items.append(item)
        progress(index + 1, len(files))
    return dict(root=str(root), items=items, ready_count=sum(i['status'] == 'ready' for i in items))


def apply_import(db, plan, region='UNKNOWN', *, library=None, cancelled=lambda: False,
                 progress=lambda done, total: None):
    """Register ready images. With library, each PNG is first copied to <library>/<system>/ and the copy
    is what the database keeps, so Skraper's own folders can be moved or cleaned freely."""
    if region not in ('UNKNOWN', 'JP', 'US', 'EU', 'WORLD'):
        raise ValueError('画像地域が不正です')
    # updated: same file re-imported with new content; unselected: imported beside a different selected image.
    result = dict(imported=0, errors=0, cancelled=False, updated=0, unselected=0)
    items = [item for item in plan['items'] if item['status'] == 'ready']
    root = Path(plan['root']).resolve()
    for index, item in enumerate(items):
        if cancelled():
            result['cancelled'] = True
            break
        try:
            path = Path(item['path'])
            if not path.resolve().is_relative_to(root) or path.is_symlink():
                raise ValueError('画像の場所が変更されています')
            if inspect_png(path) != item['sha256']:
                raise ValueError('プレビュー後に画像が変更されています')
            game = db.game(item['game_id'])
            if path.stem not in image_stems(game) or game['system'] not in SYSTEMS:
                raise ValueError('ROMとの対応が変更されています')
            stored = path.resolve()
            if library is not None:
                stored = Path(library).resolve() / game['system'] / path.name
                if not stored.is_file() or inspect_png(stored) != item['sha256']:
                    stored.parent.mkdir(parents=True, exist_ok=True)
                    temp = stored.with_name('.' + stored.name + '.tmp')
                    shutil.copyfile(path, temp)
                    if inspect_png(temp) != item['sha256']:
                        temp.unlink()
                        raise ValueError('コピーした画像を確認できません')
                    temp.replace(stored)
                # An image imported before the library existed keeps its identity: repoint it at the copy.
                db.adopt_library_copy(game['id'], path.resolve().as_uri(), stored)
            uri = stored.as_uri()
            previous = db.db.execute('SELECT sha256 FROM artworks WHERE game_id=? AND url=?',
                                     (game['id'], uri)).fetchone()
            db.save_artwork(game['id'], 'skraper', region, uri, stored,
                            item['sha256'], select=False)
            result['imported'] += 1
            if previous and previous['sha256'] != item['sha256']:
                result['updated'] += 1
            if game['artwork_path'] and game['artwork_path'] != str(stored):
                result['unselected'] += 1
        except (ValueError, OSError):
            result['errors'] += 1
        progress(index + 1, len(items))
    return result

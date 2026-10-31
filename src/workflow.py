"""UI-independent library registration and transfer planning."""
import os
from pathlib import Path, PurePosixPath

from systems import system_profile
from metadata.titles import match_titles
from metadata.approval import approval_plan, apply_approval
from consolemode.titles import title_mapping
from mister.artwork_transfer import transfer_plan, rom_transfer_plan


def register_folder(db, folder, device, system, destination, cache_root, *, lookup=True,
                    cancelled=lambda: False, progress=lambda done, total: None, matcher=match_titles):
    folder = Path(folder).resolve(strict=True)
    if not device.strip():
        raise ValueError('ライブラリ名を入力してください')
    if not folder.is_dir():
        raise ValueError('PCのROMフォルダーが見つかりません')
    profile = system_profile(system)
    if destination not in profile.roots:
        raise ValueError('機種の転送先を確認してください')
    members = (system, *profile.siblings)
    extensions = {e for m in members for e in system_profile(m).extensions}
    paths, names, duplicates = [], set(), []
    for current, dirs, files in os.walk(folder):
        if cancelled():
            raise InterruptedError('登録を中断しました')
        dirs[:] = sorted(d for d in dirs if d.casefold() != 'media' and not d.startswith('.'))
        for filename in sorted(files):
            path = Path(current) / filename
            if path.suffix.lower() in extensions:
                if filename.casefold() in names:
                    duplicates.append(filename)
                else:
                    names.add(filename.casefold())
                    paths.append(path)
    ids, skipped = [], []
    for index, path in enumerate(paths):
        if cancelled():
            raise InterruptedError('登録を中断しました。登録済みのROMは保持されます。')
        for member in members:
            try:
                ids.append(db.register_local_rom(device, member, path,
                    destination if member == system else system_profile(member).root))
                break
            except ValueError:
                continue
        else:
            skipped.append(path.name)
        progress(index + 1, len(paths))
    db.set_setting('ui/scope', dict(device=device, system=system, rom_folder=str(folder)))
    for member in members:
        key = 'workflow/' + device + '/' + member
        existing = db.setting(key, {})
        db.set_setting(key, dict(device=device, system=member, rom_folder=str(folder),
            remote_root=destination if member == system else system_profile(member).root,
            image_folder=existing.get('image_folder', str(folder / 'media' / 'box2dfront'))))
    matched, error = None, None
    if lookup and ids:
        try:
            matched = matcher(db, [db.game(i) for i in ids], cache_root,
                              cancelled=cancelled, progress=progress)
        except InterruptedError:
            raise
        except (ValueError, OSError) as exc:
            error = str(exc)
    return dict(count=len(ids), ids=ids, skipped=skipped, duplicates=duplicates,
                lookup=matched, lookup_error=error)


def batch_plan(db, games):
    """Use current DB state, rather than an old GUI snapshot, for all three plans."""
    current = [db.game(g['id']) for g in games]
    return _batch_plan(db, current)


def _batch_plan(db, current):
    selected = {game['id']: game for game in current}
    all_games = [selected.get(game['id'], game) for game in db.games()]
    images = transfer_plan(db, current, allow_empty=True)
    titles = title_mapping(current, allow_empty=True, all_games=all_games)
    roms = rom_transfer_plan(current)
    devices = {g['device'] for g in current}
    title_targets = [g['rom_path'] for g in all_games if g['device'] in devices
                     and g['match_status'] == 'confirmed'
                     and titles.get(PurePosixPath(g['rom_path']).stem) == g.get('japanese_title')
                     and PurePosixPath(g['rom_path']).stem in titles]
    return dict(images=images, titles=titles, roms=roms, title_targets=title_targets)


def review_transfer_plan(db, games):
    """Preview usable single candidates without saving until the user accepts the transfer."""
    current = [db.game(g['id']) for g in games]
    for game in current:
        game['_titles'], game['_artworks'] = db.get_candidates(game['id']), db.artworks(game['id'])
    approvals = approval_plan(current, include_weak=True)
    rows = []
    for game, choice in zip(current, approvals):
        if choice['title'] is not None:
            game.update(japanese_title=choice['title'], match_status='confirmed')
        if choice['artwork'] is not None:
            game['artwork_path'] = choice['artwork']['local_path']
        rows.append(dict(rom_filename=game['rom_filename'],
            display_title=game['japanese_title'] or PurePosixPath(game['rom_filename']).stem,
            image_path=game.get('artwork_path'), title_note=choice['title_note'] if game['japanese_title'] else '日本語名なし・ROM名で表示',
            image_note=choice['image_note'] if game.get('artwork_path') else '画像なし・画像は追加しない'))
    return dict(_batch_plan(db, current), approvals=approvals, review_rows=rows)


def accept_transfer_review(db, games, review, *, images=True, titles=True, roms=True, cancelled=lambda: False):
    """Save the explicit review, then refuse any transfer whose payload changed since preview."""
    result = apply_approval(db, review['approvals'], titles=titles, images=images, cancelled=cancelled)
    if result['cancelled'] or cancelled():
        raise InterruptedError('転送前の確認を中断しました')
    if result['errors'] or result['conflicts']:
        raise ValueError('確認後に候補が変わりました。名前と画像を確認し直してください。転送は開始していません。')
    fresh = batch_plan(db, games)
    for kind, enabled in (('images', images), ('titles', titles), ('roms', roms)):
        if enabled and fresh[kind] != review[kind]:
            raise ValueError('確認後に転送内容が変わりました。もう一度確認してください。転送は開始していません。')
        if not enabled:
            fresh[kind] = {} if kind == 'titles' else []
    return fresh

"""UI-independent library registration and transfer planning."""
import os
import stat
from pathlib import Path, PurePosixPath

from systems import system_profile
from metadata.titles import match_titles
from metadata.approval import approval_plan, apply_approval
from consolemode.titles import title_mapping
from mister.artwork_transfer import transfer_plan, rom_transfer_plan
from local_rom import rom_info


def available_games(db, *, cancelled=lambda: False):
    """Show existing ROMs and reconnect saved metadata by filename in the configured folder."""
    folders = {}
    visible = []
    for game in db.games():
        if cancelled():
            raise InterruptedError('一覧の読み込みを中断しました')
        if not game.get('local_rom_path'):
            visible.append(game)  # Explicit inventories from the MiSTer remain usable without PC ROMs.
            continue
        folder = db.setting('workflow/' + game['system'], {}).get('rom_folder')
        if folder:
            if folder not in folders:
                files = {}
                for current, dirs, names in os.walk(folder):
                    if cancelled():
                        raise InterruptedError('一覧の読み込みを中断しました')
                    dirs[:] = sorted(d for d in dirs if d.casefold() != 'media' and not d.startswith('.'))
                    for name in sorted(names):
                        files.setdefault(name, Path(current) / name)
                folders[folder] = files
            source = folders[folder].get(game['rom_filename'])
        else:
            source = Path(game['local_rom_path'])
        if source is None:
            continue
        try:
            if not source.is_file():
                continue
            if folder and not source.resolve().is_relative_to(Path(folder).resolve()):
                continue
            if str(source.resolve()) != game['local_rom_path']:
                info = rom_info(source, game['system'])
                with db.db:
                    db.db.execute('UPDATE local_roms SET local_rom_path=?,archive_member=? WHERE game_id=?',
                                  (str(source.resolve()), info['archive_member'], game['id']))
                game = db.game(game['id'])
        except (ValueError, OSError):
            continue
        visible.append(game)
    return visible


def local_rom_status(db, game):
    """Check the recorded file and current folder without changing saved metadata."""
    source = game.get('local_rom_path')
    if not source:
        return dict(label='実機一覧のみ', issue='', detail='PCのROMは未登録です。画像・名前のみ転送できます。')
    path = Path(source)
    profile = db.setting('workflow/' + game['system'], {})
    folder = profile.get('rom_folder')
    detail = '登録済みROM: ' + str(path)
    if folder:
        detail += '\n現在のROMフォルダー: ' + folder
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            raise FileNotFoundError(source)
        if folder and not path.resolve().is_relative_to(Path(folder).resolve()):
            return dict(label='設定フォルダー外', issue='登録済みROMが現在のROMフォルダー外にあります。', detail=detail)
    except (FileNotFoundError, NotADirectoryError):
        return dict(label='ROMが見つからない', issue='登録済みのPC ROMが見つかりません。', detail=detail)
    except OSError as error:
        return dict(label='ROMを確認できない', issue='PC ROMを確認できません: ' + str(error), detail=detail)
    return dict(label='確認済み', issue='', detail=detail)


def validate_local_roms(db, games):
    for game in games:
        state = local_rom_status(db, game)
        if state['issue']:
            raise ValueError(game['rom_filename'] + ': ' + state['issue'] + '\n' + state['detail'] +
                '\n設定したフォルダーにROMを置き、「再読み込み」を実行してください。転送は開始していません。')


def register_folder(db, folder, system, destination, cache_root, *, lookup=True,
                    cancelled=lambda: False, progress=lambda done, total: None, matcher=match_titles):
    folder = Path(folder).resolve(strict=True)
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
                ids.append(db.register_local_rom(member, path,
                    destination if member == system else system_profile(member).root))
                break
            except ValueError:
                continue
        else:
            skipped.append(path.name)
        progress(index + 1, len(paths))
    db.set_setting('ui/scope', dict(system=system, rom_folder=str(folder)))
    for member in members:
        key = 'workflow/' + member
        existing = db.setting(key, {})
        db.set_setting(key, dict(system=member, rom_folder=str(folder),
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
    validate_local_roms(db, current)
    return _batch_plan(db, current)


def _batch_plan(db, current):
    selected = {game['id']: game for game in current}
    all_games = [selected.get(game['id'], game) for game in db.games()]
    images = transfer_plan(db, current, allow_empty=True)
    titles = title_mapping(current, allow_empty=True, all_games=all_games)
    roms = rom_transfer_plan(current)
    title_targets = [g['rom_path'] for g in all_games if g['match_status'] == 'confirmed'
                     and titles.get(PurePosixPath(g['rom_path']).stem) == g.get('japanese_title')
                     and PurePosixPath(g['rom_path']).stem in titles]
    return dict(images=images, titles=titles, roms=roms, title_targets=title_targets)


def review_transfer_plan(db, games):
    """Preview usable single candidates without saving until the user accepts the transfer."""
    current = [db.game(g['id']) for g in games]
    validate_local_roms(db, current)
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
    validate_local_roms(db, [db.game(game['id']) for game in games])
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

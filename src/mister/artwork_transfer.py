"""One FTP session for missing local ROMs, cartridge media and Japanese aliases."""
import ftplib
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import uuid

from artwork.cache import MAX_BYTES
from artwork.skraper_import import inspect_png
from consolemode.titles import prepare_title_update
from systems import SYSTEMS, transfer_rom
from local_rom import rom_info, open_rom, stream_sha256
from .alias_guard import check_alias_collisions
from .recovery import rollback

MEDIA = '/media/fat/games/NES/media'


def rom_transfer_plan(games):
    items = []
    for game in games:
        if not game.get('local_rom_path'):
            continue
        _, target = transfer_rom(game)
        source = Path(game['local_rom_path']).resolve(strict=True)
        info = rom_info(source, game['system'], game.get('archive_member'))
        if info['name'] != target.name:
            raise ValueError('登録したPCのROM名と転送先が一致しません')
        with open_rom(source, game['system'], info['archive_member'], raw=True) as stream:
            sha = stream_sha256(stream)
        items.append(dict(system=game['system'], local_path=str(source),
                          remote_path=str(target), sha256=sha, size=info['size'], archive_member=info['archive_member']))
    return items


def remote_rom_digest(ftp, path, size):
    sha, count = hashlib.sha256(), 0
    def receive(chunk):
        nonlocal count
        count += len(chunk)
        if count > size:
            raise ValueError('転送したROMのサイズが一致しません')
        sha.update(chunk)
    ftp.retrbinary('RETR ' + path, receive)
    if count != size:
        raise ValueError('転送したROMのサイズが一致しません')
    return sha.hexdigest()


def transfer_plan(db, games, *, allow_empty=False):
    items = []
    for game in games:
        profile, rom = transfer_rom(game)
        if not game.get('artwork_path'):
            continue
        image = next((a for a in db.artworks(game['id']) if a['local_path'] == game['artwork_path']), None)
        if image and image.get('edit_stale'):
            raise ValueError('補正元画像が更新されています。新しい元画像から補正し直してください')
        if not image or inspect_png(Path(image['local_path'])) != image['sha256']:
            raise ValueError('選択画像が登録時から変わっています。再選択してください')
        name = rom.stem + '.png'
        if any(c in name for c in ('/', '\\', '\r', '\n')) or any(ord(c) < 32 for c in name):
            raise ValueError('転送先の画像名が不正です')
        items.append(dict(game_id=game['id'], system=game['system'], local_path=image['local_path'], sha256=image['sha256'],
                          remote_path=str(rom.parent / 'media' / name), rom_path=str(rom)))
    if len({i['remote_path'] for i in items}) != len(items):
        raise ValueError('転送先が重複しています。実機を1台だけ選んでください')
    if not items and not allow_empty:
        raise ValueError('選択済み画像がありません')
    return items


def digest(data):
    return hashlib.sha256(data).hexdigest()


def download(ftp, path):
    data = bytearray()
    def receive(chunk):
        if len(data) + len(chunk) > MAX_BYTES:
            raise ValueError('リモート画像が16 MiBを超えています')
        data.extend(chunk)
    ftp.retrbinary('RETR ' + path, receive)
    return bytes(data)


def transfer_images(items, host, username, password, backup_root, *, cancelled=lambda: False,
                    progress=lambda done, total: None, ftp_factory=ftplib.FTP, titles=None, roms=None,
                    title_targets=None, verify_existing_roms=False):
    """Back up existing media/aliases, then publish missing ROMs, images and aliases.

    Original files remain both locally and in a unique remote backup directory.
    Failure or cancellation restores published files in reverse order when possible.
    Conflicting external changes are preserved and recorded for later recovery.
    """
    roms = roms or []
    if not items and not titles and not roms:
        raise ValueError('転送する画像・日本語名がありません')
    media_dirs = {str(PurePosixPath(i['remote_path']).parent) for i in items}
    if len(media_dirs) > 1 or not media_dirs.issubset({m for s in SYSTEMS.values() for m in s.media_dirs}):
        raise ValueError('対応する1機種のmediaフォルダーだけを指定してください')
    media = next(iter(media_dirs), None)
    roots = set()
    for rom in roms:
        profile, target = transfer_rom(dict(system=rom['system'], rom_path=rom['remote_path']))
        roots.add(str(target.parent))
        if rom_info(rom['local_path'], rom['system'], rom.get('archive_member'))['name'] != target.name:
            raise ValueError('ROM名が一致しません')
    if len(roots) > 1 or (roots and media and media != next(iter(roots)) + '/media'):
        raise ValueError('転送先は1機種の同じフォルダーにしてください')
    if len({r['remote_path'] for r in roms}) != len(roms):
        raise ValueError('ROMの転送先が重複しています')
    run_id = uuid.uuid4().hex
    folder = Path(backup_root) / run_id
    folder.mkdir(parents=True, exist_ok=False)
    journal = dict(schema='mister-jp-transfer/v2', run_id=run_id,
                   host=host, media=media, status='preflight', items=[], titles=titles or {}, skipped_roms=[])
    report = folder / 'transfer.json'
    def save():
        tmp = folder / 'transfer.json.tmp'
        tmp.write_text(json.dumps(journal, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(report)
    save()
    def result(**extra):
        verified = [r for r in journal['items'] if r['status'] == 'verified']
        return dict(transferred=sum(r['kind'] == 'image' for r in verified),
                    roms_transferred=sum(r['kind'] == 'rom' for r in verified),
                    roms_skipped=len(journal['skipped_roms']),
                    titles_transferred=len(titles or {}) if any(r['kind'] == 'titles' for r in verified) else 0,
                    report=str(report.resolve()), **extra)
    ftp = ftp_factory()
    try:
        ftp.encoding = 'utf-8'
        ftp.connect(host, 21, timeout=20)
        ftp.login(username, password)
        password = None
        if cancelled():
            raise InterruptedError()
        # Resolve title settings/font before touching either kind of destination.
        if titles:
            allowed = title_targets if title_targets is not None else [
                i['rom_path'] for i in items if i.get('rom_path')] + [r['remote_path'] for r in roms]
            check_alias_collisions(ftp, titles, allowed, cancelled=cancelled)
        title_update = prepare_title_update(ftp, titles) if titles else None
        payloads = []
        root = next(iter(roots), str(PurePosixPath(media).parent) if media else None)
        root_listing = dict(ftp.mlsd(root)) if root else {}
        create_media = bool(items and 'media' not in root_listing)
        if items and not create_media and root_listing['media'].get('type') != 'dir':
            raise ValueError('mediaがフォルダーではありません')
        listing = dict(ftp.mlsd(media)) if items and not create_media else {}
        for item in roms:
            if cancelled():
                raise InterruptedError()
            target = PurePosixPath(item['remote_path'])
            existing_name = next((name for name in root_listing if name.casefold() == target.name.casefold()), None)
            facts = next((facts for name, facts in root_listing.items()
                          if name.casefold() == target.name.casefold()), None)
            if facts:
                if facts.get('type') != 'file':
                    raise ValueError('ROMの転送先が通常ファイルではありません')
                if verify_existing_roms:
                    with open_rom(item['local_path'], item['system'], item.get('archive_member'), raw=True) as stream:
                        if stream_sha256(stream) != item['sha256']:
                            raise ValueError('確認後にPCのROMが変更されました')
                    existing_path = str(target.parent / existing_name)
                    try:
                        equal = remote_rom_digest(ftp, existing_path, item['size']) == item['sha256']
                    except ValueError:
                        equal = False
                    if not equal:
                        raise ValueError('既存ROMの内容がPCと一致しません: ' + existing_name)
                journal['skipped_roms'].append(str(target))
                continue
            source = Path(item['local_path'])
            info = rom_info(source, item['system'], item.get('archive_member'))
            with open_rom(source, item['system'], item.get('archive_member'), raw=True) as stream:
                sha = stream_sha256(stream)
            if sha != item['sha256'] or info['size'] != item['size']:
                raise ValueError('確認後にPCのROMが変更されました')
            journal['items'].append(dict(item, kind='rom', original_existed=False, status='ready'))
            payloads.append(source)
        save()
        # All originals must be backed up before any upload or rename.
        for item in items:
            if cancelled():
                raise InterruptedError()
            target = PurePosixPath(item['remote_path'])
            if str(target.parent) != media or target.suffix != '.png' or any(ord(c) < 32 for c in str(target)):
                raise ValueError('転送先が許可されたPNGではありません')
            source = Path(item['local_path'])
            if inspect_png(source) != item['sha256']:
                raise ValueError('転送前にローカル画像が変わりました')
            data = source.read_bytes()
            if digest(data) != item['sha256']:
                raise ValueError('画像読み込み中に変更を検出しました')
            facts = listing.get(target.name)
            if facts and facts.get('type') != 'file':
                raise ValueError('画像の転送先が通常ファイルではありません: ' + target.name)
            original = download(ftp, str(target)) if facts else b''
            backup = folder / target.name
            backup.write_bytes(original)
            if digest(backup.read_bytes()) != digest(original):
                raise OSError('バックアップの照合に失敗しました')
            remote_backup = media + '/.mister-jp-backup-' + run_id + '/' + target.name
            row = dict(item, kind='image', original_existed=bool(facts),
                       backup=str(backup.resolve()), original_sha256=digest(original),
                       remote_backup=remote_backup, status='backed_up')
            journal['items'].append(row)
            payloads.append(data)
            save()
        if title_update:
            original, data = title_update['original'], title_update['data']
            target = title_update['remote_path']
            backup = folder / 'alias-original.txt'
            backup.write_bytes(original)
            (folder / 'alias-upload.txt').write_bytes(data)
            if backup.read_bytes() != original:
                raise OSError('別名バックアップの照合に失敗しました')
            journal['items'].append(dict(kind='titles', remote_path=target,
                original_existed=title_update['original_existed'], backup=str(backup.resolve()),
                original_sha256=digest(original), sha256=digest(data),
                remote_backup=target + '.mister-jp-' + run_id + '.bak', status='backed_up'))
            payloads.append(data)
            save()
        if cancelled():
            raise InterruptedError()
        if create_media:
            ftp.mkd(media)
        if any(r['kind'] == 'image' and r['original_existed'] for r in journal['items']):
            ftp.mkd(media + '/.mister-jp-backup-' + run_id)
        for index, (row, data) in enumerate(zip(journal['items'], payloads)):
            if cancelled():
                raise InterruptedError()
            target = row['remote_path']
            temp = str(PurePosixPath(target).parent) + '/.mister-jp-' + run_id + '-' + str(index) + '.tmp'
            row.update(temp_path=temp, status='staging')
            save()
            # Catch external changes since backup; never move an unbacked-up file.
            def check_original():
                if row['kind'] == 'titles':
                    check_alias_collisions(ftp, titles, allowed, cancelled=cancelled)
                if row['original_existed']:
                    if digest(download(ftp, target)) != row['original_sha256']:
                        raise ValueError('バックアップ後に実機のファイルが変更されました')
                elif PurePosixPath(target).name.casefold() in {
                        name.casefold() for name, _ in ftp.mlsd(str(PurePosixPath(target).parent))}:
                    raise ValueError('転送先に別のファイルが作成されました')
            check_original()
            if row['kind'] == 'rom':
                with open_rom(data, row['system'], row.get('archive_member'), raw=True) as stream:
                    ftp.storbinary('STOR ' + temp, stream)
                staged_sha = remote_rom_digest(ftp, temp, row['size'])
            else:
                ftp.storbinary('STOR ' + temp, io.BytesIO(data))
                staged_sha = digest(download(ftp, temp))
            if staged_sha != row['sha256']:
                raise ValueError('一時転送ファイルの照合に失敗しました')
            check_original()
            row['status'] = 'moving_original'
            save()
            if row['original_existed']:
                ftp.rename(target, row['remote_backup'])
            row['status'] = 'publishing'
            save()
            ftp.rename(temp, target)
            final_sha = (remote_rom_digest(ftp, target, row['size']) if row['kind'] == 'rom'
                         else digest(download(ftp, target)))
            if final_sha != row['sha256']:
                # Keep the published file for diagnosis; preserve the original backup.
                raise ValueError('転送後の画像照合に失敗しました')
            row['status'] = 'verified'
            save()
            progress(index + 1, len(payloads))
        journal['status'] = 'verified'
        save()
        return result()
    except InterruptedError:
        journal['outcome'] = 'cancelled'
        restored = rollback(ftp, journal, save)
        return result(cancelled=True, rolled_back=restored)
    except Exception as error:
        journal['outcome'] = 'failed'
        # Only locally generated validation messages are safe to show to users.
        reason = str(error) if type(error) is ValueError else ''
        restored = rollback(ftp, journal, save)
        # Do not expose server responses or authentication data.
        state = '反映済みファイルを元に戻しました。' if restored else '自動復旧が未完了です。転送記録から復旧してください。'
        raise RuntimeError('転送を停止しました。' + reason + ' ' + state + ' 復旧記録: ' + str(report.resolve())) from None
    finally:
        ftp.close()

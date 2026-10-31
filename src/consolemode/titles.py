"""Console Mode's public loadAliases/getAlias grammar; no guessed names.txt."""
import configparser
import ftplib
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import uuid

from mister.ftp_client import validate_path
from systems import transfer_rom
from mister.alias_guard import check_alias_collisions

FONT = '/media/fat/ConsoleMode/fonts/NotoSansCJK-Regular.otf'


def title_mapping(games, *, allow_empty=False, all_games=None):
    result = {}
    for game in games:
        if game['match_status'] != 'confirmed' or not game.get('japanese_title'):
            continue
        _, rom = transfer_rom(game)
        key, value = rom.stem, game['japanese_title']
        if '=' in key or any(ord(c) < 32 for c in key + value) or key.strip() != key or value.strip() != value:
            raise ValueError('別名ファイルに使用できない文字・前後の空白があります')
        if key in result and result[key] != value:
            raise ValueError('同じROM基本名に異なる日本語名があります')
        result[key] = value
    if not result and not allow_empty:
        raise ValueError('確定済みの日本語名がありません')
    if all_games is None:
        return result
    selected = {(g['system'], g['rom_path']) for g in games}
    for other in all_games or []:
        identity = (other['system'], other['rom_path'])
        key = PurePosixPath(other['rom_path']).stem
        if identity not in selected and key in result:
            if other['match_status'] != 'confirmed' or other.get('japanese_title') != result[key]:
                raise ValueError('別の機種・ROMと別名キーが重複します。個別確認が必要です: ' + key)
    return result


def prepare_title_update(ftp, mapping):
    """Resolve current settings on an existing connection before batch writes."""
    # Validate the generated protocol records even for direct API callers.
    for key, value in mapping.items():
        if (not key or not value or '=' in key or key.strip() != key or value.strip() != value
                or any(ord(c) < 32 for c in key + value)):
            raise ValueError('別名のキーまたは日本語名が不正です')
    config = configparser.ConfigParser(interpolation=None, strict=False)
    config.read_string(read_small(ftp, '/media/fat/ConsoleMode/config.ini').decode('utf-8-sig'))
    target = validate_path(config.get('GLOBAL', 'aliasPath', fallback='').strip())
    if not target.startswith('/media/fat/ConsoleMode/') or not target.endswith('.txt'):
        raise ValueError('別名ファイルの保存先が不正です')
    fonts = dict(ftp.mlsd('/media/fat/ConsoleMode/fonts'))
    if fonts.get(PurePosixPath(FONT).name, {}).get('type') != 'file':
        raise ValueError('日本語フォントがありません')
    entries = dict(ftp.mlsd(str(PurePosixPath(target).parent)))
    entry = entries.get(PurePosixPath(target).name)
    if entry is not None and entry.get('type') != 'file':
        raise ValueError('別名保存先が通常ファイルではありません')
    original = read_small(ftp, target) if entry else b''
    return dict(remote_path=target, original_existed=entry is not None,
                original=original, data=merge_aliases(original, mapping))


def merge_aliases(original, mapping):
    """Preserve untouched lines; replace all exact-key occurrences (last wins upstream)."""
    text = original.decode('utf-8')
    if text.startswith('\ufeff'):
        raise ValueError('既存別名ファイルにBOMがあります。手動確認が必要です')
    lines = text.splitlines(keepends=True)
    output, replaced = [], set()
    for line in lines:
        key, sep, _ = line.partition('=')
        if sep and key in mapping:
            if key not in replaced:
                output.append(key + '=' + mapping[key] + '\n')
                replaced.add(key)
        else:
            output.append(line)
    for key, value in mapping.items():
        if key not in replaced:
            if output and not output[-1].endswith('\n'):
                output.append('\n')
            output.append(key + '=' + value + '\n')
    return ''.join(output).encode('utf-8')


def read_small(ftp, path, limit=2 * 1024 * 1024):
    data = bytearray()
    def receive(chunk):
        if len(data) + len(chunk) > limit:
            raise ValueError('設定ファイルがサイズ上限を超えています')
        data.extend(chunk)
    ftp.retrbinary('RETR ' + path, receive)
    return bytes(data)


def inspect_settings(host, username, password, mapping, output_root, *, ftp_factory=ftplib.FTP, title_targets=()):
    """Read configuration, not credentials; persist only allowlisted findings."""
    ftp = ftp_factory()
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    result = dict(host=host, status='inspecting', mappings=mapping, font_path=FONT,
                  config_candidates=[], alias_path=None, font_present=False, transfer_allowed=False,
                  title_targets=list(title_targets))
    try:
        ftp.encoding = 'utf-8'
        ftp.connect(host, 21, timeout=20)
        ftp.login(username, password)
        password = None
        check_alias_collisions(ftp, mapping, title_targets)
        entries = dict(ftp.mlsd('/media/fat/ConsoleMode'))
        result['config_candidates'] = [n for n, f in entries.items() if f.get('type') == 'file' and n.lower().endswith('.ini')]
        # Do not save raw settings: they can contain scraper account credentials.
        if entries.get('config.ini', {}).get('type') == 'file':
            data = read_small(ftp, '/media/fat/ConsoleMode/config.ini')
            config = configparser.ConfigParser(interpolation=None, strict=False)
            config.read_string(data.decode('utf-8-sig'))
            path = config.get('GLOBAL', 'aliasPath', fallback='').strip()
            if path:
                path = validate_path(path)
                if not path.startswith('/media/fat/ConsoleMode/') or not path.endswith('.txt'):
                    raise ValueError('設定の別名ファイルが想定範囲外です。手動確認してください')
                result['alias_path'] = path
                facts = dict(ftp.mlsd(str(PurePosixPath(path).parent)))
                alias_fact = facts.get(PurePosixPath(path).name)
                if alias_fact and alias_fact.get('type') != 'file':
                    raise ValueError('別名ファイルの保存先が通常ファイルではありません')
                original = read_small(ftp, path) if alias_fact else b''
                result['alias_existed'] = bool(alias_fact)
                result['original_sha256'] = hashlib.sha256(original).hexdigest()
                (root / 'alias-original.txt').write_bytes(original)
                (root / 'alias-preview.txt').write_bytes(merge_aliases(original, mapping))
                result['preview_path'] = str((root / 'alias-preview.txt').resolve())
        if entries.get('fonts', {}).get('type') == 'dir':
            fonts = dict(ftp.mlsd('/media/fat/ConsoleMode/fonts'))
            result['font_present'] = fonts.get(PurePosixPath(FONT).name, {}).get('type') == 'file'
        result['status'] = 'inspected'
    except Exception:
        # Avoid exceptions from FTP/config parsers that may disclose credentials.
        result['status'] = 'failed'
    finally:
        ftp.close()
    (root / 'inspection.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def transfer_titles(host, username, password, inspection, output_root, *, ftp_factory=ftplib.FTP):
    """Revalidate settings and originals, then publish only the reviewed alias merge."""
    if (inspection.get('host') != host or inspection.get('status') != 'inspected'
            or not inspection.get('alias_path') or not inspection.get('font_present')):
        raise ValueError('同じ実機の設定と日本語フォントを先に確認してください')
    target = validate_path(inspection['alias_path'])
    if not target.startswith('/media/fat/ConsoleMode/') or not target.endswith('.txt'):
        raise ValueError('別名ファイルの保存先が不正です')
    run_id = uuid.uuid4().hex
    folder = Path(output_root) / run_id
    folder.mkdir(parents=True, exist_ok=False)
    report = folder / 'transfer.json'
    journal = dict(host=host, remote_path=target, remote_backup=target + '.mister-jp-' + run_id + '.bak',
                   temp_path=target + '.mister-jp-' + run_id + '.tmp', status='preflight',
                   mappings=inspection['mappings'])
    def save():
        temp = folder / 'transfer.json.tmp'
        temp.write_text(json.dumps(journal, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(report)
    save()
    ftp = ftp_factory()
    try:
        ftp.encoding = 'utf-8'
        ftp.connect(host, 21, timeout=20)
        ftp.login(username, password)
        password = None
        check_alias_collisions(ftp, inspection['mappings'], inspection.get('title_targets', ()))
        config = configparser.ConfigParser(interpolation=None, strict=False)
        config.read_string(read_small(ftp, '/media/fat/ConsoleMode/config.ini').decode('utf-8-sig'))
        if config.get('GLOBAL', 'aliasPath', fallback='').strip() != target:
            raise ValueError('設定の保存先が変わりました')
        fonts = dict(ftp.mlsd('/media/fat/ConsoleMode/fonts'))
        if fonts.get(PurePosixPath(FONT).name, {}).get('type') != 'file':
            raise ValueError('日本語フォントがありません')
        facts = dict(ftp.mlsd(str(PurePosixPath(target).parent)))
        entry = facts.get(PurePosixPath(target).name)
        exists = entry is not None
        if exists and entry.get('type') != 'file':
            raise ValueError('別名保存先が通常ファイルではありません')
        if exists != inspection['alias_existed']:
            raise ValueError('別名ファイルの有無が変わりました')
        original = read_small(ftp, target) if exists else b''
        original_hash = hashlib.sha256(original).hexdigest()
        if original_hash != inspection['original_sha256']:
            raise ValueError('確認後に別名ファイルが変わりました')
        data = merge_aliases(original, inspection['mappings'])
        (folder / 'alias-original.txt').write_bytes(original)
        (folder / 'alias-upload.txt').write_bytes(data)
        journal.update(original_existed=exists, original_sha256=original_hash,
                       sha256=hashlib.sha256(data).hexdigest(), status='backed_up')
        save()
        ftp.storbinary('STOR ' + journal['temp_path'], io.BytesIO(data))
        if read_small(ftp, journal['temp_path']) != data:
            raise ValueError('一時ファイルが一致しません')
        if exists:
            if read_small(ftp, target) != original:
                raise ValueError('転送中に別名ファイルが変わりました')
            journal['status'] = 'moving_original'
            save()
            ftp.rename(target, journal['remote_backup'])
        journal['status'] = 'publishing'
        save()
        try:
            ftp.rename(journal['temp_path'], target)
        except Exception:
            if exists:
                ftp.rename(journal['remote_backup'], target)
                journal['status'] = 'restored'
                save()
            raise
        if read_small(ftp, target) != data:
            raise ValueError('転送後の別名ファイルが一致しません')
        journal['status'] = 'verified'
        save()
        return dict(status='verified', count=len(inspection['mappings']), report=str(report.resolve()))
    except Exception:
        journal['outcome'] = 'failed'
        save()
        raise RuntimeError('タイトル転送を停止しました。再調査・復旧記録を確認してください: ' + str(report.resolve())) from None
    finally:
        ftp.close()

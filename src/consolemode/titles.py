"""Console Mode's public loadAliases/getAlias grammar; no guessed names.txt."""
import configparser
from pathlib import PurePosixPath

from mister.ftp_client import validate_path
from systems import transfer_rom

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

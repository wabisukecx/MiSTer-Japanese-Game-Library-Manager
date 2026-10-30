"""Native Japanese title candidates from the public Retronian JSON API.

Data: Retronian GameDB, CC BY-SA 4.0; upstream provenance is retained per title.
Candidates are never confirmed by this module.
"""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import time
import urllib.request

from .matcher import normalize
from local_rom import open_rom

PLATFORMS = {'NES': 'fc', 'SNES': 'sfc', 'MD': 'md', 'GB': 'gb', 'GBC': 'gbc', 'GBA': 'gba', 'PCE': 'pce',
             'PS1': 'ps1', 'N64': 'n64', 'NEOGEO': 'neogeo', 'WS': 'ws', 'WSC': 'wsc'}
BASE = 'https://gamedb.retronian.com/api/v1'
SOURCE = 'https://github.com/retronian/retronian-gamedb'
LICENSE = 'CC BY-SA 4.0'
MAX_BYTES = 64 * 1024 * 1024
# The catalog is never bundled with this tool: each user downloads it into their own work folder.
NOTICE = '''These files are copies of the Retronian GameDB, downloaded by this tool for your own use.
Source:  https://github.com/retronian/retronian-gamedb  (https://gamedb.retronian.com/)
License: CC BY-SA 4.0  https://creativecommons.org/licenses/by-sa/4.0/
The data includes Wikipedia text (CC BY-SA 4.0); each title records its upstream source.
If you redistribute these files or data derived from them, keep this notice, credit the source,
mark your changes and share them under CC BY-SA 4.0. Delete the files to remove the local copy.
'''


def load_catalog(system, cache_root, *, refresh=False, opener=urllib.request.urlopen,
                 cancelled=lambda: False):
    platform = PLATFORMS[system]
    cache = Path(cache_root) / (platform + '.json')
    cached = None
    if cache.is_file():
        try:
            cached = json.loads(cache.read_text(encoding='utf-8'))
            if not isinstance(cached.get('games'), list):
                cached = None
        except (ValueError, OSError, AttributeError):
            cached = None
    if cached and not refresh and time.time() - cached.get('fetched_at', 0) < 7 * 86400:
        return cached['games'], 'cache'
    if cancelled():
        raise InterruptedError('日本語名の照合を中断しました')
    try:
        request = urllib.request.Request(BASE + '/' + platform + '.json',
                                         headers={'User-Agent': 'MiSTer-JP-Library/0.1'})
        with opener(request, timeout=20) as response:
            chunks, size = [], 0
            while chunk := response.read(65536):
                if cancelled():
                    raise InterruptedError('日本語名の照合を中断しました')
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError('日本語名DBがサイズ上限を超えています')
                chunks.append(chunk)
        rows = json.loads(b''.join(chunks))
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise ValueError('日本語名DBの形式が不正です')
    except (OSError, ValueError):
        if cached:
            return cached['games'], 'offline-cache'
        raise ValueError('日本語名DBを取得できませんでした。ネットワークを確認して再照合してください。') from None
    if cancelled():
        raise InterruptedError('日本語名の照合を中断しました')
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix('.tmp')
    tmp.write_text(json.dumps(dict(source=SOURCE, license=LICENSE, fetched_at=time.time(), games=rows),
                              ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(cache)
    (cache.parent / 'NOTICE.txt').write_text(NOTICE, encoding='utf-8')
    return rows, 'download'


def build_index(rows):
    exact, hashes, names = {}, {}, {}
    for row in rows:
        roms = [r for r in row.get('roms', []) if isinstance(r, dict)]
        for rom in roms:
            name = rom.get('name', '')
            if name:
                exact.setdefault(name.casefold(), []).append(row)
                names.setdefault(normalize(name), []).append(row)
            if rom.get('sha256'):
                hashes.setdefault(rom['sha256'].lower(), []).append(row)
        for title in row.get('titles', []):
            if isinstance(title, dict) and title.get('text'):
                names.setdefault(normalize(title['text']), []).append(row)
    return exact, hashes, names


def title_candidates(game, rows, *, cancelled=lambda: False, index=None):
    exact, hashes, names = index if index is not None else build_index(rows)
    matched, reason, score = [], '', 1.0
    source = game.get('local_rom_path')
    if source and Path(source).is_file():
        sha = hashlib.sha256()
        with open_rom(source, game['system'], game.get('archive_member')) as stream:
            while chunk := stream.read(1024 * 1024):
                if cancelled():
                    raise InterruptedError('日本語名の照合を中断しました')
                sha.update(chunk)
        matched = hashes.get(sha.hexdigest(), [])
        reason = '同一機種・ROMのSHA-256一致'
    if not matched:
        matched = exact.get(PurePosixPath(game['rom_filename']).stem.casefold(), [])
        reason = '同一機種・地域とRevを含むROM名一致（内容は未照合）'
    if not matched:
        matched = names.get(normalize(game['rom_filename']), [])
        reason, score = '同一機種・正規化したタイトル一致（地域・Revは未照合）', 0.8
    candidates, seen = [], set()
    for row in matched:
        for title in row.get('titles', []):
            text = title.get('text', '')
            if (title.get('lang') != 'ja' or title.get('script') not in ('Jpan', 'Kana', 'Hira')
                    or not re.search(r'[\u3040-\u30ff\u3400-\u9fff]', text)
                    or any(ord(c) < 32 for c in text) or text != text.strip()):
                continue
            # Keep different spellings/conflicts visible; identical candidates are collapsed.
            if text in seen:
                continue
            seen.add(text)
            entry = BASE + '/games/' + PLATFORMS[game['system']] + '/' + str(row['id']) + '.json'
            candidates.append(dict(japanese_title=text, score=score, reason=reason,
                source=entry, provider='retronian', upstream_source=title.get('source'),
                source_verified=bool(title.get('verified')), license=LICENSE))
    return candidates


def catalog_fetched_at(system, cache_root):
    """Unix time the cached catalog was downloaded, or None when unknown."""
    try:
        value = json.loads((Path(cache_root) / (PLATFORMS[system] + '.json')).read_text(encoding='utf-8'))['fetched_at']
        return float(value)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def match_titles(db, games, cache_root, *, refresh=False, cancelled=lambda: False,
                 progress=lambda done, total: None, loader=load_catalog, recheck_confirmed=False):
    """Refresh candidates. With recheck_confirmed, confirmed titles are only compared, never changed:
    differing single-candidate proposals are returned in result['updates'] for explicit approval."""
    catalogs, modes = {}, {}
    for system in sorted({g['system'] for g in games}):
        catalogs[system], modes[system] = loader(system, cache_root, refresh=refresh, cancelled=cancelled)
    indexes = {system: build_index(rows) for system, rows in catalogs.items()}
    result = dict(matched=0, ambiguous=0, missing=0, confirmed=0, modes=modes, updates=[],
                  fetched_at={system: catalog_fetched_at(system, cache_root) for system in catalogs})
    for index, game in enumerate(games):
        if cancelled():
            raise InterruptedError('日本語名の照合を中断しました。照合済み候補は保持されます。')
        current = db.game(game['id'])
        if current['match_status'] == 'confirmed':
            result['confirmed'] += 1
            if recheck_confirmed:
                fresh = title_candidates(game, catalogs[game['system']], cancelled=cancelled,
                                         index=indexes[game['system']])
                previous = [c for c in db.get_candidates(game['id']) if c.get('provider') != 'retronian']
                db.save_candidates(game['id'], previous + fresh)
                names = list(dict.fromkeys(c['japanese_title'] for c in fresh if c.get('score') == 1))
                if len(names) == 1 and names[0] != current['japanese_title']:
                    result['updates'].append(dict(game_id=game['id'], rom_filename=game['rom_filename'],
                                                  current=current['japanese_title'], proposed=names[0]))
        else:
            candidates = title_candidates(game, catalogs[game['system']], cancelled=cancelled,
                                          index=indexes[game['system']])
            previous = [c for c in db.get_candidates(game['id']) if c.get('provider') != 'retronian']
            db.save_candidates(game['id'], previous + candidates)
            result['matched' if candidates else 'missing'] += 1
            result['ambiguous'] += len({c['japanese_title'] for c in previous + candidates}) > 1
        progress(index + 1, len(games))
    return result

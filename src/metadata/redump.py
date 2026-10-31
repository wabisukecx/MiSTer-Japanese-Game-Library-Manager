"""Filename-based Japanese title candidates from Redump Local Names.

The third-party export is downloaded separately; no metadata is bundled.
CHD hashes are not hashes of Redump's original disc tracks.
"""
import json
from pathlib import Path, PurePosixPath
import re
import time
from urllib.parse import quote
import urllib.request

from .matcher import normalize

SOURCE = 'https://github.com/yingw/redump-local-names'
LICENSE = 'Public domain (Redump metadata declaration)'
LICENSE_URL = 'https://redump.info/about'
FILES = {
    'MCD': 'Sega - Mega CD & Sega CD - Datfile (545) (2026-02-18 10-05-51) (Local Name).json',
    'PCECD': 'NEC - PC Engine CD & TurboGrafx CD - Datfile (551) (2026-01-06 09-10-30) (Local Name).json',
    'SATURN': 'Sega - Saturn - Datfile (2413) (2026-02-18 13-11-49) (Local Name).json',
    'PS1': 'Sony - PlayStation - Datfile (10903) (2026-02-17 21-14-11) (Local Name).json',
}
PLATFORMS = FILES
MAX_BYTES = 16 * 1024 * 1024
NOTICE = '''Redump title metadata, fetched from the third-party Redump Local Names export.
Export: https://github.com/yingw/redump-local-names
Original source of this export: http://redump.org/
Current Redump metadata declaration: https://redump.info/about (public domain).
This declaration concerns metadata, not box art, game files or website source code.
Downloaded data is not part of this tool's MIT-licensed source or the Retronian catalog.
'''


def load_catalog(system, cache_root, *, refresh=False, opener=urllib.request.urlopen,
                 cancelled=lambda: False):
    cache = Path(cache_root) / (system.lower() + '.json')
    cached = None
    try:
        cached = json.loads(cache.read_text(encoding='utf-8'))
        if (not isinstance(cached, dict) or not valid_rows(cached.get('games')) or
                not isinstance(cached.get('fetched_at'), (int, float))):
            cached = None
    except (OSError, ValueError):
        pass
    if cancelled():
        raise InterruptedError('日本語名の照合を中断しました')
    if cached and not refresh and time.time() - cached.get('fetched_at', 0) < 7 * 86400:
        return cached['games'], 'cache'
    url = 'https://raw.githubusercontent.com/yingw/redump-local-names/main/' + quote(FILES[system], safe='')
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'MiSTer-JP-Library/0.1'})
        with opener(request, timeout=20) as response:
            chunks, size = [], 0
            while chunk := response.read(65536):
                if cancelled():
                    raise InterruptedError('日本語名の照合を中断しました')
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError('Redump日本語名DBがサイズ上限を超えています')
                chunks.append(chunk)
        payload = json.loads(b''.join(chunks))
        if not isinstance(payload, dict) or not valid_rows(payload.get('data')):
            raise ValueError('Redump日本語名DBの形式が不正です')
    except InterruptedError:
        raise
    except (OSError, ValueError):
        if cached:
            return cached['games'], 'offline-cache'
        raise ValueError('Redump日本語名DBを取得できませんでした。ネットワークを確認して再照合してください。') from None
    if cancelled():
        raise InterruptedError('日本語名の照合を中断しました')
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix('.tmp')
    tmp.write_text(json.dumps(dict(source=SOURCE, download_url=url, license=LICENSE,
        license_url=LICENSE_URL, fetched_at=time.time(), dat_version=payload.get('dat_version'),
        generated_at=payload.get('generated_at'), games=payload['data']), ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8')
    tmp.replace(cache)
    (cache.parent / 'NOTICE.txt').write_text(NOTICE, encoding='utf-8')
    return payload['data'], 'download'


def valid_rows(rows):
    return isinstance(rows, list) and all(isinstance(r, dict) and
        all(isinstance(r.get(k), str) for k in ('dat_name', 'local_name', 'disc_id', 'region')) and
        r['disc_id'].isascii() and r['disc_id'].isdigit() for r in rows)


def build_index(rows):
    exact, names = {}, {}
    for row in rows:
        # Local names can also be Korean/Russian: only offer Japanese releases with Japanese script.
        # Some export values contain literal Unicode escapes in addition to ordinary Japanese text.
        title = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m[1], 16)), row['local_name'])
        if ('Japan' not in [r.strip() for r in row['region'].split(',')] or
                not re.search(r'[\u3040-\u30ff\u3400-\u9fff]', title) or
                '\ufffd' in title or any(ord(c) < 32 or 0xd800 <= ord(c) <= 0xdfff for c in title) or
                title != title.strip()):
            continue
        row = dict(row, local_name=title)
        exact.setdefault(row['dat_name'].casefold(), []).append(row)
        names.setdefault(normalize(row['dat_name']), []).append(row)
    return exact, names


def title_candidates(game, rows, *, cancelled=lambda: False, index=None):
    if cancelled():
        raise InterruptedError('日本語名の照合を中断しました')
    exact, names = index if index is not None else build_index(rows)
    matched = exact.get(PurePosixPath(game['rom_filename']).stem.casefold(), [])
    score, reason = 1.0, '同一機種・Redump DAT名一致（内容は未照合）'
    if not matched:
        matched = names.get(normalize(game['rom_filename']), [])
        score, reason = 0.8, '同一機種・Redump正規化タイトル一致（地域・版・ディスクは未照合）'
    candidates, seen = [], set()
    for row in matched:
        title = row['local_name']
        if title in seen:
            continue
        seen.add(title)
        candidates.append(dict(japanese_title=title, score=score, reason=reason,
            match_kind='rom_name' if score == 1 else 'title_name',
            source='https://redump.info/disc/' + row['disc_id'], provider='redump',
            upstream_source=SOURCE, source_verified=False, license=LICENSE, license_url=LICENSE_URL))
    return candidates


def catalog_fetched_at(system, cache_root):
    try:
        return float(json.loads((Path(cache_root) / (system.lower() + '.json')).read_text(encoding='utf-8'))['fetched_at'])
    except (OSError, ValueError, KeyError, TypeError):
        return None

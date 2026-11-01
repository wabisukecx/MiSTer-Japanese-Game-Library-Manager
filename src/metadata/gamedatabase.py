"""Japanese titles from PigSaint's GameDataBase (CC BY 4.0), fetched per platform."""
import csv
import io
import json
from pathlib import Path
import re
import time
import urllib.request

from . import retronian

SOURCE = 'https://github.com/PigSaint/GameDataBase'
LICENSE = 'CC BY 4.0'
PLATFORMS = {
    'NES': 'console_nintendo_famicom_nes.csv',
    'SNES': 'console_nintendo_superfamicom_snes.csv',
    'GB': 'console_nintendo_gameboy.csv',
    'GBC': 'console_nintendo_gameboycolor.csv',
    'GBA': 'console_nintendo_gameboyadvance.csv',
    'N64': 'console_nintendo_nintendo64.csv',
    'MD': 'console_sega_megadrive_genesis.csv',
    'MCD': 'console_sega_megacd_segacd.csv',
    'SATURN': 'console_sega_saturn.csv',
    'PCE': 'console_nec_pcengine_turbografx_supergrafx.csv',
    'PCECD': 'console_nec_cdrom2.csv',
    'FDS': 'console_nintendo_famicomdisksystem.csv',
    'SG1000': 'console_sega_sg1000_sc3000_othellomultivision.csv',
    'SMS': 'console_sega_markIII_mastersystem.csv',
    'GG': 'console_sega_gamegear.csv',
    'S32X': 'console_sega_super32x.csv',
    'NGP': 'console_snk_neogeopocket_neogeopocketcolor.csv',
    'NGPC': 'console_snk_neogeopocket_neogeopocketcolor.csv',
    'VB': 'console_nintendo_virtualboy.csv',
}
MAX_BYTES = 16 * 1024 * 1024
NOTICE = '''GameDataBase, copyright 2024 PigSaint, https://github.com/PigSaint/GameDataBase
License: CC BY 4.0, https://creativecommons.org/licenses/by/4.0/
Changes: extracted Japanese-region titles, selected Japanese cover/screen text,
and converted CSV records to this tool's JSON matching format.
Retain attribution, the license link and this change notice when redistributing.
These downloaded records are separate from this tool's source code.
'''


def convert_csv(data, *, system=None):
    reader = csv.DictReader(io.StringIO(data.decode('utf-8-sig')))
    required = {'Title', 'Title (exact)', 'Title screen', 'Title screen (exact)', 'Region', 'SHA256'}
    if not required.issubset(reader.fieldnames or []):
        raise ValueError('GameDataBaseのCSV形式が不正です')
    rows = []
    for i, record in enumerate(reader):
        if not re.search(r'\bJapan\b', record.get('Region') or ''):
            continue
        tags = (record.get('Tags') or '').split()
        if system == 'NGP' and not any(t.startswith('#compatibility:neogeopocket>mono') for t in tags):
            continue
        if system == 'NGPC' and not any(t.startswith('#compatibility:neogeopocket>') and '>color' in t for t in tags):
            continue
        # '=' means "same as the ordinary title". Prefer the cover, then the title screen.
        title = None
        for field, fallback in (('Title (exact)', 'Title'), ('Title screen (exact)', 'Title screen')):
            value = record.get(field) or ''
            value = (record.get(fallback) or '') if value == '=' else value
            if (re.search(r'[\u3040-\u30ff\u3400-\u9fff]', value) and value == value.strip() and
                    not any(ord(c) < 32 for c in value) and '\ufffd' not in value):
                title = value
                break
        if title is None:
            continue
        aliases = {record.get('Title'), record.get('Title screen')}
        roms = [dict(name=name) for name in sorted(a for a in aliases if a and a != '=')]
        sha = record.get('SHA256') or ''
        if re.fullmatch(r'[0-9a-fA-F]{64}', sha):
            roms.append(dict(sha256=sha.lower()))
        rows.append(dict(id=str(i), roms=roms, titles=[dict(text=title, lang='ja', script='Jpan',
                                                          source=SOURCE, verified=False)]))
    return rows


def load_catalog(system, cache_root, *, refresh=False, opener=urllib.request.urlopen,
                 cancelled=lambda: False):
    cache = Path(cache_root) / (system.lower() + '.json')
    cached = None
    try:
        cached = json.loads(cache.read_text(encoding='utf-8'))
        if (not isinstance(cached, dict) or not isinstance(cached.get('games'), list) or
                not isinstance(cached.get('fetched_at'), (int, float))):
            cached = None
    except (OSError, ValueError):
        pass
    if cancelled():
        raise InterruptedError('日本語名の照合を中断しました')
    if cached and not refresh and time.time() - cached['fetched_at'] < 7 * 86400:
        return cached['games'], 'cache'
    url = 'https://raw.githubusercontent.com/PigSaint/GameDataBase/main/' + PLATFORMS[system]
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'MiSTer-JP-Library/0.1'})
        with opener(request, timeout=20) as response:
            chunks, size = [], 0
            while chunk := response.read(65536):
                if cancelled():
                    raise InterruptedError('日本語名の照合を中断しました')
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError('GameDataBaseがサイズ上限を超えています')
                chunks.append(chunk)
        rows = convert_csv(b''.join(chunks), system=system)
    except InterruptedError:
        raise
    except (OSError, ValueError, csv.Error):
        if cached:
            return cached['games'], 'offline-cache'
        raise ValueError('GameDataBaseを取得できませんでした。ネットワークを確認して再照合してください。') from None
    if cancelled():
        raise InterruptedError('日本語名の照合を中断しました')
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix('.tmp')
    tmp.write_text(json.dumps(dict(source=SOURCE, download_url=url, license=LICENSE,
        fetched_at=time.time(), games=rows), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(cache)
    (cache.parent / 'NOTICE.txt').write_text(NOTICE, encoding='utf-8')
    return rows, 'download'


build_index = retronian.build_index


def title_candidates(game, rows, *, cancelled=lambda: False, index=None):
    candidates = retronian.title_candidates(game, rows, cancelled=cancelled, index=index,
        entry_source=SOURCE + '/blob/main/' + PLATFORMS[game['system']],
        provider='gamedatabase', license_name=LICENSE)
    for candidate in candidates:
        if 'SHA-256' not in candidate['reason']:
            candidate.update(score=0.8, match_kind='title_name',
                             reason='同一機種・GameDataBaseのタイトル一致（地域・版は未照合）')
    return candidates


def catalog_fetched_at(system, cache_root):
    try:
        return float(json.loads((Path(cache_root) / (system.lower() + '.json')).read_text(encoding='utf-8'))['fetched_at'])
    except (OSError, ValueError, KeyError, TypeError):
        return None

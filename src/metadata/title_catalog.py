"""Portable, attributed library title snapshots and conservative offline merging."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from local_rom import open_rom
from systems import SYSTEMS, system_profile
from .title_resolution import group_candidates, title_key

FORMAT = 'mister-jp-title-db'
MAX_BYTES = 64 * 1024 * 1024
HASH_LENGTHS = {'sha256': 64, 'sha1': 40, 'crc32': 8}
EVIDENCE_FIELDS = ('japanese_title', 'provider', 'score', 'reason', 'source', 'upstream_source',
                   'license', 'license_url', 'source_verified', 'match_kind', 'fetched_at')


def attribution(value):
    if not isinstance(value, str):
        return None
    if (PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute() or
            value.startswith(('file:', 'ftp://', 'sftp://', 'smb://'))):
        return 'local-source'
    if value.startswith(('https://', 'http://')):
        parts = urlsplit(value)
        host = parts.netloc.rsplit('@', 1)[-1]
        secret = ('password', 'token', 'secret', 'api_key', 'apikey', 'credential', 'ssid', 'devid', 'authorization', 'username', 'pwd')
        query = [(k, v) for k, v in parse_qsl(parts.query) if not any(s in k.lower() for s in secret)]
        return urlunsplit((parts.scheme, host, parts.path, urlencode(query), parts.fragment))
    return value


def clean_evidence(candidate):
    result = {k: candidate[k] for k in EVIDENCE_FIELDS if k in candidate and
              (candidate[k] is None or isinstance(candidate[k], (str, int, float, bool)))}
    for key in ('source', 'upstream_source', 'license_url'):
        if key in result:
            result[key] = attribution(result[key])
    return result


def unique(items):
    seen, result = set(), []
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def identity(entry):
    for algorithm in ('sha256', 'sha1'):
        if entry['hashes'].get(algorithm):
            return entry['system'], algorithm, entry['hashes'][algorithm]
    return entry['system'], 'filename', entry['rom_names'][0].casefold(), entry['hashes'].get('crc32', '')


def finalize(entries):
    for entry in entries:
        entry['rom_names'] = sorted(set(entry['rom_names']))
        entry['candidates'] = unique(entry['candidates'])
        entry['confirmed_titles'] = sorted(set(entry['confirmed_titles']))
        entry['history'] = unique(entry['history'])
        entry['entry_id'] = hashlib.sha256(json.dumps(identity(entry), ensure_ascii=False).encode('utf-8')).hexdigest()
        entry['candidate_conflict'] = len(group_candidates(entry['candidates'])) > 1
        entry['confirmation_conflict'] = len({title_key(t) for t in entry['confirmed_titles']}) > 1
        entry['needs_review'] = entry['candidate_conflict'] or entry['confirmation_conflict']
    entries.sort(key=lambda e: (e['system'], e['entry_id']))
    licenses = {c.get('license') or 'unspecified' for e in entries for c in
                e['candidates'] + [c for h in e['history'] for c in h.get('evidence', [])]}
    if any(e['confirmed_titles'] for e in entries):
        licenses.add('User decisions: no redistribution license assigned by this tool')
    return dict(format=FORMAT, schema_version=1, created_at=datetime.now(timezone.utc).isoformat(),
        purpose='Local title aggregation. Original licenses remain in force; no automatic public distribution license.',
        licenses=sorted(licenses), entries=entries)


def export_catalog(db, games, *, hash_roms=False, cancelled=lambda: False, progress=lambda done, total: None):
    games = [game for game in games if game['system'] in SYSTEMS]
    entries = []
    for index, requested in enumerate(games):
        if cancelled():
            raise InterruptedError('日本語名DBの書き出しを中断しました')
        game = db.game(requested['id'])
        hashes = {}
        if hash_roms and game.get('local_rom_path'):
            sha = hashlib.sha256()
            with open_rom(game['local_rom_path'], game['system'], game.get('archive_member')) as stream:
                while chunk := stream.read(1024 * 1024):
                    if cancelled():
                        raise InterruptedError('日本語名DBの書き出しを中断しました')
                    sha.update(chunk)
            hashes['sha256'] = sha.hexdigest()
        history = []
        for item in db.title_decisions(game['id']):
            history.append({k: item.get(k) for k in ('id', 'action', 'previous_title', 'title', 'recorded_at')})
            history[-1]['evidence'] = [clean_evidence(c) for c in item.get('evidence', [])]
        entries.append(dict(system=game['system'], rom_names=[game['rom_filename']], hashes=hashes,
            candidates=[clean_evidence(c) for c in db.get_candidates(game['id'])], history=history,
            confirmed_titles=[game['japanese_title']] if game['match_status'] == 'confirmed' else []))
        progress(index + 1, len(games))
    return merge_catalogs([finalize(entries)], cancelled=cancelled)


def validate(document):
    if (not isinstance(document, dict) or document.get('format') != FORMAT or document.get('schema_version') != 1 or
            not isinstance(document.get('entries'), list) or len(document['entries']) > 100000):
        raise ValueError('対応する日本語名DB JSONではありません')
    for entry in document['entries']:
        if not isinstance(entry, dict):
            raise ValueError('日本語名DBのレコード形式が不正です')
        if not isinstance(entry.get('system'), str):
            raise ValueError('日本語名DBの機種が不正です')
        system_profile(entry['system'])
        names = entry.get('rom_names')
        if not isinstance(names, list) or not names or not all(isinstance(n, str) and n and
                '/' not in n and '\\' not in n and ':' not in n and not any(ord(c) < 32 for c in n) for n in names):
            raise ValueError('日本語名DBのROM名が不正です')
        hashes = entry.get('hashes')
        if not isinstance(hashes, dict) or any(k not in HASH_LENGTHS or not isinstance(v, str) or
                not re.fullmatch('[0-9a-f]{%d}' % HASH_LENGTHS[k], v) for k, v in hashes.items()):
            raise ValueError('日本語名DBのハッシュ形式が不正です')
        if not any(k in hashes for k in ('sha256', 'sha1')) and len({n.casefold() for n in names}) != 1:
            raise ValueError('ハッシュのない異なるROM名は別レコードにしてください')
        for field in ('candidates', 'history', 'confirmed_titles'):
            if not isinstance(entry.get(field), list):
                raise ValueError('日本語名DBの候補・確定名・履歴が不正です')
        if not all(isinstance(c, dict) and isinstance(c.get('japanese_title'), str) for c in entry['candidates']):
            raise ValueError('日本語名DBの候補が不正です')
        if not all(c.get('license') is None or isinstance(c.get('license'), str) for c in entry['candidates']):
            raise ValueError('日本語名DBのライセンスが不正です')
        if not all(isinstance(t, str) and t.strip() for t in entry['confirmed_titles']):
            raise ValueError('日本語名DBの確定名が不正です')
        for item in entry['history']:
            if not isinstance(item, dict) or not isinstance(item.get('evidence', []), list) or not all(
                    isinstance(c, dict) for c in item.get('evidence', [])):
                raise ValueError('日本語名DBの判断履歴が不正です')
            if (not all(item.get(k) is None or isinstance(item.get(k), str) for k in
                        ('id', 'action', 'previous_title', 'title', 'recorded_at')) or
                    not all(c.get('license') is None or isinstance(c.get('license'), str) for c in item.get('evidence', []))):
                raise ValueError('日本語名DBの判断履歴が不正です')


def merge_catalogs(documents, *, cancelled=lambda: False):
    merged = {}
    for document in documents:
        validate(document)
        for item in document['entries']:
            if cancelled():
                raise InterruptedError('日本語名DBのマージを中断しました')
            key = identity(item)
            entry = merged.setdefault(key, dict(system=item['system'], hashes=dict(item['hashes']),
                rom_names=[], candidates=[], confirmed_titles=[], history=[]))
            # Keep other hash metadata only if it does not contradict the chosen identity.
            for algorithm, value in item['hashes'].items():
                if algorithm in entry['hashes'] and entry['hashes'][algorithm] != value:
                    raise ValueError('同一レコードに矛盾するハッシュがあります')
                entry['hashes'][algorithm] = value
            entry['rom_names'].extend(item['rom_names'])
            entry['candidates'].extend(clean_evidence(c) for c in item['candidates'])
            entry['confirmed_titles'].extend(item['confirmed_titles'])
            for history in item['history']:
                clean = {k: history.get(k) for k in ('id', 'action', 'previous_title', 'title', 'recorded_at')}
                clean['evidence'] = [clean_evidence(c) for c in history.get('evidence', [])]
                entry['history'].append(clean)
    return finalize(list(merged.values()))


def read_catalog(path):
    with Path(path).open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('日本語名DB JSONがサイズ上限を超えています')
    document = json.loads(data.decode('utf-8-sig'))
    validate(document)
    return document


def write_catalog(path, document):
    validate(document)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    if len(data) > MAX_BYTES:
        raise ValueError('日本語名DB JSONがサイズ上限を超えています')
    with target.open('xb') as stream:
        stream.write(data)
    return dict(path=str(target), entries=len(document['entries']),
                conflicts=sum(e.get('needs_review', False) for e in document['entries']))

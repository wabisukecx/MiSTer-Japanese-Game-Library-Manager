"""Group typography variants and rank evidence without voting or choosing conflicting titles."""
import re
import unicodedata


def title_key(title):
    """Ignore width and whitespace only; retain symbols, subtitles and compatibility characters."""
    title = ''.join(chr(ord(c) - 0xfee0) if '\uff01' <= c <= '\uff5e' else c for c in title)
    title = re.sub(r'[\uff61-\uff9f]+', lambda m: unicodedata.normalize('NFKC', m[0]), title)
    return ''.join(c for c in unicodedata.normalize('NFC', title) if not c.isspace())


def evidence_rank(candidate):
    # The fallback supports candidates already stored by earlier versions.
    if candidate.get('match_kind') == 'sha256' or 'SHA-256' in candidate.get('reason', ''):
        return 3
    return 2 if candidate.get('score') == 1 else 1


def group_candidates(candidates):
    by_title = {}
    for candidate in candidates:
        key = title_key(candidate.get('japanese_title', ''))
        if key:
            by_title.setdefault(key, []).append(candidate)
    groups = []
    for key, evidence in by_title.items():
        best = max(evidence, key=evidence_rank)
        groups.append(dict(best, title_key=key, evidence=evidence, rank=evidence_rank(best)))
    groups.sort(key=lambda group: -group['rank'])
    for group in groups:
        group['recommended'] = bool(groups and group['rank'] == groups[0]['rank'] and
                                    sum(g['rank'] == group['rank'] for g in groups) == 1)
    return groups


def batch_title(candidates, *, allow_weak=False):
    """Conflicting spellings require individual review, even when one has a stronger match.

    allow_weak also accepts a single spelling backed only by a title-name match (score below 1)."""
    groups = group_candidates(candidates)
    if len(groups) != 1:
        return None
    return groups[0] if allow_weak or groups[0].get('score') == 1 else None

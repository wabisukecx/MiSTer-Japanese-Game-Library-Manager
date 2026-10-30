"""Concrete, reviewable batch approval; keep confirmed values and skip ambiguity."""
from pathlib import Path
import sqlite3

from artwork.skraper_import import inspect_png


def approval_plan(games):
    plan = []
    for game in games:
        strong = [c for c in game['_titles'] if c.get('score') == 1 and c.get('japanese_title', '').strip()]
        names = list(dict.fromkeys(c['japanese_title'] for c in strong))
        title = None
        if game['match_status'] == 'confirmed':
            title_note = '確定済みを保持'
        elif len(names) == 1:
            title = names[0]
            # Score 1 also covers a ROM-name match whose content was never compared; say which it was.
            hashed = any('SHA-256' in c.get('reason', '') for c in strong)
            title_note = '候補を確定（ROM内容一致）' if hashed else '候補を確定（ROM名一致・内容は未照合）'
        else:
            title_note = '候補なし、または複数候補のため個別確認'
        artwork = None
        artworks = game['_artworks']
        parents = {a.get('source_artwork_id') for a in artworks}
        # A correction made from an image that has since changed no longer matches its source.
        leaves = [a for a in artworks if a['id'] not in parents and not a.get('edit_stale')]
        if game['artwork_path']:
            selected = next((a['id'] for a in artworks if a['local_path'] == game['artwork_path']), None)
            newer = [a for a in leaves if selected is not None and a['id'] > selected]
            image_note = ('選択済みを保持（新しい画像候補あり・個別に確認）' if newer else '選択済みを保持')
        else:
            if len(leaves) == 1:
                artwork = leaves[0].copy()
                image_note = '補正済み画像を採用' if artwork.get('rotation_degrees') else '画像候補を採用'
            else:
                image_note = '画像なし、または複数候補のため個別確認'
        plan.append(dict(game_id=game['id'], rom_filename=game['rom_filename'], title=title,
                         artwork=artwork, title_note=title_note, image_note=image_note,
                         expected=dict(match_status=game['match_status'], japanese_title=game['japanese_title'],
                                       artwork_path=game['artwork_path'])))
    return plan


def apply_approval(db, plan, *, titles=True, images=True, cancelled=lambda: False,
                   progress=lambda done, total: None):
    result = dict(titles=0, images=0, errors=0, conflicts=0, cancelled=False, applied_ids=[])
    for index, item in enumerate(plan):
        if cancelled():
            result['cancelled'] = True
            break
        title = item['title'] if titles else None
        artwork = item['artwork'] if images else None
        if title is None and artwork is None:
            progress(index + 1, len(plan))
            continue
        try:
            # Keep each game atomic and recheck snapshot state inside the write lock.
            with db.db:
                db.db.execute('BEGIN IMMEDIATE')
                game = db.game(item['game_id'])
                if any(game[key] != value for key, value in item['expected'].items()):
                    result['conflicts'] += 1
                    continue
                if title is not None:
                    live_names = {c['japanese_title'] for c in db.get_candidates(game['id']) if c.get('score') == 1}
                    if live_names != {title} or any(ord(c) < 32 for c in title):
                        raise ValueError('承諾するタイトル候補が変更されています')
                if artwork is not None:
                    live = next((a for a in db.artworks(game['id']) if a['id'] == artwork['id']), None)
                    if live is None or live.get('edit_stale'):
                        raise ValueError('補正元画像が変更されています')
                    row = db.db.execute('SELECT * FROM artworks WHERE id=? AND game_id=?',
                                         (artwork['id'], game['id'])).fetchone()
                    if (row is None or row['sha256'] != artwork['sha256']
                            or row['local_path'] != artwork['local_path']
                            or inspect_png(Path(row['local_path'])) != row['sha256']):
                        raise ValueError('承諾する画像候補が変更されています')
                if title is not None:
                    db.db.execute("UPDATE games SET japanese_title=?,match_status='confirmed' WHERE id=?",
                                  (title, game['id']))
                if artwork is not None:
                    db.db.execute('UPDATE games SET artwork_source=?,artwork_region=?,artwork_path=? WHERE id=?',
                                  (row['source'], row['region'], row['local_path'], game['id']))
            result['titles'] += int(title is not None)
            result['images'] += int(artwork is not None)
            result['applied_ids'].append(item['game_id'])
        except (ValueError, OSError, sqlite3.Error):
            result['errors'] += 1
        progress(index + 1, len(plan))
    return result

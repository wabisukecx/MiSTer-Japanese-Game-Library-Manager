"""Combine independent title providers without replacing confirmed user choices."""
from pathlib import Path

from . import retronian, redump, gamedatabase
from .title_resolution import group_candidates, batch_title, title_key


def match_titles(db, games, cache_root, *, refresh=False, cancelled=lambda: False,
                 progress=lambda done, total: None, recheck_confirmed=False, providers=None):
    # Keep the existing Retronian cache location; Redump gets a separate sibling folder.
    providers = providers if providers is not None else (
        ('retronian', retronian, Path(cache_root)),
        ('redump', redump, Path(cache_root).parent / 'redump'),
        ('gamedatabase', gamedatabase, Path(cache_root).parent / 'gamedatabase'),
    )
    catalogs, modes, fetched, warnings = {}, {}, {}, []
    for system in sorted({g['system'] for g in games}):
        catalogs[system] = []
        for name, provider, root in providers:
            if system not in provider.PLATFORMS:
                continue
            key = system + '/' + name
            try:
                rows, modes[key] = provider.load_catalog(system, root, refresh=refresh, cancelled=cancelled)
            except InterruptedError:
                raise
            except (OSError, ValueError) as error:
                modes[key] = 'failed'
                warnings.append(name + ': ' + str(error))
                continue
            catalogs[system].append((name, provider, rows, provider.build_index(rows)))
            fetched[key] = provider.catalog_fetched_at(system, root)
    result = dict(matched=0, ambiguous=0, missing=0, confirmed=0, modes=modes,
                  fetched_at=fetched, updates=[], warnings=warnings)
    for index, game in enumerate(games):
        if cancelled():
            raise InterruptedError('日本語名の照合を中断しました。照合済み候補は保持されます。')
        current = db.game(game['id'])
        if current['match_status'] == 'confirmed':
            result['confirmed'] += 1
            if not recheck_confirmed:
                progress(index + 1, len(games))
                continue
        available = catalogs[game['system']]
        refreshed = {name for name, *_ in available}
        # A failed provider keeps its old candidates; other providers can still refresh.
        candidates = [c for c in db.get_candidates(game['id']) if c.get('provider') not in refreshed]
        for name, provider, rows, lookup in available:
            fresh = provider.title_candidates(game, rows, cancelled=cancelled, index=lookup)
            for candidate in fresh:
                candidate['fetched_at'] = fetched.get(game['system'] + '/' + name)
            candidates.extend(fresh)
        db.save_candidates(game['id'], candidates)
        groups = group_candidates(candidates)
        if current['match_status'] == 'confirmed':
            single = batch_title(candidates)
            # Conflicts, weak matches and failed refreshes cannot propose replacing a confirmed title.
            if (single and not any(
                    key.startswith(game['system'] + '/') and value not in ('cache', 'download')
                    for key, value in modes.items())):
                proposed = single['japanese_title']
                if title_key(proposed) != title_key(current['japanese_title']):
                    result['updates'].append(dict(game_id=game['id'], rom_filename=game['rom_filename'],
                        current=current['japanese_title'], proposed=proposed))
        else:
            result['matched' if candidates else 'missing'] += 1
            result['ambiguous'] += len(groups) > 1
        progress(index + 1, len(games))
    return result

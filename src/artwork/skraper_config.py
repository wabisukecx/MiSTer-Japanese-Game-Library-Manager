"""Read where Skraper itself will look for ROMs and write box art, so the user keeps Skraper's defaults.

Only the ROM folder and the first image output of a system are read; accounts and other settings are ignored.
"""
import json
from pathlib import Path


def system_paths(skraper_folder, screenscraper_id):
    try:
        config = json.loads((Path(skraper_folder) / 'skraper.config.json').read_text(encoding='utf-8-sig'))
        for system in config.get('Systems', []):
            if screenscraper_id not in (system.get('SystemId'), *system.get('SecondarySystemIds', [])):
                continue
            rom_folder = system['GamesAndRoms']['Path']
            media = next((m for m in system.get('Medias', []) if m.get('Type') == 0), None)
            if not rom_folder or not media or not media.get('OutputFolder'):
                return None
            output = (media['OutputFolder'].replace('%ROMROOTFOLDER%', rom_folder)
                      .replace('%SCREENSCRAPERPLATFORM%', system['GamesAndRoms'].get('Platform', '')))
            return dict(rom_folder=str(Path(rom_folder)), image_folder=str(Path(output)))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return None

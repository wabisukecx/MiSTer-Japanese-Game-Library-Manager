"""Supported cartridge systems; keep provider IDs and path rules together."""
from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class System:
    id: str
    name: str
    root: str
    extensions: tuple[str, ...]
    transfer_extensions: tuple[str, ...]
    libretro: str
    screenscraper_id: int
    verified: bool = False
    alternate_roots: tuple[str, ...] = ()
    # Systems registered from one PC folder (the ROM type decides which) but kept in separate MiSTer folders.
    siblings: tuple[str, ...] = ()

    @property
    def roots(self):
        return (self.root, *self.alternate_roots)

    @property
    def media_dirs(self):
        return tuple(root + '/media' for root in self.roots)

    @property
    def media(self):
        return self.root + '/media'


SYSTEMS = {
    'NES': System('NES', 'ファミコン', '/media/fat/games/NES', ('.nes', '.zip'), ('.nes',),
                  'Nintendo - Nintendo Entertainment System', 3, True),
    'SNES': System('SNES', 'スーパーファミコン', '/media/fat/games/SNES', ('.sfc', '.smc', '.bin', '.zip'),
                   ('.sfc', '.smc', '.bin'), 'Nintendo - Super Nintendo Entertainment System', 4, True),
    'MD': System('MD', 'メガドライブ', '/media/fat/games/MegaDrive', ('.bin', '.gen', '.md', '.zip'),
                 ('.bin', '.gen', '.md'), 'Sega - Mega Drive - Genesis', 1, True,
                 alternate_roots=('/media/fat/games/Genesis',)),
    # Console Mode Handheld.ini: GAMEBOY -> /media/fat/games/GAMEBOY (.gb), GAMEBOY COLOR -> /media/fat/games/GBC (.gbc).
    'GB': System('GB', 'ゲームボーイ', '/media/fat/games/GAMEBOY', ('.gb', '.zip'), ('.gb',),
                 'Nintendo - Game Boy', 9, True, siblings=('GBC',)),
    'GBC': System('GBC', 'ゲームボーイカラー', '/media/fat/games/GBC', ('.gbc', '.zip'), ('.gbc',),
                  'Nintendo - Game Boy Color', 10, True, siblings=('GB',)),
    # Console Mode Handheld.ini: GAMEBOY ADVANCE -> /media/fat/games/GBA (.gba).
    'GBA': System('GBA', 'ゲームボーイアドバンス', '/media/fat/games/GBA', ('.gba', '.zip'), ('.gba',),
                  'Nintendo - Game Boy Advance', 12, True),
    # Console Mode Console.ini: TURBOGRAFX-16 -> /media/fat/games/TGFX16 (.pce, .sgx). Not yet verified on hardware.
    'PCE': System('PCE', 'PCエンジン', '/media/fat/games/TGFX16', ('.pce', '.sgx', '.zip'), ('.pce', '.sgx'),
                  'NEC - PC Engine - TurboGrafx 16', 31, False),
}

ROM_EXTENSIONS = frozenset(ext for profile in SYSTEMS.values() for ext in profile.extensions)


def system_profile(system):
    try:
        return SYSTEMS[system]
    except KeyError:
        raise ValueError('未対応の機種です: ' + str(system)) from None


def transfer_rom(game):
    profile = system_profile(game['system'])
    rom = PurePosixPath(game['rom_path'])
    if (str(rom.parent) not in profile.roots or rom.suffix.lower() not in (*profile.transfer_extensions, '.zip')
            or any(ord(c) < 32 for c in str(rom)) or '\\' in str(rom)):
        raise ValueError('%sの転送は%s直下の%sのみ対応しています' % (
            profile.name, ' / '.join(profile.roots), ', '.join((*profile.transfer_extensions, '.zip'))))
    return profile, rom

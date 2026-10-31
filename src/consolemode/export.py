"""Stop at the research gate; do not invent Console Mode file formats."""
class SpecificationUnverified(RuntimeError):
    pass


BLOCKERS = [
    'the generic export command is disabled; use the GUI batch transfer (ROMs, images and aliases) or recover-transfer',
    'Japanese title rendering verified only on home-mister Console Mode v1.2.3 with the installed CJK font',
    'real-device verification covers NES, SNES, MD, GB, GBC, GBA, PCE and NEOGEO only; other systems are untested',
]


def review_manifest(games):
    return dict(schema='mister-jp-library-review/v1', transfer_allowed=False,
                blockers=BLOCKERS.copy(), files=[], games=games)


def export_consolemode(games):
    raise SpecificationUnverified('; '.join(BLOCKERS))

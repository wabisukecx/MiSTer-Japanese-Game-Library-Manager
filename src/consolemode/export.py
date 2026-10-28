"""Stop at the research gate; do not invent Console Mode file formats."""
class SpecificationUnverified(RuntimeError):
    pass


BLOCKERS = [
    'general export across systems and directory layouts unverified; NES GUI transfers are separate',
    'Japanese title rendering verified only on home-mister Console Mode v1.2.3 with the installed CJK font',
    'general artwork dimensions and cache invalidation unverified; four NES image replacements verified',
]


def review_manifest(games):
    return dict(schema='mister-jp-library-review/v1', transfer_allowed=False,
                blockers=BLOCKERS.copy(), files=[], games=games)


def export_consolemode(games):
    raise SpecificationUnverified('; '.join(BLOCKERS))

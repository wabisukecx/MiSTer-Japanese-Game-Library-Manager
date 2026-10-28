"""Conservative candidate matching. Filename similarity never confirms a title."""
import re
import unicodedata
from pathlib import PurePosixPath
from systems import ROM_EXTENSIONS


def normalize(filename):
    title = unicodedata.normalize('NFKC', filename)
    if PurePosixPath(title).suffix.lower() in ROM_EXTENSIONS:
        title = title.rsplit('.', 1)[0]
    title = re.sub(r'\([^)]*\)|\[[^]]*\]', ' ', title)
    return ' '.join(re.sub(r'[^\w\s]', ' ', title.casefold()).split())


def regions(filename):
    tags = re.findall(r'\(([^)]*)\)', filename)
    mapping = {'japan': 'JP', 'j': 'JP', 'jp': 'JP', 'usa': 'US', 'u': 'US',
               'us': 'US', 'europe': 'EU', 'e': 'EU', 'eu': 'EU', 'world': 'WORLD'}
    return list(dict.fromkeys(mapping[token.strip().lower()] for tag in tags
                             for token in tag.split(',') if token.strip().lower() in mapping))


def candidates(filename, system, aliases):
    key = normalize(filename)
    return [dict(japanese_title=a['japanese_title'], score=1.0,
                 reason='same system; normalized alias exact match; region/revision identity unverified',
                 source=a.get('source', 'local-import'), alias=a['alias'])
            for a in aliases if a['system'] == system and key and normalize(a['alias']) == key]

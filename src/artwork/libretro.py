"""Exact catalog-name lookup; never guess that an overseas box is Japanese."""
import re
from urllib.parse import quote
from systems import system_profile

BASE = 'https://thumbnails.libretro.com/Nintendo%20-%20Nintendo%20Entertainment%20System/Named_Boxarts/'


def artwork_url(catalog_title, system='NES'):
    if not catalog_title.strip() or any(ord(c) < 32 for c in catalog_title):
        raise ValueError('A catalog title without control characters is required')
    name = re.sub(r'[&*/:`<>?\\|\"]', '_', catalog_title)
    base = 'https://thumbnails.libretro.com/' + quote(system_profile(system).libretro, safe='') + '/Named_Boxarts/'
    return base + quote(name, safe='') + '.png'


def prioritize(items):
    def rank(item):
        if item['region'] == 'JP':
            return {'screenscraper': 0, 'libretro': 1}.get(item['source'], 2)
        return 3
    return sorted(items, key=rank)

"""Consistent console/handheld and manufacturer grouping for GUI system selectors."""
from PyQt6.QtCore import Qt

from systems import SYSTEMS


SYSTEM_GROUPS = (
    ('コンソール / 任天堂', ('NES', 'FDS', 'SNES', 'VB', 'N64')),
    ('コンソール / SEGA', ('SG1000', 'SMS', 'MD', 'MCD', 'S32X', 'SATURN')),
    ('コンソール / NEC', ('PCE', 'PCECD')),
    ('コンソール / ソニー', ('PS1',)),
    ('コンソール / SNK', ('NEOGEO',)),
    ('ハンドヘルド / 任天堂', ('GB', 'GBC', 'GBA')),
    ('ハンドヘルド / SEGA', ('GG',)),
    ('ハンドヘルド / SNK', ('NGP', 'NGPC')),
    ('ハンドヘルド / バンダイ', ('WS', 'WSC')),
)


def populate_systems(combo, *, include_all=False):
    previous = combo.currentData()
    blocked = combo.blockSignals(True)
    try:
        combo.clear()
        if include_all:
            combo.addItem('すべての機種', '')
        first_system = None
        for heading, identifiers in SYSTEM_GROUPS:
            combo.addItem(heading, None)
            item = combo.model().item(combo.count() - 1)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            font = item.font()
            font.setBold(True)
            item.setFont(font)
            for identifier in identifiers:
                profile = SYSTEMS[identifier]
                if first_system is None:
                    first_system = combo.count()
                combo.addItem(profile.name, profile.id)
        selected = combo.findData(previous) if previous is not None else -1
        combo.setCurrentIndex(selected if selected >= 0 else 0 if include_all else first_system)
        combo.setMaxVisibleItems(20)
    finally:
        combo.blockSignals(blocked)

"""Table model and filter for the game list."""
from PyQt6.QtCore import Qt, QAbstractTableModel, QSortFilterProxyModel, pyqtSignal
from PyQt6.QtGui import QColor

from metadata.title_resolution import group_candidates


class LibraryModel(QAbstractTableModel):
    checks_changed = pyqtSignal()
    headers = ('ROM名', '日本語タイトル', '地域', 'パッケージ', '照合状態')

    def __init__(self, parent=None):
        super().__init__(parent)
        self.games = []
        self.checked_ids = set()

    def replace(self, games):
        self.beginResetModel()
        self.games = games
        self.checked_ids.intersection_update(g['id'] for g in games)
        self.endResetModel()
        self.checks_changed.emit()

    def flags(self, index):
        flags = super().flags(index)
        if index.isValid() and index.column() == 0:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if index.isValid() and index.column() == 0 and role == Qt.ItemDataRole.CheckStateRole:
            game_id = self.games[index.row()]['id']
            if value == Qt.CheckState.Checked or value == Qt.CheckState.Checked.value:
                self.checked_ids.add(game_id)
            else:
                self.checked_ids.discard(game_id)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
            self.checks_changed.emit()
            return True
        return False

    def set_checks(self, ids, checked):
        if checked:
            self.checked_ids.update(ids)
        else:
            self.checked_ids.difference_update(ids)
        if self.games:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.games)-1, 0), [Qt.ItemDataRole.CheckStateRole])
        self.checks_changed.emit()

    def rowCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.games)

    def columnCount(self, parent=None):
        return len(self.headers)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headers[section]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        game = self.games[index.row()]
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            return Qt.CheckState.Checked if game['id'] in self.checked_ids else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.UserRole:
            return game
        if role == Qt.ItemDataRole.ToolTipRole:
            return game['rom_path']
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 4:
            return QColor('#14766a' if game['match_status'] == 'confirmed' else '#8e641a')
        if role == Qt.ItemDataRole.DisplayRole:
            groups = group_candidates(game['_titles'])
            candidate = groups[0]['japanese_title'] if groups else ''
            title = game['japanese_title'] or (candidate + '（候補）' if candidate else '—')
            art = '選択済み' if game['artwork_path'] else ('候補 %d枚' % len(game['_artworks']) if game['_artworks'] else '未取得')
            status = {'confirmed': '確定済み', 'review': '候補あり', 'unmatched': 'ROM名で表示'}.get(game['match_status'], game['match_status'])
            return (game['rom_filename'], title, game['region'] or '不明', art, status)[index.column()]


class LibraryFilter(QSortFilterProxyModel):
    system = ''
    query = ''
    pending = False
    ids = None  # when set, only these game IDs (the folder registered last) are shown

    def filterAcceptsRow(self, row, parent):
        game = self.sourceModel().games[row]
        if self.ids is not None and game['id'] not in self.ids:
            return False
        if self.system and game['system'] != self.system:
            return False
        if self.pending and game['match_status'] == 'confirmed':
            return False
        text = ' '.join([game['rom_filename'], game['japanese_title'] or '',
                         *[c['japanese_title'] for c in game['_titles']]])
        return self.query.casefold() in text.casefold()

"""Background work: short-lived DB access and the cancellable QThread wrapper."""
import threading

from PyQt6.QtCore import QThread, pyqtSignal

from database.store import Store
from systems import SYSTEMS
from workflow import available_games


def with_db(path, operation):
    db = Store(path)
    try:
        return operation(db)
    finally:
        db.close()


def load_library(path, cancelled, progress):
    def load(db):
        games = [game for game in available_games(db, cancelled=cancelled) if game['system'] in SYSTEMS]
        db.prune_missing_artworks(cancelled=cancelled, game_ids={game['id'] for game in games})
        games = [db.game(game['id']) for game in games]
        for index, game in enumerate(games):
            if cancelled():
                raise InterruptedError('一覧の読み込みを中断しました')
            game['_titles'] = db.get_candidates(game['id'])
            game['_artworks'] = db.artworks(game['id'])
            progress(index + 1, len(games))
        return games
    return with_db(path, load)


class Task(QThread):
    result = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int, int)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.cancel = threading.Event()

    def run(self):
        try:
            self.result.emit(self.operation(self.cancel.is_set, self.progress.emit))
        except InterruptedError as error:
            self.failed.emit(str(error))
        except Exception as error:
            self.failed.emit(str(error))

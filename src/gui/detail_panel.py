"""Selected-game panel: title candidates, cover preview, rotation and saving."""
from pathlib import Path
import time

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QImageReader, QPixmap, QTransform

from artwork.skraper_import import inspect_png
from metadata.title_resolution import group_candidates
from .tasks import with_db


class DetailMixin:
    def show_game(self, index, previous=None):
        game = index.data(Qt.ItemDataRole.UserRole)
        if not game:
            return
        self.current_game = game
        self.title_context.setText(game['rom_filename'])
        self.title_candidates.blockSignals(True)
        self.title_candidates.clear()
        self.title_candidates.addItem('手動で入力する', None)
        for candidate in group_candidates(game['_titles']):
            suffix = '（推奨・ROM内容一致）' if candidate['recommended'] and candidate['rank'] == 3 else '（候補）'
            self.title_candidates.addItem(candidate['japanese_title'] + suffix, candidate)
        saved_title = game['japanese_title']
        selected = 1 if self.title_candidates.count() > 1 else 0
        if saved_title:
            selected = next((i for i in range(1, self.title_candidates.count())
                             if self.title_candidates.itemData(i)['japanese_title'] == saved_title), 0)
        self.title_candidates.setCurrentIndex(selected)
        self.title_edit.setText(game['japanese_title'] or '')
        self.evidence.setPlainText('ユーザー確定済み' if game['match_status'] == 'confirmed' else '候補を選ぶか、タイトルを入力して確定してください。')
        self.title_candidates.blockSignals(False)
        if selected:
            self.use_title_candidate(selected)
        self.current_artwork = None
        self.pixmap = None
        self.cover.setPixmap(QPixmap())
        self.cover.setText('画像候補がありません')
        self.provenance.setText('')
        artworks = sorted(game['_artworks'], key=lambda a: (a['local_path'] != game['artwork_path'],
                         not a['source'].startswith('skraper'), a['region'] != 'JP', -a['id']))
        if artworks:
            preferred = next((i for i, a in enumerate(artworks) if a['id'] == self.desired_artwork_id), 0)
            if artworks[preferred]['id'] == self.desired_artwork_id:
                self.desired_artwork_id = None
            self.show_artwork(artworks[preferred])
        self.update_rotation_controls()

    def use_title_candidate(self, index):
        candidate = self.title_candidates.itemData(index)
        if candidate:
            self.title_edit.setText(candidate['japanese_title'])
            blocks = []
            labels = {'retronian': 'Retronian GameDB', 'redump': 'Redump',
                      'gamedatabase': 'GameDataBase by PigSaint'}
            for evidence in candidate.get('evidence', [candidate]):
                blocks.append('%s / %s\n表記: %s\n一致度 %s · %s\n出典: %s\n元データ: %s' % (
                    labels.get(evidence.get('provider'), '手動・その他'), evidence.get('license', '利用条件は出典を確認'),
                    evidence['japanese_title'], evidence.get('score', '—'), evidence.get('reason', ''),
                    evidence.get('source', '不明'), evidence.get('upstream_source', '不明')))
                stamp = evidence.get('fetched_at')
                if isinstance(stamp, (int, float)):
                    blocks[-1] += '\nDB取得日: ' + time.strftime('%Y-%m-%d', time.localtime(stamp))
            self.evidence.setPlainText('\n\n'.join(blocks))

    def show_artwork(self, artwork):
        self.current_artwork = None
        self.pixmap = None
        self.angle = 0
        if artwork is None:
            self.update_rotation_controls()
            return
        self.current_artwork = artwork
        path = Path(artwork['local_path'])
        self.update_provenance()
        self.provenance.setToolTip(artwork['url'])
        try:
            if path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError('画像が大きすぎます')
            reader = QImageReader(str(path))
            size = reader.size()
            if not size.isValid() or max(size.width(), size.height()) > 8192:
                raise ValueError('画像サイズを確認できません')
            reader.setScaledSize(size.scaled(QSize(1600, 1600), Qt.AspectRatioMode.KeepAspectRatio))
            image = reader.read()
            if image.isNull():
                raise ValueError('画像を読み込めません')
            self.pixmap = QPixmap.fromImage(image)
            self.draw_cover()
        except (OSError, ValueError) as error:
            self.cover.setPixmap(QPixmap())
            self.cover.setText(str(error))
        self.update_rotation_controls()

    def draw_cover(self):
        if self.pixmap:
            display = self.pixmap.transformed(QTransform().rotate(self.angle), Qt.TransformationMode.SmoothTransformation)
            self.cover.setPixmap(display.scaled(self.cover.size() - QSize(20, 20),
                                 Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def rotate(self, degrees):
        self.angle = (self.angle + degrees) % 360
        self.draw_cover()
        self.update_provenance()
        self.update_rotation_controls()

    def update_rotation_controls(self):
        self.save_button.setEnabled(self.task is None and self.current_game is not None
                                    and bool(self.title_edit.text().strip() or self.current_artwork))

    def update_provenance(self):
        artwork = self.current_artwork
        if artwork:
            state = ' · 回転%d°（未保存）' % self.angle if self.angle else (' · 補正済み' if artwork.get('rotation_degrees') else '')
            self.provenance.setText('%s / 地域 %s%s\n%s' % (artwork['source'], artwork['region'], state,
                                    Path(artwork['local_path']).name))

    def save_game(self):
        if not self.current_game or self.task is not None:
            return
        game_id, title, angle = self.current_game['id'], self.title_edit.text(), self.angle
        artwork = self.current_artwork.copy() if self.current_artwork else None
        output = Path(self.db_path).parent / 'artwork-edits' / ('game-%d' % game_id)
        def save(cancelled, progress):
            rotation = None
            if artwork:
                if angle:
                    from artwork.orientation import rotate_png
                    rotation = rotate_png(artwork['local_path'], artwork['sha256'], angle, output, cancelled=cancelled)
                elif inspect_png(Path(artwork['local_path'])) != artwork['sha256']:
                    raise ValueError('取得後に画像が変更されています。再度取り込んで確認してください。')
            if cancelled():
                raise InterruptedError('保存を中断しました')
            return with_db(self.db_path, lambda db: db.save_game_review(
                game_id, title, artwork['id'] if artwork else None, rotation))
        def done(artwork_id):
            self.desired_artwork_id = artwork_id
            self.saved('名前・画像をPC内に保存しました。')
        self.start_task(save, done, '名前と画像を保存中…')

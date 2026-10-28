"""Exact quarter-turn PNG copies for the optional Qt GUI; originals stay intact."""
import hashlib
import os
from pathlib import Path
import tempfile

from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PyQt6.QtGui import QImage, QTransform

from .cache import MAX_BYTES, valid_png


def rotate_png(source, expected_sha256, degrees, output_folder, *, cancelled=lambda: False):
    if type(degrees) is not int or degrees % 360 not in (90, 180, 270):
        raise ValueError('補正角度は90・180・270度を指定してください')
    angle = degrees % 360

    def check_cancelled():
        if cancelled():
            raise InterruptedError('画像の補正保存を中断しました')

    check_cancelled()
    source = Path(source).resolve()
    with source.open('rb') as stream:
        original = stream.read(MAX_BYTES + 1)
    if hashlib.sha256(original).hexdigest() != expected_sha256 or not valid_png(original):
        raise ValueError('取得後に元画像が変更されたか、PNGが不正です。再取り込みしてください。')
    image = QImage.fromData(original, 'PNG')
    if image.isNull():
        raise ValueError('元画像をデコードできません')
    check_cancelled()
    corrected = image.transformed(QTransform().rotate(angle), Qt.TransformationMode.FastTransformation)
    buffer_data = QByteArray()
    buffer = QBuffer(buffer_data)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not corrected.save(buffer, 'PNG'):
        raise ValueError('補正画像をPNGとして保存できません')
    buffer.close()
    data = bytes(buffer_data)
    if not valid_png(data):
        raise ValueError('補正PNGが検証に失敗しました（上限16 MiB）')
    check_cancelled()
    folder = Path(output_folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / ('%s-r%d-%s.png' % (source.stem[:70], angle, expected_sha256[:20]))
    if target == source:
        raise ValueError('元画像は上書きできません')
    if target.exists():
        if target.is_symlink() or target.read_bytes() != data:
            raise ValueError('同名の補正画像が既に存在し、内容が異なります。上書きしません。')
    else:
        handle, temporary = tempfile.mkstemp(dir=folder, suffix='.png')
        try:
            with os.fdopen(handle, 'wb') as stream:
                stream.write(data)
            check_cancelled()
            try:
                # Atomic publish without replacing any pre-existing file.
                os.link(temporary, target)
            except FileExistsError:
                if target.is_symlink() or target.read_bytes() != data:
                    raise ValueError('補正画像の保存先が競合しました。上書きしません。') from None
        finally:
            Path(temporary).unlink(missing_ok=True)
    return dict(path=str(target), sha256=hashlib.sha256(data).hexdigest(),
                width=corrected.width(), height=corrected.height(),
                rotation_degrees=angle, original_sha256=expected_sha256)

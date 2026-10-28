import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@unittest.skipUnless(importlib.util.find_spec('PyQt6'), 'optional PyQt6 is not installed')
class OrientationTests(unittest.TestCase):
    def test_full_resolution_pixel_rotation_and_original_preservation(self):
        from PyQt6.QtGui import QImage, QColor
        from artwork.orientation import rotate_png
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source.png'
            image = QImage(3, 2, QImage.Format.Format_ARGB32)
            image.fill(QColor('transparent'))
            image.setPixelColor(0, 0, QColor('red'))
            image.setPixelColor(2, 1, QColor('blue'))
            self.assertTrue(image.save(str(source), 'PNG'))
            original = source.read_bytes()
            digest = hashlib.sha256(original).hexdigest()
            result = rotate_png(source, digest, 90, root / 'edited')
            rotated = QImage(result['path'])
            self.assertEqual((rotated.width(), rotated.height()), (2, 3))
            self.assertEqual(rotated.pixelColor(1, 0), QColor('red'))
            self.assertEqual(rotated.pixelColor(0, 2), QColor('blue'))
            self.assertEqual(rotated.pixelColor(0, 0).alpha(), 0)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(rotate_png(source, digest, 90, root / 'edited')['path'], result['path'])
            Path(result['path']).write_bytes(b'existing-user-data')
            with self.assertRaises(ValueError):
                rotate_png(source, digest, 90, root / 'edited')
            self.assertEqual(Path(result['path']).read_bytes(), b'existing-user-data')

    def test_changed_source_invalid_angle_and_cancel_do_not_write(self):
        from PyQt6.QtGui import QImage
        from artwork.orientation import rotate_png
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source.png'
            image = QImage(2, 3, QImage.Format.Format_ARGB32)
            image.fill(0)
            image.save(str(source), 'PNG')
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            for angle in (0, 45):
                with self.assertRaises(ValueError):
                    rotate_png(source, digest, angle, root / 'edited')
            with self.assertRaises(InterruptedError):
                rotate_png(source, digest, 90, root / 'edited', cancelled=lambda: True)
            with self.assertRaises(ValueError):
                rotate_png(source, 'wrong', 90, root / 'edited')
            self.assertFalse((root / 'edited').exists())


if __name__ == '__main__':
    unittest.main()

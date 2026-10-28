import hashlib
import json
import os
import struct
import tempfile
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from urllib.parse import urlsplit

MAX_BYTES = 16 * 1024 * 1024


def validate_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname != 'thumbnails.libretro.com'
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError('Only the public HTTPS Libretro thumbnail host is enabled')


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def fetch_image(url):
    validate_url(url)
    opener = urllib.request.build_opener(SafeRedirect())
    for attempt in range(3):
        try:
            with opener.open(urllib.request.Request(url, headers={'User-Agent': 'mister-jp-library/0.1'}), timeout=15) as response:
                data = response.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise ValueError('Image exceeds 16 MiB')
                return data
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            if code not in (500, 502, 503, 504) or attempt == 2:
                raise RuntimeError('Artwork HTTP request failed (status %s)' % code) from None
        except (OSError, urllib.error.URLError):
            if attempt == 2:
                raise RuntimeError('Artwork download failed or timed out') from None
        time.sleep(0.5 * (2 ** attempt))


def valid_png(data):
    if len(data) > MAX_BYTES or not data.startswith(b'\x89PNG\r\n\x1a\n'):
        return False
    pos, first, image_data = 8, True, False
    while pos + 12 <= len(data):
        length = struct.unpack('>I', data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        end = pos + 12 + length
        if end > len(data):
            return False
        payload = data[pos + 8:pos + 8 + length]
        checksum = struct.unpack('>I', data[end - 4:end])[0]
        if zlib.crc32(kind + payload) & 0xffffffff != checksum:
            return False
        if first:
            if kind != b'IHDR' or length != 13:
                return False
            width, height = struct.unpack('>II', payload[:8])
            if not (0 < width <= 8192 and 0 < height <= 8192):
                return False
            first = False
        if kind == b'IDAT':
            image_data = True
        if kind == b'IEND':
            return image_data and length == 0 and end == len(data)
        pos = end
    return False


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ImageCache:
    def __init__(self, root, fetch=fetch_image, validator=validate_url):
        self.root, self.fetch = Path(root), fetch
        self.validator = validator
        self.root.mkdir(parents=True, exist_ok=True)

    def get(self, url):
        self.validator(url)
        key = hashlib.sha256(url.encode('utf-8')).hexdigest()
        path = self.root / (key + '.png')
        metadata = self.root / (key + '.json')
        if path.is_file() and metadata.is_file():
            data = path.read_bytes()
            try:
                info = json.loads(metadata.read_text(encoding='utf-8'))
                if info.get('url') == url and info.get('sha256') == hashlib.sha256(data).hexdigest() and valid_png(data):
                    return path
            except (ValueError, UnicodeError):
                pass
        data = self.fetch(url)
        if not valid_png(data):
            raise ValueError('Downloaded data is not a structurally valid PNG')
        atomic_write(path, data)
        atomic_write(metadata, json.dumps(dict(url=url, sha256=hashlib.sha256(data).hexdigest())).encode('utf-8'))
        return path

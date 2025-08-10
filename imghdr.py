# Minimal imghdr shim for Python 3.13+ (backward compat for PTB 13.x)
import os

def _read32(file, n=32):
    if isinstance(file, (str, bytes, os.PathLike)):
        with open(file, 'rb') as f:
            return f.read(n)
    pos = file.tell()
    try:
        return file.read(n)
    finally:
        file.seek(pos)

def what(file, h=None):
    h = h or _read32(file)
    if not h:
        return None
    if h[:3] == b'\\xff\\xd8\\xff':   # JPEG
        return 'jpeg'
    if h.startswith(b'\\x89PNG\\r\\n\\x1a\\n'):
        return 'png'
    if h[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    if h[:4] in (b'MM\\x00\\x2a', b'II\\x2a\\x00'):
        return 'tiff'
    if h[:2] == b'BM':
        return 'bmp'
    return None

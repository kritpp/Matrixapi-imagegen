"""Inspect original GIF containers without decoding/re-encoding their frames."""
from pathlib import Path
import struct


def inspect_gif(path):
    """Count complete image frames and durations; reject a truncated container."""
    with Path(path).open('rb') as stream:
        def read(size):
            value = stream.read(size)
            if len(value) != size:
                raise ValueError('Truncated GIF')
            return value

        def blocks():
            while True:
                count = read(1)[0]
                if not count:
                    return
                yield read(count)

        if read(6) not in (b'GIF87a', b'GIF89a'):
            raise ValueError('Not a GIF')
        width, height, packed, _, _ = struct.unpack('<HHBBB', read(7))
        if not width or not height:
            raise ValueError('Invalid GIF dimensions')
        if packed & 0x80:
            read(3 * (2 ** ((packed & 7) + 1)))
        durations = []
        delay = 0
        loop = None
        while True:
            marker = read(1)
            if marker == b';':
                break
            if marker == b'!':
                label = read(1)
                if label == b'\xf9':
                    if read(1) != b'\x04':
                        raise ValueError('Invalid GIF graphics control block')
                    control = read(4)
                    delay = int.from_bytes(control[1:3], 'little') * 10
                    if read(1) != b'\x00':
                        raise ValueError('Invalid GIF graphics control terminator')
                elif label == b'\xff':
                    application = read(read(1)[0])
                    for block in blocks():
                        if application in (b'NETSCAPE2.0', b'ANIMEXTS1.0') and len(block) >= 3 and block[0] == 1:
                            loop = int.from_bytes(block[1:3], 'little')
                else:
                    for _ in blocks():
                        pass
            elif marker == b',':
                descriptor = read(9)
                if descriptor[8] & 0x80:
                    read(3 * (2 ** ((descriptor[8] & 7) + 1)))
                read(1)  # LZW minimum code size
                for _ in blocks():
                    pass
                durations.append(delay)
                delay = 0
            else:
                raise ValueError('Invalid GIF block')
        if not durations:
            raise ValueError('GIF contains no frames')
        return {'format':'gif', 'width':width, 'height':height,
                'frame_count':len(durations), 'animated':len(durations)>1,
                'frame_durations_ms':durations, 'duration_ms':sum(durations), 'loop':loop}


def media_metadata(paths):
    result = []
    for value in paths:
        path = Path(value)
        row = {'path':path.resolve().as_posix(), 'format':path.suffix.lower().lstrip('.')}
        if path.suffix.lower() == '.gif':
            try:
                row.update(inspect_gif(path))
            except (ValueError, OSError) as exc:
                row.update({'animated':False, 'frame_count':0, 'inspection_error':str(exc)})
        result.append(row)
    return result

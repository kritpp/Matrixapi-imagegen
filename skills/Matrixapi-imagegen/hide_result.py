#!/usr/bin/env python3
"""Hide a delivered result JSON in Windows Explorer after a short delay."""

from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path
import time


FILE_ATTRIBUTE_HIDDEN = 0x2
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF


def hide_file(path: Path) -> bool:
    if os.name != "nt" or not path.is_file():
        return False
    kernel32 = ctypes.windll.kernel32
    get_attributes = kernel32.GetFileAttributesW
    set_attributes = kernel32.SetFileAttributesW
    get_attributes.argtypes = [ctypes.c_wchar_p]
    get_attributes.restype = ctypes.c_uint32
    set_attributes.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    set_attributes.restype = ctypes.c_int
    attributes = get_attributes(str(path))
    if attributes == INVALID_FILE_ATTRIBUTES:
        return False
    return bool(set_attributes(str(path), attributes | FILE_ATTRIBUTE_HIDDEN))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--delay-ms", type=int, default=10_000)
    args = parser.parse_args()
    if args.delay_ms < 0 or args.delay_ms > 60_000:
        parser.error("--delay-ms must be between 0 and 60000")
    if args.delay_ms:
        time.sleep(args.delay_ms / 1000)
    hide_file(args.path.expanduser().resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

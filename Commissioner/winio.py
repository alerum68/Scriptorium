"""
Commissioner/winio.py -- Windows file-lock retry IO utilities.

Consolidated retry wrappers for filesystem operations (move, read, unlink, replace)
that are subject to transient Windows file-lock errors (e.g. from browser downloads,
antivirus scanning, or lingering file handles).
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import time

__all__ = [
    "move_with_retry",
    "read_text_with_retry",
    "unlink_with_retry",
    "replace_with_retry",
]


def move_with_retry(src: Path, dst: Path, attempts: int = 5, delay: float = 0.5,
                    on_collision: str = "overwrite") -> str:
    """Moves src to dst, retrying on transient Windows file-lock errors. Returns 'moved' or
    'skipped' (when on_collision='skip' and dst already exists - src is discarded either way,
    since a skipped download is never needed again)."""
    src = Path(src)
    dst = Path(dst)
    if on_collision == "skip" and dst.exists():
        src.unlink(missing_ok=True)
        return "skipped"
    for attempt in range(1, attempts + 1):
        try:
            shutil.move(str(src), str(dst))
            return "moved"
        except OSError as e:
            if attempt == attempts:
                print(f"[ERROR] Could not move {src.name} to {dst} after {attempts} attempts: {e}")
                raise
            time.sleep(delay)
    return "moved"


def read_text_with_retry(path: Path, attempts: int = 5, delay: float = 0.5,
                         encoding: str = "utf-8") -> str:
    """Reads text from path, retrying on transient Windows file locks."""
    path = Path(path)
    for attempt in range(1, attempts + 1):
        try:
            return path.read_text(encoding=encoding)
        except OSError:
            if attempt == attempts:
                raise
            time.sleep(delay)
    return ""


def unlink_with_retry(path: Path, attempts: int = 5, delay: float = 0.5,
                      missing_ok: bool = True) -> None:
    """Deletes a file, retrying on transient Windows file locks."""
    path = Path(path)
    for attempt in range(1, attempts + 1):
        try:
            path.unlink(missing_ok=missing_ok)
            return
        except OSError:
            if attempt == attempts:
                raise
            time.sleep(delay)


def replace_with_retry(src: Path, dst: Path, attempts: int = 5, delay: float = 0.05) -> None:
    """Replaces dst with src atomically, retrying on transient Windows PermissionErrors."""
    src = Path(src)
    dst = Path(dst)
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay * (attempt + 1))

"""Commissioner JSON I/O utilities.

Provides atomic JSON writing (temp file + rename) and crash-safe checkpoint loading/saving.
"""

import json
from pathlib import Path
from typing import Any, Union

__all__ = ["atomic_write_json", "load_checkpoint", "save_checkpoint"]


def atomic_write_json(
    path: Union[str, Path],
    data: Any,
    indent: int = 2,
    ensure_ascii: bool = False,
    **kwargs: Any,
) -> None:
    """Writes data to path as JSON via a temporary file + atomic rename, so a crash
    or interruption mid-write never leaves a corrupted or truncated file at the final path.
    On any failure the temp file is removed and the original exception re-raised."""
    target = Path(path)
    if str(target.parent) and not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(f"{target}.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii, **kwargs)
        tmp_path.replace(target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def load_checkpoint(
    checkpoint_path: Union[str, Path],
    default: Any = None,
) -> Any:
    """Loads JSON data from a checkpoint file. Returns default if the file is missing
    or unparseable."""
    p = Path(checkpoint_path)
    if not p.exists():
        return default
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] Failed to read checkpoint {checkpoint_path}: {e}")
        return default


def save_checkpoint(
    checkpoint_path: Union[str, Path],
    data: Any,
    indent: int = 2,
    ensure_ascii: bool = False,
    **kwargs: Any,
) -> None:
    """Saves checkpoint data atomically to a JSON file."""
    atomic_write_json(checkpoint_path, data, indent=indent, ensure_ascii=ensure_ascii, **kwargs)

"""Publish complete JSON receipts without retrying the work they describe."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any


def atomic_write_json(path: Path, value: Any, *, exclusive: bool = False) -> None:
    """Publish a same-directory temporary file, retrying file locks for at most 3s.

    Initial receipts use a hard link to atomically refuse an existing destination.
    Later snapshots replace that destination only after the whole JSON is closed.
    """
    path = Path(path)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        deadline = time.monotonic() + 3.0
        while True:
            try:
                if exclusive:
                    os.link(temporary, path)
                else:
                    os.replace(temporary, path)
                return
            except PermissionError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(0.05, remaining))
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            # A scanner may still hold the temporary file; retain the actual
            # publication error and never touch the previous receipt to clean up.
            pass

"""Immutable original bytes and deterministic text decoding."""

import hashlib
import os
import tempfile
from pathlib import Path


def decode_source(raw_bytes: bytes) -> str:
    return raw_bytes.decode("utf-8-sig", errors="strict").replace("\r\n", "\n").replace("\r", "\n")


def save_snapshot(raw_bytes: bytes, snapshot_root: Path) -> tuple[str, Path]:
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    snapshot_root.mkdir(parents=True, exist_ok=True)
    target = snapshot_root / f"{content_hash}.md"
    if target.exists():
        if target.read_bytes() != raw_bytes:
            raise ValueError("snapshot hash collision or corruption")
        return content_hash, target
    descriptor, temporary_path = tempfile.mkstemp(prefix=".snapshot-", dir=snapshot_root)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw_bytes)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, target)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
    return content_hash, target

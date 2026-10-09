from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_bytes(p: Path, data: bytes) -> None:
    """Write to a temp file in the same directory, fsync, then os.replace: readers never see a partial file."""
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def write_json(p: Path, obj) -> None:
    atomic_write_bytes(Path(p), json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"))


def read_json(p: Path):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", s).strip("_")[:60] or "doc"

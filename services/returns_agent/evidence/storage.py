"""Evidence file storage. Production uses S3-compatible storage (MinIO in dev); the local
and in-memory stores implement the same interface for development and tests."""

from pathlib import Path
from typing import Protocol


class EvidenceStore(Protocol):
    def put(self, key: str, data: bytes) -> str: ...
    def get(self, uri: str) -> bytes: ...


class LocalEvidenceStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def put(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"local://{key}"

    def get(self, uri: str) -> bytes:
        return self._path(uri.removeprefix("local://")).read_bytes()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError("evidence key escapes the store")
        return path


class MemoryEvidenceStore:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def put(self, key: str, data: bytes) -> str:
        self.files[key] = data
        return f"mem://{key}"

    def get(self, uri: str) -> bytes:
        return self.files[uri.removeprefix("mem://")]

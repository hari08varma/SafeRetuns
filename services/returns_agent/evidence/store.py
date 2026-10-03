"""Evidence storage. Local filesystem for development and tests; an S3/MinIO store implements
the same two methods for production."""

from pathlib import Path
from typing import Protocol


class EvidenceStore(Protocol):
    def put(self, key: str, data: bytes) -> str:
        """Stores the bytes and returns the uri."""
        ...

    def get(self, uri: str) -> bytes: ...


class LocalEvidenceStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def put(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"file://{key}"

    def get(self, uri: str) -> bytes:
        return self._path(uri.removeprefix("file://")).read_bytes()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):  # keys are generated, but never trust paths
            raise ValueError("evidence key escapes the store")
        return path

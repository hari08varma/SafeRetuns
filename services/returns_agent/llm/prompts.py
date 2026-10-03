"""Versioned prompt files in config/prompts. Each file starts with `<!-- version: N -->`;
the version and a content hash are recorded with every LLM call."""

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache

from returns_agent.config import config_dir

_HEADER = re.compile(r"^<!--\s*version:\s*([\w.-]+)\s*-->\s*\n")


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    text: str

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()[:16]

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"


@lru_cache
def load_prompt(name: str) -> Prompt:
    raw = (config_dir() / "prompts" / f"{name}.md").read_text(encoding="utf-8")
    match = _HEADER.match(raw)
    if not match:
        raise ValueError(f"prompt {name} is missing its '<!-- version: N -->' header")
    return Prompt(name=name, version=match.group(1), text=raw[match.end() :].strip())

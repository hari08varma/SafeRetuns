"""Remove PII before text reaches an LLM; restore it only in customer-facing output.

Known values (the customer's name, phone, email, address) are replaced exactly; patterns
catch anything else that looks like contact or payment data.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("UPI", re.compile(r"\b[\w.-]{2,}@[a-zA-Z]{2,}\b")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("IFSC", re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")),
    ("PHONE", re.compile(r"(?:\+?91[\s-]?)?\b[6-9]\d{4}[\s-]?\d{5}\b")),
    ("ACCOUNT", re.compile(r"\b\d{9,18}\b")),
]


@dataclass
class Redactor:
    known: dict[str, str] = field(default_factory=dict)  # raw value -> label (NAME, ADDRESS...)
    _forward: dict[str, str] = field(default_factory=dict)
    _reverse: dict[str, str] = field(default_factory=dict)

    def _token(self, label: str, value: str) -> str:
        if value not in self._forward:
            count = sum(1 for t in self._reverse if t.startswith(f"<{label}_")) + 1
            token = f"<{label}_{count}>"
            self._forward[value], self._reverse[token] = token, value
        return self._forward[value]

    def _replacer(self, label: str) -> Callable[[re.Match[str]], str]:
        return lambda m: self._token(label, m.group(0))

    def redact(self, text: str) -> str:
        for value, label in sorted(self.known.items(), key=lambda kv: -len(kv[0])):
            if value:
                text = re.sub(re.escape(value), self._replacer(label), text, flags=re.IGNORECASE)
        for label, pattern in _PATTERNS:
            text = pattern.sub(self._replacer(label), text)
        return text

    @property
    def tokens(self) -> frozenset[str]:
        """Placeholders this redactor created (the only ones restore can fill)."""
        return frozenset(self._reverse)

    def restore(self, text: str) -> str:
        for token, value in self._reverse.items():
            text = text.replace(token, value)
        return text

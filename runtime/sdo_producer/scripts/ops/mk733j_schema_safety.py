"""Small exact-schema and private-payload helpers for MK733J authority data."""
from __future__ import annotations

import re
from typing import Any

# Keys are normalized so snake/camel/kebab variants cannot evade the boundary.
SENSITIVE_NORMALIZED_KEYS = {
    "rawprompt", "prompt", "transcript", "rawtranscript",
    "hiddenreasoning", "hiddenchainofthought", "hiddencot", "chainofthought",
    "secret", "credential", "credentials", "token", "apikey", "accesstoken",
}


def normalized_key(value: Any) -> str:
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(value))
    return "".join(ch for ch in snake.lower() if ch.isalnum())


def contains_sensitive_key(value: Any) -> bool:
    """True when an authority artifact recursively names a private payload."""
    if isinstance(value, dict):
        return any(
            normalized_key(key) in SENSITIVE_NORMALIZED_KEYS or contains_sensitive_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(contains_sensitive_key(item) for item in value)
    return False


def exact_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value)

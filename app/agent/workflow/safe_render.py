"""Neutral scalar projection shared by deterministic user-facing renderers."""

from __future__ import annotations

import unicodedata


def sanitize_external_scalar(value, *, limit: int = 1000, secrets_to_remove=()) -> str:
    """Bound one external scalar and neutralize controls, bidi and active Markdown tokens."""
    rendered = str(value or "")
    for secret in secrets_to_remove or ():
        secret = str(secret or "")
        if secret:
            rendered = rendered.replace(secret, "redacted")
    mapped = {"#": "＃", "|": "｜", "{": "｛", "}": "｝", "[": "［", "]": "］",
              "<": "＜", ">": "＞", "`": "", "\\": "＼"}
    chars = []
    for character in rendered:
        category = unicodedata.category(character)
        if category in {"Cc", "Cf"}:
            chars.append(" " if character in "\r\n\t" else "")
            continue
        chars.append(mapped.get(character, character))
    return " ".join("".join(chars).split())[:limit].strip()


__all__ = ["sanitize_external_scalar"]

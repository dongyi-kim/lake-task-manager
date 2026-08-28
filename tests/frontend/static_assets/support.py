"""Shared paths and collections for static frontend contract tests."""
from __future__ import annotations

import re
from pathlib import Path

from support.paths import REPO_ROOT, STATIC_ROOT

STATIC = STATIC_ROOT
ROOT = REPO_ROOT
ASSETS = sorted(list(STATIC.rglob("*.js")) + list(STATIC.rglob("*.css")))
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
OURS = [p for p in ASSETS if p.suffix == ".js" and "vendor" not in p.parts]
VUE_COMPONENTS = [p for p in ASSETS if p.suffix == ".js" and "components" in p.parts]


def asset_id(path: Path) -> str:
    return str(path.relative_to(STATIC.parent.parent))


def comment_editor_source() -> str:
    """Return the editor facade plus its feature modules as one contract surface."""
    paths = [STATIC / "components" / "ui" / "CommentEditor.js"]
    paths.extend(sorted((STATIC / "components" / "editor").glob("*.js")))
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def agent_view_source() -> str:
    """Return the AgentView facade plus its feature modules as one contract surface."""
    paths = [STATIC / "components" / "views" / "AgentView.js"]
    paths.extend(sorted((STATIC / "components" / "agent").glob("*.js")))
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def template_literal(source: str) -> str | None:
    """Read inline Vue templates and extracted ``*_TEMPLATE`` module constants."""
    masked = source.replace("\\`", "\0")
    match = re.search(
        r"(?:template:\s*|const\s+[A-Z][A-Z0-9_]*TEMPLATE\s*=\s*)`",
        masked,
    )
    if not match:
        return None
    end = masked.find("`", match.end())
    return None if end < 0 else masked[match.end():end]

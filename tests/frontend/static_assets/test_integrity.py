"""Read each asset once for mechanical checks; keep optional JS parsing independent."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from frontend.static_assets.support import ASSETS, CTRL_RE, OURS, asset_id, template_literal


def test_static_assets_exist():
    assert len(ASSETS) > 20, "정적 자산을 못 찾았다 — 경로 규약이 바뀌었나"


@pytest.mark.parametrize("path", ASSETS, ids=asset_id)
def test_static_asset_hygiene(path: Path):
    """Report every applicable rule for a file, including files without a Vue template."""
    src = path.read_text(encoding="utf-8")
    violations = []
    # A literal backspace changes a regex's meaning without causing a syntax error.
    controls = [(src[:m.start()].count("\n") + 1, hex(ord(m.group())))
                for m in CTRL_RE.finditer(src)]
    if controls:
        violations.append(f"control characters: {controls[:5]}")

    if path.suffix == ".js":
        # Inline handlers silently fail under CSP; use addEventListener instead.
        handlers = re.findall(r"\bon(?:error|load|click|change|input)\s*=\s*[\"']", src)
        if handlers:
            violations.append(f"inline event handlers: {handlers[:3]}")
        if "vendor" not in path.parts:
            backticks = []
            for match in re.finditer(r"<!--.*?-->", src, re.S):
                body = match.group(0)
                if "`" in body.replace("\\`", ""):
                    backticks.append((src[:match.start()].count("\n") + 1,
                                      body[:70].replace("\n", " ")))
            if backticks:
                violations.append(f"raw backticks in HTML comments: {backticks[:3]}")

        body = template_literal(src) if "components" in path.parts else None
        if body is not None:
            # Vue expressions can access instance properties, not imported modules.
            mods = re.findall(r"^import\s+(?:\{\s*([\w,\s]+)\s*\}|(\w+))\s+from", src, re.M)
            names = {n.strip() for a, b in mods for n in (a or b or "").split(",") if n.strip()}
            names -= {"h", "ref", "computed"}
            imported_calls = [match.group(0)[:60] for name in sorted(names)
                              for match in re.finditer(
                                  r'[@:]?[\w.-]+="[^"]*\b' + re.escape(name) + r"\.\w", body)]
            if imported_calls:
                violations.append(f"template calls imported modules: {imported_calls[:3]}")
            # Vue tolerates an extra closing div, which can silently break layout.
            if "vendor" not in path.parts:
                markup = re.sub(r"<!--.*?-->", "", body, flags=re.S)
                opens = len(re.findall(r"<div\b", markup))
                closes = len(re.findall(r"</div>", markup))
                if opens != closes:
                    violations.append(f"unbalanced div tags: {opens} opening / {closes} closing")

    assert not violations, f"{asset_id(path)}:\n" + "\n".join(violations)


@pytest.mark.parametrize("path", OURS, ids=asset_id)
def test_javascript_parses(path: Path):
    """Missing esprima may skip parsing locally, but never skips the hygiene checks."""
    esprima = pytest.importorskip("esprima")
    src = path.read_text(encoding="utf-8")
    # esprima predates ES2020; preserve the existing optional-chain/nullish normalization.
    src = src.replace("?.(", "(").replace("?.[", "[").replace("?.", ".").replace("??", "||")
    try:
        esprima.parseModule(src)
    except Exception as exc:  # noqa: BLE001 — report parser errors against the source asset
        pytest.fail(f"{asset_id(path)} 파싱 실패: {exc}")

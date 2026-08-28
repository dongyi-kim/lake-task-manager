"""Mechanical source checks for Agent and test tooling."""
from __future__ import annotations

import pytest

from frontend.static_assets.support import CTRL_RE
from support.paths import REPO_ROOT

# ── 파이썬 소스 위생 ────────────────────────────────────────────────────────
# 같은 편집 사고의 파이썬 판. heredoc 으로 소스를 고치면 줄바꿈이 **공백으로 뭉개져**
# `if a  <공백 17칸>  and b:` 같은 줄이 남는다. 문법은 멀쩡해서 테스트도 전부 통과하고
# 리뷰에서도 넘어가지만, 그 줄은 아무도 다시 읽지 못한다(실측 5건).
_ROOT = REPO_ROOT
AGENT_PY = sorted((_ROOT / "app" / "agent").rglob("*.py"))
# ★ **배터리 소스도 같은 검사를 받는다.** 여기 0x08 이 박히면 체커의 정규식이 조용히
#   달라져 **무엇을 재는지가 바뀐다** — 실측: `DL-` 이 `DL-` 로 박혀 사람 조사
#   케이스 두 건이 통과할 답에도 FAIL 로 떨어졌다. app/agent 만 보고 tools/ 를 안 봐서
#   pytest 1035 이 초록인 채로 지나갔다 — 가드가 사고가 난 자리를 안 덮고 있었다.
TOOLS_PY = sorted((_ROOT / "tools").glob("agent_*.py"))


def _code_only(src: str) -> list:
    """문자열·주석 **내용**을 지운 줄 목록. 리터럴 안의 정렬 공백은 정상이다."""
    import io
    import token as T
    import tokenize
    lines = src.splitlines()
    masked = list(lines)
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type not in (T.STRING, T.COMMENT):
            continue
        (r1, c1), (r2, c2) = tok.start, tok.end
        if r1 == r2:
            ln = masked[r1 - 1]
            masked[r1 - 1] = ln[:c1] + "X" * (c2 - c1) + ln[c2:]
        else:
            for r in range(r1, r2 + 1):
                masked[r - 1] = ""
    return masked


@pytest.mark.parametrize("path", AGENT_PY, ids=lambda p: p.name)
def test_agent_source_has_no_collapsed_newlines(path):
    src = path.read_text(encoding="utf-8")
    bad = [(i, raw.strip()[:90])
           for i, (raw, m) in enumerate(zip(src.splitlines(), _code_only(src)), 1)
           if len(raw) > 100 and m.strip() and "      " in m.lstrip()]
    assert not bad, (
        f"{path.name} 에 줄바꿈이 공백으로 뭉개진 코드 줄이 있다 — "
        + "; ".join(f"L{i}: {s}" for i, s in bad)
        + " (heredoc 대신 Edit 도구로 고칠 것)")


@pytest.mark.parametrize("path", AGENT_PY + TOOLS_PY, ids=lambda p: p.name)
def test_agent_source_has_no_control_chars(path):
    hit = CTRL_RE.search(path.read_text(encoding="utf-8"))
    assert not hit, (f"{path.name} 에 제어문자 0x{ord(hit.group()):02x} 가 박혔다 — "
                     r"정규식의 \b 가 백스페이스로 변한 그 사고다")

"""정적 자산(JS/CSS)의 **기계적 결함**을 커밋 전에 잡는다.

브라우저가 없어도 잡을 수 있는 것들이다. 렌더가 조용히 깨지는 사고가 반복됐고
(정규식의 `\\b` 가 편집 과정에서 **백스페이스 문자(0x08)** 로 파일에 박혀 매칭이
영구히 실패한 실측 사고), 그런 것은 눈으로 코드를 봐도 보이지 않는다 —
파일에 그대로 있는 것처럼 보이기 때문이다. 그래서 바이트로 검사한다.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
ASSETS = sorted(list(STATIC.rglob("*.js")) + list(STATIC.rglob("*.css")))

# 소스에 있어서는 안 되는 제어문자 — 탭·개행·CR 만 허용한다.
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _rel(p: Path) -> str:
    return str(p.relative_to(STATIC.parent.parent))


def test_static_assets_exist():
    assert len(ASSETS) > 20, "정적 자산을 못 찾았다 — 경로 규약이 바뀌었나"


@pytest.mark.parametrize("path", ASSETS, ids=_rel)
def test_no_control_characters(path: Path):
    """제어문자 0개. 특히 0x08 — `\\b` 를 쓴 정규식이 편집 도구를 거치며 박히는 사고가
    있었다(실측). 파일을 열어 봐도 정상으로 보이는데 정규식은 절대 매칭되지 않는다."""
    src = path.read_text(encoding="utf-8")
    hits = [(src[:m.start()].count("\n") + 1, hex(ord(m.group())))
            for m in CTRL_RE.finditer(src)]
    assert not hits, f"{_rel(path)} 에 제어문자: {hits[:5]}"


OURS = [p for p in ASSETS if p.suffix == ".js" and "vendor" not in p.parts]


@pytest.mark.parametrize("path", OURS, ids=_rel)
def test_javascript_parses(path: Path):
    """우리 JS 가 **문법적으로 성립**하는가. 문자열 치환으로 파일을 고치다 보면 토막이
    남아 파일 전체가 죽는데, 그러면 화면이 통째로 비고 원인은 콘솔에만 남는다.
    (esprima 가 없는 환경에서는 건너뛴다 — 개발 의존성이다.)"""
    esprima = pytest.importorskip("esprima")
    src = path.read_text(encoding="utf-8")
    # esprima 는 ES2020 이전까지만 안다 — optional chaining·nullish 를 동등한 옛 문법으로
    # 낮춰 준다(구조 검증이 목적이지 문법 감시가 목적이 아니다).
    src = src.replace("?.(", "(").replace("?.[", "[").replace("?.", ".").replace("??", "||")
    try:
        esprima.parseModule(src)
    except Exception as e:                       # noqa: BLE001 — 파서가 뭘 던지든 실패다
        pytest.fail(f"{_rel(path)} 파싱 실패: {e}")


@pytest.mark.parametrize("path", [p for p in ASSETS if p.suffix == ".js"], ids=_rel)
def test_no_inline_event_handlers(path: Path):
    """인라인 이벤트 핸들러 금지 — CSP 에서 막히면 **조용히** 동작하지 않는다
    (프사 실패를 숨기는 onerror 가 안 돌아 깨진 이미지가 그대로 남았다 — 실측).
    리스너는 코드에서 addEventListener 로 붙인다."""
    src = path.read_text(encoding="utf-8")
    hits = re.findall(r"\bon(?:error|load|click|change|input)\s*=\s*[\"']", src)
    assert not hits, f"{_rel(path)} 인라인 핸들러: {hits[:3]}"


VUE_COMPONENTS = [p for p in ASSETS
                  if p.suffix == ".js" and "components" in p.parts]


@pytest.mark.parametrize("path", VUE_COMPONENTS, ids=_rel)
def test_templates_do_not_call_imported_modules(path: Path):
    """Vue 템플릿은 **컴포넌트 인스턴스 프로퍼티만** 본다. 템플릿 표현식에서 import 한
    모듈(agentApi·api…)을 부르면 예외도 없이 **조용히 아무 일도 일어나지 않는다**.

    실측: 설정 창을 닫을 때 `@close="… agentApi.status() …"` 로 모델 표시를 갱신하게
    해 뒀는데 한 번도 실행되지 않아, 모델을 바꿔도 좌상단이 옛 값 그대로였다.
    """
    src = path.read_text(encoding="utf-8")
    m = re.search(r"template:\s*`", src)
    if not m:
        pytest.skip("템플릿 없음")
    tpl = src[m.end():]
    mods = re.findall(r"^import\s+(?:\{\s*([\w,\s]+)\s*\}|(\w+))\s+from", src, re.M)
    names = {n.strip() for a, b in mods for n in (a or b or "").split(",") if n.strip()}
    names -= {"h", "ref", "computed"}          # 렌더 함수용 — 템플릿과 무관
    bad = []
    for name in names:
        for mm in re.finditer(r'[@:]?[\w.-]+="[^"]*\b' + re.escape(name) + r"\.\w", tpl):
            bad.append(mm.group(0)[:60])
    assert not bad, f"{_rel(path)} 템플릿이 모듈을 직접 부른다: {bad[:3]}"


# ── 파이썬 소스 위생 ────────────────────────────────────────────────────────
# 같은 편집 사고의 파이썬 판. heredoc 으로 소스를 고치면 줄바꿈이 **공백으로 뭉개져**
# `if a  <공백 17칸>  and b:` 같은 줄이 남는다. 문법은 멀쩡해서 테스트도 전부 통과하고
# 리뷰에서도 넘어가지만, 그 줄은 아무도 다시 읽지 못한다(실측 5건).
_ROOT = Path(__file__).resolve().parents[1]
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

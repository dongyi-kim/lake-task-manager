"""agent/prompts/roles.py — 역할별 시스템 지시의 **로더**. 내용은 `roles/*.md` 에 있다.

프롬프트를 파이썬 문자열로 코드에 몰아넣지 않는 이유(사용자 지시): 프롬프트는 코드가 아니라
**편집하는 자산**이다. md 파일이면 diff 가 깨끗하고, 이스케이프 지옥이 없고, 비개발자도 고칠
수 있고, 프롬프트만 바꾼 커밋이 코드 리뷰에 섞이지 않는다.

앱 시작 때 한 번 읽어 상수로 노출한다 — 기존 import 경로(`from app.agent.prompts.roles import
SYSTEM_X`)는 그대로 살아 있어 역할 코드는 아무것도 몰라도 된다.

| 파일 | 쓰는 곳 |
|---|---|
| planner.md   | Planner — 의도 분류만, 답 만들지 않기 |
| historian.md | Historian — 조사 요령·외부 검색 경계·브리핑 요령 |
| refiner.md   | Refiner — 되묻기 기준·쪼개기 기준 (동적 경고는 코드가 덧붙인다) |
| assigner.md  | Assigner — 근거 4신호·금지 사항 |
| reviewer.md  | Reviewer — 3-Check·기계 판정 우선 |
| operator.md  | Operator — 승인 토큰 규율·실패 보고 |
| responder.md | Responder — 근거 문장·표 사용·키 날조 금지 |
| pmo.md       | PMO — 현황 조회 판단 기준·권한 거부 존중 |

새 역할을 추가하면: md 를 만들고 아래 목록에 이름을 넣는다. 파일이 없으면 **시작 시점에
바로 죽는다** — 프롬프트가 빈 채로 조용히 돌아가는 것보다 낫다.
"""

from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).parent / "roles"


def _load(name: str) -> str:
    p = _DIR / f"{name}.md"
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"프롬프트 파일이 비어 있습니다: {p}")
    return text


SYSTEM_PLANNER = _load("planner")
SYSTEM_HISTORIAN = _load("historian")
SYSTEM_REFINER = _load("refiner")
SYSTEM_ASSIGNER = _load("assigner")
SYSTEM_REVIEWER = _load("reviewer")
SYSTEM_OPERATOR = _load("operator")
SYSTEM_RESPONDER = _load("responder")
SYSTEM_PMO = _load("pmo")
SYSTEM_CURATOR = _load("curator")
SYSTEM_COMPOSER = _load("composer")   # 에디터 안에서 본문·코멘트를 써 주는 역할


def _load_playbooks() -> dict:
    """playbooks.md 의 `## id` 절들 → {id: 본문}. 전형적 요청의 사전 정의 플로우."""
    text = (_DIR.parent / "playbooks.md").read_text(encoding="utf-8")
    out, cur, buf = {}, None, []
    for line in text.splitlines():
        if line.startswith("## "):
            if cur:
                out[cur] = "\n".join(buf).strip()
            cur, buf = line[3:].strip(), []
        elif cur is not None:
            buf.append(line)
    if cur:
        out[cur] = "\n".join(buf).strip()
    return out


PLAYBOOKS = _load_playbooks()

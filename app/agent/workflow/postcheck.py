"""agent/workflow/postcheck.py — **내보내기 직전의 후검증.** 플레이북별 최소선을 코드가 본다.

왜 필요한가(사용자 지시 2026-08-10: "주로 등장하는 태스크에 대해서는 미리 명확한 플로우와
체크리스트, 결과에 대한 후검증도 적용해라"):

  프롬프트에 "이렇게 써라"를 적어 두면 **대체로** 그렇게 나온다. 문제는 '대체로'다 —
  같은 요청이 어떤 날은 연표만 나오고 어떤 날은 현재 상태까지 나온다. 실사용에서 걸린
  결함의 상당수가 **모델의 실력이 아니라 흔들림**이었다(이력 답변에 '지금'이 빠짐,
  진척 답변에 진행 중 자식 누락, 일괄 계획에 대상 표 누락…).

  흔들림은 지시로 못 잡는다. **잴 수 있는 것은 코드가 재고**, 못 지켰으면 그 사실을
  드러낸다. 이 저장소의 다른 가드들(grounding·본문 게이트)과 같은 자리다.

세 가지를 지킨다:
  ① **판정만 한다** — 답을 고쳐 쓰지 않는다. 고치는 것은 Responder 의 재작성 경로 몫이고,
     여기서 조용히 손보면 무엇이 부족했는지 아무도 모르게 된다.
  ② **최소선만 본다** — "표가 있나", "근거가 붙었나" 처럼 **형식**만. 내용의 옳고 그름은
     judge 도 사람도 아닌 코드가 판단할 일이 아니다.
  ③ **모르면 통과** — 플레이북이 없거나 조건이 안 맞으면 아무 말도 안 한다. 과잉 경고는
     경고를 무시하게 만든다.
"""

from __future__ import annotations

import re

_KEY = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
_REF_MARK = re.compile(r"\[\d{1,2}\]")
_TABLE_ROW = re.compile(r"^\s*\|.+\|\s*$", re.M)


def _has_table(text: str) -> bool:
    return len(_TABLE_ROW.findall(text or "")) >= 2      # 헤더 + 구분선 최소


def _has_refs(text: str) -> bool:
    """참조 섹션이 있나 — `**참조**` 아래 목록, 또는 번호 붙은 출처 줄."""
    t = text or ""
    if "참조" in t and _KEY.search(t):
        return True
    return bool(re.search(r"^\s*(?:\[\d{1,2}\]|\d{1,2}[.)])\s+\S", t, re.M))


# ── 플레이북별 최소선 ──────────────────────────────────────────────
# 각 함수는 **위반 사유 목록**을 돌려준다(빈 목록 = 통과).

def _check_history(text: str, state: dict) -> list:
    """이력·경위 답변 — '지금 어떤가'와 '어떻게 왔나'가 **둘 다** 있어야 한다.

    실사용 지적 두 번: 연표만 내고 현재 상태가 없었다 / 근거 없이 티켓 제목만 옮겼다.
    """
    bad = []
    if "현재" not in text:
        bad.append("현재 상태가 없다 — 이력만으로는 '그래서 지금 어떤가'에 답이 안 된다")
    if not _has_table(text):
        bad.append("현재 상태·연표를 표로 내지 않았다(| 항목 | 값 | 근거 |)")
    if _KEY.search(text) and not _has_refs(text):
        bad.append("티켓을 언급했는데 참조 목록이 없다 — 사용자가 확인할 길이 없다")
    return bad


def _check_find_tickets(text: str, state: dict) -> list:
    """조건 조회 — 표로 내거나, 0건임을 **기준과 함께** 밝히거나."""
    if _has_table(text):
        return []
    if re.search(r"0\s*건|없습니다|해당(하는)?\s*티켓이\s*없", text or ""):
        return []
    return ["조건 조회인데 결과 표도 '0건' 명시도 없다"]


def _check_workload(text: str, state: dict) -> list:
    """활동·워크로드 — 한 명만 보고 끝내지 않는다(3층 보고의 최소 흔적)."""
    people = set(re.findall(r"\b[a-z][\w-]*\.[\w-]+\b", text or ""))
    if len(people) <= 1 and not _has_table(text):
        return ["여러 사람을 물었는데 한 명(또는 표 없이)으로 끝났다"]
    return []


def _check_draft(text: str, state: dict) -> list:
    """초안 갈래 — 승인 카드에 **항목이 있어야** 사용자가 승인할 수 있다.

    "질문만 내고 초안 0건"은 실사용에서 먹통으로 읽힌다(실측 2회). 다만 되묻는 턴은
    정당하므로, **질문도 없고 항목도 없을 때만** 위반이다.
    """
    items = ((state or {}).get("draft") or {}).get("items") or []
    if items or (state or {}).get("questions") or (state or {}).get("change_plan"):
        return []
    return ["초안도 질문도 없다 — 사용자가 할 수 있는 일이 없는 답이다"]


CHECKS = {
    "history": [_check_history],
    "knowledge": [_check_history],          # 지식 브리프도 근거·표 규율은 같다
    "asset_lookup": [_check_history],
    "find_tickets": [_check_find_tickets],
    "workload": [_check_workload],
    "task_create": [_check_draft],
    "bug_report": [_check_draft],
    "epic_create": [_check_draft],
    "subtask_bulk": [_check_draft],
}


def check(state: dict, text: str) -> list:
    """이 답이 그 플레이북의 최소선을 지켰나 — 위반 사유 목록(빈 목록 = 통과).

    플레이북은 Planner 가 고른다. 못 고른 턴(빈 문자열)에는 아무것도 걸지 않는다 —
    무엇을 하려는 요청인지 모르는 상태에서 형식을 강요하면 그게 더 나쁘다.
    """
    pb = str((state or {}).get("playbook") or "").strip()
    fns = CHECKS.get(pb) or []
    out = []
    for fn in fns:
        try:
            out += fn(text or "", state or {})
        except Exception:                    # noqa: BLE001 — 검사기가 답을 막으면 안 된다
            continue
    return out


def note(bad: list) -> str:
    """위반을 **답 아래에 붙일 한 덩이**로. 숨기지 않는 것이 이 저장소의 규율이다."""
    if not bad:
        return ""
    rows = "\n".join(f"- {b}" for b in bad[:4])
    return ("\n\n> ⚠ **이 답변이 우리 형식 기준을 다 채우지 못했습니다**\n"
            + "\n".join(f"> {r}" for r in rows.splitlines()))

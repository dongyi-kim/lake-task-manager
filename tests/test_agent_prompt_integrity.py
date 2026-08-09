"""역할 프롬프트(md)와 실제 역할 코드가 어긋나지 않는지 — **구조적 방어장치**.

왜 테스트로 두는가: 성능 라운드에서 Refiner·Assigner 를 `ToolAgent` → `StructuredAgent`
로 바꾸며 도구를 전부 걷어냈는데, **md 는 그대로 남아** "먼저 `search_rules` 를 불러라",
"`get_module_people` 로 후보를 모아라" 하고 열 군데서 시켰다. 코드는 멀쩡히 돌고 테스트도
전부 통과하니 아무도 몰랐다 — 모델만 없는 도구를 찾아 헤맸다.

이런 어긋남은 사람이 md 를 읽어야만 보이고, 읽는 일은 잊힌다. 기계가 본다.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from app.agent.workflow.agents.assigner import Assigner
from app.agent.workflow.agents.curator import Curator
from app.agent.workflow.agents.historian import Historian
from app.agent.workflow.agents.operator import Operator
from app.agent.workflow.agents.planner import Planner
from app.agent.workflow.agents.pmo import PMO
from app.agent.workflow.agents.refiner import Refiner
from app.agent.workflow.agents.responder import Responder
from app.agent.workflow.agents.reviewer import Reviewer

ROLES = {
    "planner": Planner, "historian": Historian, "refiner": Refiner,
    "assigner": Assigner, "reviewer": Reviewer, "operator": Operator,
    "responder": Responder, "pmo": PMO, "curator": Curator,
}
MD_DIR = pathlib.Path(__file__).resolve().parents[1] / "app/agent/prompts/roles"

# 도구를 **부르라는 지시가 아닌** 줄 — 금지 예시("Wrong: …")는 도구명이 나와도 정상이다.
_ANTI_EXAMPLE = re.compile(r"^\s*(?:Wrong|Right|나쁜 예|좋은 예)\s*:")


def _all_tool_names() -> set:
    from app.agent import tools as T
    return set(T.BY_NAME)


def _own_tool_names(role) -> set:
    try:
        return {t.name for t in role().tools}
    except Exception:                       # pragma: no cover - 도구 없는 역할
        return set()


@pytest.mark.parametrize("name", sorted(ROLES))
def test_role_md_does_not_order_tools_the_role_lacks(name):
    """md 가 시키는 도구는 그 역할이 실제로 가진 것이어야 한다."""
    p = MD_DIR / f"{name}.md"
    if not p.exists():
        pytest.skip(f"{name}.md 없음")
    known, own = _all_tool_names(), _own_tool_names(ROLES[name])
    ghosts = {}
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if _ANTI_EXAMPLE.match(line):
            continue
        for m in set(re.findall(r"`([a-z_]{4,})`", line)) | set(
                re.findall(r"\b([a-z_]{4,})\(", line)):
            if m in known and m not in own:
                ghosts.setdefault(m, i)
    assert not ghosts, (
        f"{name}.md 가 이 역할에 없는 도구를 부르라고 지시한다: "
        + ", ".join(f"{k}(L{v})" for k, v in sorted(ghosts.items()))
        + f" — 이 역할의 도구는 {sorted(own) or '없음'}. "
        "도구를 걷어냈다면 md 도 '재료는 이미 자료에 있다'로 고쳐야 한다.")


# **조회하던 것을 코드 사전취합으로 옮긴** 역할들. 이들에게만 "도구가 없다"는 선언을
# 요구한다 — 그전까지 md 가 "먼저 불러라"고 시키던 자리라서, 없다고 못 박지 않으면 모델이
# "확인해 보겠습니다"로 답하거나 조회한 척한다.
# (Planner·Reviewer·Curator·Responder 는 처음부터 도구가 없었고 md 도 조회를 시킨 적이
#  없으므로 대상이 아니다 — 안 하던 말을 새로 넣는 건 토큰만 늘린다.)
_CONVERTED_TO_MATERIALS = ("refiner", "assigner")


@pytest.mark.parametrize("name", _CONVERTED_TO_MATERIALS)
def test_converted_role_md_declares_it_has_no_tools(name):
    assert not _own_tool_names(ROLES[name]), f"{name} 이 다시 도구를 갖게 됐다 — 이 테스트를 고쳐라"
    text = (MD_DIR / f"{name}.md").read_text(encoding="utf-8")
    assert re.search(r"NO tools|no tools|도구가 없다|도구를 쓰지", text), (
        f"{name}.md 에 '도구가 없다'는 선언이 없다 — 재료는 코드가 미리 실어 주는데 "
        "모델은 그걸 모른 채 조회하려 든다.")


def test_every_role_md_is_loaded_by_the_loader():
    """roles/ 의 md 는 전부 로더 상수로 노출돼야 한다 — 고아 파일은 조용히 안 쓰인다."""
    from app.agent.prompts import roles as R
    loaded = {v for k, v in vars(R).items() if k.startswith("SYSTEM_") and isinstance(v, str)}
    for p in sorted(MD_DIR.glob("*.md")):
        body = p.read_text(encoding="utf-8").strip()
        assert body in loaded, f"{p.name} 이 어떤 SYSTEM_* 상수로도 로드되지 않는다"

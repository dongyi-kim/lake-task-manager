"""local(fake) 서버를 외부 오픈소스 패키지 jira820 으로 서빙하는 브리지.

핵심: `mock == local` 불변식 유지. mock(이 앱을 in-process 로 호출) 과 local(실 HTTP) 이 **같은**
`app.world.get_world()` + 같은 jira820 직렬화기를 쓰므로 출력이 일치한다 — 전송 방식만 다르다.

jira820 은 범용 Jira DC 8.20.8 mock 이라 **자체 샘플(JIRA820 프로젝트 + Confluence 스페이스)** 을
seed 로 갖는다. 여기서는 그 위에 **이 프로젝트 world(DL) 를 additive 로 주입** → 스토어에
JIRA820 + DL 이 공존한다. 덕분에 멀티 프로젝트/스페이스 시나리오(통합 검색 등)를 dev 에서 검증할 수 있다.
(사용자/키가 disjoint: jira820=u01·JIRA820-N / 우리=사번·DL-N → 우리 DL 스코프 쿼리엔 영향 없음.)
"""

import os

from jira820 import make_app
from jira820.config import Config
from jira820.store import Store

from .settings import get_settings
from .world import World as _World, get_world

W_ME = _World.ME          # dev 세션 사용자(= world 픽스처의 "나")

# 사내 워크플로 상태/타입 스킴 — app/world.py 직렬화기와 동일해야 statusCategory·subtask 판정이 일치
_STATUSES = [["Open", "todo", "1"], ["In Progress", "inprogress", "3"],
             ["Reopened", "todo", "4"], ["Resolved", "done", "5"], ["Closed", "done", "6"]]
# 전이 규칙. jira820 기본값은 '아무 상태 → 아무 상태' 라 편하지만, 그러면 우클릭 메뉴에
# 도달 불가능한 상태까지 뜬다 — 앱이 "갈 수 있다" 고 해 놓고 Jira 가 거절하는 상황을 로컬에서
# 재현하지 못한다. 사내 워크플로와 같은 모양으로 좁혀 둔다.
_TRANSITIONS = {
    "Open":        ["In Progress", "Resolved"],
    "In Progress": ["Resolved", "Open"],
    "Resolved":    ["Closed", "Reopened"],
    "Closed":      ["Reopened"],
    "Reopened":    ["In Progress", "Resolved"],
}
# 전이 화면 — 사내 워크플로는 **되돌리는 전이에도** 입력을 요구한다.
# Reopen 은 "누가 다시 볼 것인가(담당자)" 와 "왜 되돌리는가(코멘트)" 가 없으면 나중에 아무도
# 이유를 모른다 → 둘 다 강제. (Resolved/Closed 로 갈 때는 기본 화면 = 작업시간·담당자·해결책)
_TRANSITION_SCREENS = {
    "Reopened": ["assignee", "comment"],
}
# 사내 우선순위 — 'P{n}-이름' 체계 + 미분류(Unclassified). 등급은 접두사 숫자로 읽는다.
_PRIORITIES = [["P0-Blocker", "1"], ["P1-Critical", "2"], ["P2-Major", "3"],
               ["P3-Minor", "4"], ["P4-Trivial", "5"], ["Unclassified", "6"]]
_ISSUE_TYPES = [["Bug", "1"], ["Epic", "2"], ["Improvement", "3"], ["New Feature", "4"],
                ["Story", "5"], ["Task", "6"], ["Sub-Task", "7"]]


def build_store():
    w = get_world()
    s = get_settings()
    # jira820 자체 샘플 프로젝트는 'JIRA820'(우리 DL 과 분리). 스킴(상태/타입/필드/모듈)은 우리와 동일하게
    # 맞춰 두 프로젝트가 같은 직렬화기로 일관 서빙되도록 한다.
    cfg = Config(
        project_key="JIRA820", project_name="JIRA820 Sample Project",
        base_date=w.today, server_version="8.20.8", confluence_version="9.2.4",
        sp_field=s.sp_field_id, epic_link_field=s.epic_link_field_id,
        epic_name_field=s.epic_name_field_id,
        subtask_type="Sub-Task",
        statuses=_STATUSES, transition_scheme=_TRANSITIONS, issue_types=_ISSUE_TYPES,
        transition_screens=_TRANSITION_SCREENS,
        priorities=_PRIORITIES,
        # 새로 만든 티켓은 **미분류**로 시작한다 — 아무 등급이나 자동으로 붙이면
        # 'P2 인데 아무도 P2 라고 판단한 적 없는' 티켓이 쌓인다.
        default_priority="Unclassified",
        modules=list(w.modules), components_extra=["사용자 VoC"],
        latency_ms=int(os.getenv("FAKE_LATENCY_MS", "0")),
        # 세션 사용자(/myself) — dev 에서 '내 Task' 가 빈 화면이 되지 않게 world 사람으로.
        # (실 Jira 에선 SSO 로그인 사용자다.)
        current_user=W_ME,
    )
    store = Store(cfg, seed=True)       # jira820 자체 샘플(JIRA820 프로젝트 + confluence) 시드
    # 이 프로젝트 world(DL) 를 additive 주입 (키/사용자 disjoint → 교체 아님, 공존).
    store.users.update(w.users)
    store.issues.update(w.issues)
    store.sprints.update(getattr(w, 'sprints', {}) or {})   # '스프린트 내 티켓만' 필터용       # 내부 이슈 dict 는 jira820 직렬화기와 호환(누락 키는 .get 기본값)
    store.attachments.update(w.attachments)   # 첨부(바이트 포함) — 다운로드/썸네일 경로까지 동작
    store.activity.update(w.activity)
    store.confluence.update(w.confluence)
    store.reindex()
    return store


def build_injected_app():
    return make_app(store=build_store())

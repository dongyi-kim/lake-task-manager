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
from .world import get_world

# 사내 워크플로 상태/타입 스킴 — app/world.py 직렬화기와 동일해야 statusCategory·subtask 판정이 일치
_STATUSES = [["Open", "todo", "1"], ["In Progress", "inprogress", "3"],
             ["Reopened", "todo", "4"], ["Resolved", "done", "5"], ["Closed", "done", "6"]]
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
        subtask_type="Sub-Task",
        statuses=_STATUSES, issue_types=_ISSUE_TYPES,
        modules=list(w.modules), components_extra=["사용자 VoC"],
        latency_ms=int(os.getenv("FAKE_LATENCY_MS", "0")),
    )
    store = Store(cfg, seed=True)       # jira820 자체 샘플(JIRA820 프로젝트 + confluence) 시드
    # 이 프로젝트 world(DL) 를 additive 주입 (키/사용자 disjoint → 교체 아님, 공존).
    store.users.update(w.users)
    store.issues.update(w.issues)       # 내부 이슈 dict 는 jira820 직렬화기와 호환(누락 키는 .get 기본값)
    store.attachments.update(w.attachments)   # 첨부(바이트 포함) — 다운로드/썸네일 경로까지 동작
    store.activity.update(w.activity)
    store.confluence.update(w.confluence)
    store.reindex()
    return store


def build_injected_app():
    return make_app(store=build_store())

"""local(fake) 서버를 외부 오픈소스 패키지 jira820 으로 서빙하는 브리지.

핵심: `mock == local` 불변식 유지. mock(이 앱을 in-process 로 호출) 과 local(실 HTTP) 이 **같은**
`app.world.get_world()` + 같은 jira820 직렬화기를 쓰므로 출력이 일치한다 — 전송 방식만 다르다.

jira820 은 범용 Jira DC 8.20.8 mock 이라 자체 generic 데이터를 시드하지만, 여기서는 `seed=False` 로
빈 스토어를 만든 뒤 **이 프로젝트 world 를 주입**해 우리 데이터를 그대로 서빙한다.
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
    cfg = Config(
        project_key=w.project, project_name="Lake Task Manager",
        base_date=w.today, server_version="8.20.8",
        sp_field=s.sp_field_id, epic_link_field=s.epic_link_field_id,
        subtask_type="Sub-Task",
        statuses=_STATUSES, issue_types=_ISSUE_TYPES,
        modules=list(w.modules), components_extra=["사용자 VoC"],
        latency_ms=int(os.getenv("FAKE_LATENCY_MS", "0")),
    )
    store = Store(cfg, seed=False)      # 시드 생략 → 우리 world 주입
    store.users = w.users
    store.issues = w.issues             # 내부 이슈 dict 는 jira820 직렬화기와 호환(누락 키는 .get 기본값)
    store.activity = w.activity
    store.confluence = w.confluence
    store.reindex()
    return store


def build_injected_app():
    return make_app(store=build_store())

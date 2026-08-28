"""mock == local 불변식 가드.

jira820(외부 패키지)에 이 프로젝트 world 를 주입한 서버(app/fakebridge)를 TestClient 로 띄우고,
그 위로 HTTP 하는 provider shim 을 local JiraClient 에 꽂아, build_vit/build_workload/rollup 출력이
env=mock(in-process) 과 동일한지 비교한다. jira820 미설치 시 skip.
"""

import json
import os

import pytest

os.environ.setdefault("JIRA_ENV", "mock")

pytest.importorskip("jira820")

from fastapi.testclient import TestClient  # noqa: E402

from app.infra.cache import Cache  # noqa: E402
from app.infra.settings import get_settings, load_people, load_plan  # noqa: E402
from app.domain import rollup, vit, workload   # noqa: E402
from app.jira.jira_client import JiraClient  # noqa: E402


class _Shim:
    """local BasicAuthProvider 대체 — jira820 TestClient 로 HTTP."""
    supports_parallel = False

    def __init__(self, client):
        self.c = client

    def get_json(self, path, params=None):
        r = self.c.get(path, params=params or {})
        if r.status_code in (401, 403) or r.status_code >= 500:
            raise RuntimeError(f"HTTP {r.status_code}")
        return r.json()

    def get_text(self, path, params=None):
        r = self.c.get(path, params=params or {})
        return r.text

    def close(self):
        pass


def _local_client(tmp_path):
    from app.mock.fakebridge import build_injected_app
    tc = TestClient(build_injected_app())
    c = JiraClient(get_settings(), Cache(str(tmp_path / "local.sqlite3")))
    c.env = "local"
    c._provider = _Shim(tc)
    c._provider_built = True
    return c


def _mock_client(tmp_path):
    c = JiraClient(get_settings(), Cache(str(tmp_path / "mock.sqlite3")))
    c.env = "mock"
    return c


def _norm(o):
    if isinstance(o, dict):
        return {k: _norm(v) for k, v in o.items() if k not in ("generatedAt", "jiraBase")}
    if isinstance(o, list):
        return [_norm(x) for x in o]
    return o


def _eq(a, b):
    return json.dumps(_norm(a), sort_keys=True, ensure_ascii=False) == \
        json.dumps(_norm(b), sort_keys=True, ensure_ascii=False)


def test_mock_local_parity(tmp_path):
    plan, people = load_plan(), load_people()
    mock = _mock_client(tmp_path)
    local = _local_client(tmp_path)

    # WBS 롤업
    assert _eq(rollup.build(plan, mock.epic_progress_map(plan)),
               rollup.build(plan, local.epic_progress_map(plan))), "wbs rollup differs"
    # 현안
    assert _eq(vit.build_vit(mock, plan, people), vit.build_vit(local, plan, people)), "vit differs"
    # 워크로드
    assert _eq(workload.build_workload(mock, plan, people),
               workload.build_workload(local, plan, people)), "workload differs"
    # 인력 상세 (첫 인력)
    uid = next(iter(people.values()))[0]
    assert _eq(mock.workload_tickets(uid), local.workload_tickets(uid)), "workload tickets differ"


# ── 캐시가 어제 세계를 물고 있으면 오늘 세계와 섞인다 (실측: 자정 넘어 테스트 4건 파손) ──
def test_dev_cache_namespace_carries_the_world_date():
    """dev 세계는 `today` 기준으로 **매 프로세스 재생성**되는데 캐시는 파일이라 자정을
    넘겨 살아남는다 — 어제 티켓과 오늘 티켓이 한 화면에 섞인다. 실측: DL-9090 의 자식이
    3건인데 캐시가 1건만 물어 '하위 Sub-Task 1/1 완료'가 됐고 테스트 4건이 깨졌다.
    **코드는 멀쩡했다.** 서버는 rev 로 stale 을 확인할 수 있지만 캐시는 그럴 수단도 없었다."""
    from datetime import date
    from app.agent.tools._ctx import client
    env = client().env
    assert env.startswith("mock@") or env.startswith("local@"), env
    assert env.endswith(date.today().isoformat()), env

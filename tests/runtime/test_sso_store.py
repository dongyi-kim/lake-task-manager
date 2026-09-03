"""SSO 세션 저장소 — 서비스별 분리 저장.

가드하는 실제 사고:
 1) 한 서비스를 다시 로그인하면 나머지 서비스 쿠키가 날아가던 것(단일 파일 전체 덮어쓰기).
 2) 쓰다가 죽으면 잘린 JSON 이 남아 로그인이 통째로 사라지던 것(원자적 교체로 방지).
prod SSO 에는 접근하지 않는다 — 파일 조작만 검증한다.
"""
import json
import os
import sys

from app.auth.sso_store import SsoStore   # noqa: E402

BASES = {"jira": "https://jira.corp.example",
         "confluence": "https://conf.corp.example",
         "bitbucket": "https://bit.corp.example"}


def _ck(name, domain):
    return {"name": name, "value": "v", "domain": domain, "path": "/"}


def _store(tmp_path):
    return SsoStore(str(tmp_path / "jira_state.json"), BASES)


def _state(*cookies):
    return {"cookies": list(cookies), "origins": []}


def test_saving_one_service_keeps_the_others(tmp_path):
    """핵심 회귀: Confluence 만 다시 로그인해도 Jira/Bitbucket 세션은 살아 있어야 한다."""
    st = _store(tmp_path)
    st.save("jira", _state(_ck("JSESSIONID", "jira.corp.example")))
    st.save("bitbucket", _state(_ck("BBSESSION", "bit.corp.example")))
    st.save("confluence", _state(_ck("CSESSION", "conf.corp.example")))

    # Confluence 재로그인 — 그 컨텍스트엔 Confluence 쿠키뿐이다(예전엔 이때 전부 날아갔다)
    st.save("confluence", _state(_ck("CSESSION", "conf.corp.example")))

    names = {c["name"] for c in st.merged()["cookies"]}
    assert names == {"JSESSIONID", "BBSESSION", "CSESSION"}


def test_save_keeps_only_that_services_cookies(tmp_path):
    """서비스 파일엔 그 서비스 호스트 쿠키만 — 안 그러면 나눈 의미가 없다."""
    st = _store(tmp_path)
    st.save("jira", _state(_ck("A", "jira.corp.example"), _ck("B", "conf.corp.example")))
    saved = json.load(open(st.path("jira"), encoding="utf-8"))
    assert [c["name"] for c in saved["cookies"]] == ["A"]


def test_parent_domain_cookie_belongs_to_the_service(tmp_path):
    """사내는 보통 '.corp.example' 상위 도메인에 쿠키를 붙인다 — 이것도 그 서비스 것으로 봐야 한다."""
    st = _store(tmp_path)
    st.save("jira", _state(_ck("SSO", ".corp.example")))
    assert [c["name"] for c in json.load(open(st.path("jira"), encoding="utf-8"))["cookies"]] == ["SSO"]


def test_unmatched_cookies_are_kept_rather_than_lost(tmp_path):
    """도메인 규칙이 예상과 다르면(사내 프록시 등) 버리지 말고 통째로 둔다 — 잃는 것보다 낫다."""
    st = _store(tmp_path)
    st.save("jira", _state(_ck("WEIRD", "auth.other.example")))
    assert [c["name"] for c in json.load(open(st.path("jira"), encoding="utf-8"))["cookies"]] == ["WEIRD"]


def test_save_all_from_splits_one_login_window(tmp_path):
    """로그인 창 하나로 세 서비스를 다 돌았을 때 — 도메인별로 갈라 각각 저장된다."""
    st = _store(tmp_path)
    st.save_all_from(_state(_ck("J", "jira.corp.example"),
                            _ck("C", "conf.corp.example"),
                            _ck("B", "bit.corp.example")))
    got = {s: [c["name"] for c in json.load(open(st.path(s), encoding="utf-8"))["cookies"]]
           for s in ("jira", "confluence", "bitbucket")}
    assert got == {"jira": ["J"], "confluence": ["C"], "bitbucket": ["B"]}


def test_legacy_single_file_is_migrated_and_kept(tmp_path):
    """예전 jira_state.json 은 도메인 기준으로 갈라 옮기되 원본은 남긴다(되돌릴 여지)."""
    legacy = tmp_path / "jira_state.json"
    legacy.write_text(json.dumps(_state(_ck("J", "jira.corp.example"),
                                        _ck("C", "conf.corp.example"))), encoding="utf-8")
    st = _store(tmp_path)
    assert st.any_exists()
    names = {c["name"] for c in st.merged()["cookies"]}
    assert names == {"J", "C"}
    assert legacy.exists()                       # 원본 보존
    assert st.path("jira").exists() and st.path("confluence").exists()


def test_write_is_atomic_and_leaves_no_partial_file(tmp_path):
    """쓰기 실패 시 기존 파일이 온전해야 한다(잘린 JSON 이 남으면 로그인이 날아간다)."""
    st = _store(tmp_path)
    st.save("jira", _state(_ck("GOOD", "jira.corp.example")))
    before = st.path("jira").read_text(encoding="utf-8")

    class Boom(dict):
        def get(self, *a, **k):
            raise RuntimeError("write failed midway")

    try:
        st.save("jira", Boom())
    except Exception:
        pass
    assert st.path("jira").read_text(encoding="utf-8") == before
    assert not [p for p in os.listdir(st.dir) if p.startswith(".tmp-")]   # 임시파일 잔여 없음


def test_merged_is_none_when_nothing_saved(tmp_path):
    st = _store(tmp_path)
    assert st.merged() is None and not st.any_exists()


def test_status_reports_per_service(tmp_path):
    st = _store(tmp_path)
    st.save("jira", _state(_ck("J", "jira.corp.example")))
    stt = st.status()
    assert stt["jira"]["exists"] and stt["jira"]["savedAt"]
    assert not stt["confluence"]["exists"]


def test_conditional_save_rejects_snapshot_older_than_latest_login(tmp_path):
    """느린 old provider snapshot은 그 사이 저장된 새 로그인 쿠키를 덮을 수 없다."""
    st = _store(tmp_path)
    st.save("jira", _state(_ck("OLD", "jira.corp.example")))
    revision = st.revision("jira")
    st.save("jira", _state(_ck("NEW_LOGIN", "jira.corp.example")))

    saved = st.save_if_unchanged(
        "jira", _state(_ck("OLD_ROLLING", "jira.corp.example")), revision)

    assert saved is False
    cookies = json.load(open(st.path("jira"), encoding="utf-8"))["cookies"]
    assert [cookie["name"] for cookie in cookies] == ["NEW_LOGIN"]


def test_conditional_save_succeeds_when_revision_is_unchanged(tmp_path):
    st = _store(tmp_path)
    st.save("jira", _state(_ck("OLD", "jira.corp.example")))
    revision = st.revision("jira")

    assert st.save_if_unchanged(
        "jira", _state(_ck("ROLLED", "jira.corp.example")), revision) is True
    cookies = json.load(open(st.path("jira"), encoding="utf-8"))["cookies"]
    assert [cookie["name"] for cookie in cookies] == ["ROLLED"]

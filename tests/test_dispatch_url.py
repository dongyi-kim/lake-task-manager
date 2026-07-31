# -*- coding: utf-8 -*-
"""dispatch_url(Jira 링크 디스패처) 시나리오 회귀 — 삭제/부재/오류 케이스 포함.

실제 브라우저·네트워크는 건드리지 않는다(Popen/urlopen/레지스트리 조회를 전부 가로챈다).
'중간에 삭제된 경우'(기억해 둔 브라우저 삭제, config 삭제, 앱 종료)가 와도 링크가
죽지 않고 폴백 체인을 타는지가 핵심 검증이다.
"""
import os
import tempfile
import types
from unittest import mock

import dispatch_url as d


# ── URL 판정 ─────────────────────────────────────────────────────────────
def test_browse_url_matches():
    assert d._jira_ticket_of("http://127.0.0.1:8080/browse/DL-9008") == "DL-9008"
    assert d._jira_ticket_of("http://127.0.0.1:8080/browse/dl-77") == "DL-77"          # 소문자 정규화
    assert d._jira_ticket_of("http://127.0.0.1:8080/browse/DL-1?focusedId=3#c") == "DL-1"  # 쿼리 무시


def test_non_ticket_urls_go_to_browser():
    f = d._jira_ticket_of
    assert f("http://127.0.0.1:8080/browse/DL-9008/") is None      # 트레일링 슬래시
    assert f("http://127.0.0.1:8080/secure/Dashboard.jspa") is None  # Jira 의 다른 경로
    assert f("https://www.google.com/browse/DL-1") is None         # 다른 호스트
    assert f("http://127.0.0.1:8080/browse/hello") is None         # 키 형식 아님
    assert f("https://127.0.0.1:8080/browse/DL-1") is None         # 스킴 불일치
    assert f("not a url ::") is None


# ── config 삭제/깨짐 ─────────────────────────────────────────────────────
def test_config_missing_or_broken():
    with mock.patch.object(d, "_ROOT", tempfile.mkdtemp()):
        assert d._load_cfg() == (None, 8000)
        assert d._jira_ticket_of("http://127.0.0.1:8080/browse/DL-1") is None
    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, "config"))
    with open(os.path.join(tmp, "config", "jira.yml"), "w") as f:
        f.write("{{{{ broken yaml :::")
    with mock.patch.object(d, "_ROOT", tmp):
        assert d._load_cfg() == (None, 8000)


# ── 앱 부재/창 없는 모드 ────────────────────────────────────────────────
class _FakeResp:
    def __init__(self, code, body):
        self.status, self._b = code, body

    def read(self):
        return self._b.encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_send_to_app_when_down():
    with mock.patch.object(d, "_app_port", lambda: 1):     # 닫힌 포트 — 즉시 거부
        assert d._send_to_app("DL-1") is False


def test_send_to_app_window_modes():
    # 창 없는 모드(window=none)는 True 를 주면 링크가 삼켜진다 — False 여야 브라우저 폴백을 탄다
    with mock.patch("urllib.request.urlopen", return_value=_FakeResp(200, '{"ok":true,"window":"none"}')):
        assert d._send_to_app("DL-1") is False
    with mock.patch("urllib.request.urlopen", return_value=_FakeResp(200, '{"ok":true,"window":"focus"}')):
        assert d._send_to_app("DL-1") is True
    with mock.patch("urllib.request.urlopen", return_value=_FakeResp(200, "garbage")):
        assert d._send_to_app("DL-1") is False


# ── 전달 브라우저 삭제/부재 폴백 체인 ───────────────────────────────────
def test_forward_fallback_chain():
    calls = []

    def fake_popen(arg, *a, **kw):
        if isinstance(arg, str) and "DELETED" in arg:
            raise FileNotFoundError(arg)                   # 기억해 둔 브라우저가 삭제됨
        calls.append(arg)
        return types.SimpleNamespace()

    with mock.patch.object(d.subprocess, "Popen", side_effect=fake_popen):
        # 1) ForwardCmd 의 exe 삭제 → App Paths 폴백
        with mock.patch.object(d, "_saved_forward_cmd", lambda: '"C:\\DELETED\\browser.exe" %1'), \
             mock.patch.object(d, "_find_browser", lambda: "C:\\REAL\\edge.exe"):
            calls.clear()
            d._forward("https://x.com/")
            assert calls == [["C:\\REAL\\edge.exe", "https://x.com/"]]
        # 2) 브라우저 실행파일이 하나도 없음 → microsoft-edge: 프로토콜(Windows 내장)
        with mock.patch.object(d, "_saved_forward_cmd", lambda: None), \
             mock.patch.object(d, "_find_browser", lambda: None), \
             mock.patch.object(d.os, "startfile", create=True) as sf:
            d._forward("https://y.com/")
            assert sf.call_args[0][0] == "microsoft-edge:https://y.com/"
        # 3) 정상 ForwardCmd — %1 치환
        with mock.patch.object(d, "_saved_forward_cmd", lambda: '"C:\\ok\\chrome.exe" --single-argument %1'):
            calls.clear()
            d._forward("https://z.com/a?b=1")
            assert calls[0] == '"C:\\ok\\chrome.exe" --single-argument https://z.com/a?b=1'
        # 4) %1 없는 커맨드 — URL 덧붙임
        with mock.patch.object(d, "_saved_forward_cmd", lambda: '"C:\\ok\\browser.exe"'):
            calls.clear()
            d._forward("https://q.com/")
            assert calls[0] == '"C:\\ok\\browser.exe" "https://q.com/"'


# ── 종합 흐름 ────────────────────────────────────────────────────────────
def test_dispatch_flow():
    with mock.patch.object(d, "_jira_ticket_of", lambda u: "DL-1"):
        with mock.patch.object(d, "_send_to_app", lambda k: False), \
             mock.patch.object(d, "_forward") as fw:
            d._dispatch("http://jira/browse/DL-1")
            assert fw.called                                # 앱 죽음 → Jira 를 브라우저로
        with mock.patch.object(d, "_send_to_app", lambda k: True), \
             mock.patch.object(d, "_forward") as fw:
            d._dispatch("http://jira/browse/DL-1")
            assert not fw.called                            # 앱이 받음 → 브라우저 안 띄움
    with mock.patch.object(d, "_jira_ticket_of", lambda u: None), \
         mock.patch.object(d, "_forward") as fw:
        d._dispatch("https://google.com/")
        assert fw.called                                    # 비-Jira → 브라우저

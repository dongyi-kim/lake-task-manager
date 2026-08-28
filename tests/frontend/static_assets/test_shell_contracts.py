"""Home, guide, and global-search static UI contracts."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from frontend.static_assets.support import ROOT, STATIC

def test_home_shows_only_five_daily_release_notes():
    home = (STATIC / "components" / "views" / "HomeView.js").read_text(encoding="utf-8")
    notes = (STATIC / "lib" / "releaseNotes.js").read_text(encoding="utf-8")
    guide = (STATIC.parents[1] / "AGENTS.md").read_text(encoding="utf-8")

    assert "RELEASES.slice(0, 10)" in home
    assert ':key="r.version"' in home and "{{ r.version }}" in home
    assert "r.date" not in home and "hn-date" not in home
    versions = re.findall(r'version:\s*"(v\d{4}\.\d{2}\.\d{2})"', notes)
    assert len(versions) >= 5
    assert versions == sorted(versions, reverse=True)
    assert len(versions) == len(set(versions))
    assert not re.search(r'version:\s*"v\d{4}\.\d{2}\.\d{2}\.\d+"', notes)
    assert not re.search(r'\bdate\s*:', notes)
    assert "같은 날짜의 기존 항목에 변경 내용을 통합" in guide
    assert "별도 `date` 필드" in guide
    assert "최신 5개 날짜만 표시" in guide
def test_feature_guides_explain_search_recents_quick_open_and_browser_access():
    guides = (STATIC / "lib" / "guides.js").read_text(encoding="utf-8")
    spot = (STATIC / "components" / "ui" / "GuideSpot.js").read_text(encoding="utf-8")

    assert 'id: "global-search-recent-1"' in guides
    assert 'anchor: ".search-trig"' in guides
    assert "/ 키를 누르면 통합 검색창이 열립니다" in guides
    assert "최근 열어본" in guides and "티켓·문서·웹 링크" in guides
    assert 'id: "quick-open-hotkey-1"' in guides
    assert 'anchor: ".setmenu-trig"' in guides
    assert "body: ({ hotkey }) => hotkey" in guides
    assert 'export function hotkeyLabel(spec)' in guides
    assert 'api.prefs().then((p) =>' in spot and "p.quickOpenHotkey" in spot
    assert 'id: "browser-localhost-access-1"' in guides
    assert 'body: ({ port }) =>' in guides and '"웹 브라우저 주소창에서 localhost:" + port' in guides
    assert 'port: window.location.port || "4457"' in spot
    assert 'this.g.body({ hotkey: hotkeyLabel(this.hotkey), port:' in spot
    assert "{{ bodyText() }}" in spot


def test_global_search_keeps_up_to_twenty_unique_recent_items():
    search = (STATIC / "components" / "ui" / "SearchOverlay.js").read_text(encoding="utf-8")
    assert "const RECENT_MAX = 20;" in search
    assert "const RECENT_FETCH = 100;" in search
    assert "api.recent(RECENT_FETCH)" in search
    assert ").slice(0, RECENT_MAX);" in search


def test_global_search_revalidates_kept_results_when_reopened():
    search = (STATIC / "components" / "ui" / "SearchOverlay.js").read_text(encoding="utf-8")
    typeahead = (STATIC / "lib" / "typeahead.js").read_text(encoding="utf-8")

    assert "clear() { m.clear(); }" in typeahead
    assert "return { run, cancel, clear };" in typeahead
    assert "Object.values(this._src).forEach((t) => t.clear());" in search
    assert "if (this.q.trim()) this.run();" in search

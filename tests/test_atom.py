"""fake_jira.atom — 실 Jira Activity Streams ATOM 구조 + 파싱 라운드트립."""
import os
import sys
import xml.etree.ElementTree as ET
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.fake_jira import atom   # noqa: E402

A = "{http://www.w3.org/2005/Atom}"                 # Atom
V = "{http://activitystrea.ms/spec/1.0/}"           # activity


def _events():
    return [
        {"date": date(2026, 7, 1), "kind": "created", "key": "LAKE-1", "summary": "설정 외부화"},
        {"date": date(2026, 6, 30), "kind": "resolved", "key": "LAKE-2", "summary": "버그 수정"},
    ]


def test_atom_wellformed_real_structure():
    xml = atom.feed("http://localhost:8080", "skcc.x1042", _events(), display_name="김도윤 SKCC")
    root = ET.fromstring(xml)                        # well-formed
    entries = root.findall(f"{A}entry")
    assert len(entries) == 2
    e = entries[0]
    # kind = category term
    assert e.find(f"{A}category").get("term") == "created"
    # alternate link → /browse/KEY
    href = [ln.get("href") for ln in e.findall(f"{A}link") if ln.get("rel") == "alternate"][0]
    assert href.endswith("/browse/LAKE-1")
    # 실 Jira: 요약은 title(HTML) 이 아니라 activity:object/summary 에 있다
    obj = e.find(f"{V}object")
    assert obj is not None
    assert obj.findtext(f"{A}title") == "LAKE-1"
    assert obj.findtext(f"{A}summary") == "설정 외부화"
    # title 은 HTML(행위자·KEY 포함, 요약 아님)
    title = e.findtext(f"{A}title")
    assert "LAKE-1" in title and "김도윤" in title and "설정 외부화" not in title
    # verb / application 존재
    assert e.findtext(f"{V}verb").startswith("http://activitystrea.ms/schema/1.0/")


def test_atom_client_parse_roundtrip():
    """클라이언트 파서와 같은 경로로 kind/key/summary/date 추출이 되는지."""
    xml = atom.feed("http://localhost:8080", "skcc.x1042", _events(), display_name="김도윤 SKCC")
    root = ET.fromstring(xml)
    e = root.findall(f"{A}entry")[0]
    kind = e.find(f"{A}category").get("term")
    obj = e.find(f"{V}object")
    summary = obj.findtext(f"{A}summary")
    when = e.findtext(f"{A}updated")
    assert (kind, summary) == ("created", "설정 외부화")
    assert when and when.startswith("2026-07-01T")

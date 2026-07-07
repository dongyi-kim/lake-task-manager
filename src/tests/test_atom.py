"""fake_jira.atom — ATOM 피드 well-formed + 파싱 라운드트립."""
import os
import sys
import xml.etree.ElementTree as ET
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.fake_jira import atom   # noqa: E402

_NS = "{http://www.w3.org/2005/Atom}"


def test_atom_wellformed_and_fields():
    events = [
        {"date": date(2026, 7, 1), "kind": "created", "key": "LAKE-1", "summary": "설정 외부화"},
        {"date": date(2026, 6, 30), "kind": "resolved", "key": "LAKE-2", "summary": "버그 수정"},
    ]
    xml = atom.feed("http://localhost:8080", "jdoe", events)
    root = ET.fromstring(xml)                    # well-formed (예외 없으면 통과)
    entries = root.findall(f"{_NS}entry")
    assert len(entries) == 2
    e = entries[0]
    assert e.find(f"{_NS}category").get("term") == "created"
    href = [ln.get("href") for ln in e.findall(f"{_NS}link") if ln.get("rel") == "alternate"][0]
    assert href.endswith("/browse/LAKE-1")
    title = e.findtext(f"{_NS}title")
    assert "LAKE-1" in title and "설정 외부화" in title

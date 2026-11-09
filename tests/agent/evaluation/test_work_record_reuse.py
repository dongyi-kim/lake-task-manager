"""Real recovery found repeated cursors and valid same-digest chunk reuse."""
import copy
from hashlib import sha256
import json

from tools.codex_mcp_eval import work_cases, work_evidence
from tools.codex_mcp_eval.work_data import build_work_data


def page(start, items, next_start, *, total=3, key="DEMO-1"):
    return {"name": "get_evidence_page", "arguments": {"key": key, "kind": "comments", "start_at": start},
            "result": {"ok": True, "data": {"key": key, "kind": "comments", "startAt": start,
                "total": total, "sourceComplete": True, "partial": False, "items": items,
                "nextStartAt": next_start, "hasMore": next_start is not None}}}


def stream(*, short=False, changed=False, moved=False, key="DEMO-1"):
    record = {"id": "long-1", "body": "prefix " * 20 + ("CANCELLED" if not changed else "EXECUTED!") + " tail" * 20}
    text = json.dumps(record, ensure_ascii=False)
    one, two = len(text) // 3, 2 * len(text) // 3
    index, total = (2, 4) if moved else (1, 3)
    calls = [page(0, [{"id": "first"}, *([{"id": "inserted"}] if moved else [])], index, total=total, key=key)]
    ranges = [(0, one), (two, len(text))] if short else [(0, one), (one, two), (two, len(text))]
    for start, end in ranges:
        item = {"id": record["id"], "sourceIndex": index, "recordOffset": start,
                "recordJson": text[start:end], "totalCharacters": len(text),
                "recordSha256": sha256(text.encode()).hexdigest(),
                "nextRecordOffset": end if end < len(text) else None}
        call = page(index, [item], index if end < len(text) else index + 1, total=total, key=key)
        call["arguments"]["record_offset"] = start
        calls.append(call)
    calls.append(page(index + 1, [{"id": "last"}], None, total=total, key=key))
    return calls


def test_same_id_and_digest_reuses_verified_middle_chunks_during_membership_revalidation():
    result = work_evidence.evidence_collection(stream() + stream(short=True), "DEMO-1")
    assert result["complete"] and len(result["records"]) == result["total"] == 3
    assert "CANCELLED" in next(row for row in result["records"] if row["id"] == "long-1")["body"]


def test_record_position_may_change_but_identity_and_digest_must_match():
    result = work_evidence.evidence_collection(stream() + stream(short=True, moved=True), "DEMO-1")
    assert result["complete"] and len(result["records"]) == result["total"] == 4
    assert not work_evidence.evidence_collection(stream() + stream(short=True, changed=True), "DEMO-1")["complete"]
    assert not work_evidence.evidence_collection(stream() + stream(short=True, key="DEMO-2"), "DEMO-2")["complete"]
    assert not work_evidence.evidence_collection(stream() + stream(short=True)[:-1], "DEMO-1")["complete"]


def test_repeated_successful_cursor_is_a_stall_but_advancing_record_offsets_are_not():
    first = stream()[0]
    assert work_evidence.has_stalled_pagination([first, first, first])
    assert not work_evidence.has_stalled_pagination([first, first])
    assert not work_evidence.has_stalled_pagination(stream() + stream(short=True))
    failed = {**first, "result": {"ok": False}}
    assert not work_evidence.has_stalled_pagination([failed, failed, failed])
    changed = copy.deepcopy(first)
    changed["result"]["data"]["items"] = [{"id": "updated"}]
    assert not work_evidence.has_stalled_pagination([first, first, changed])


def test_correct_final_count_does_not_hide_a_stalled_pagination_loop():
    case = next(c for c in work_cases.CASES if c["id"] == "WORK-R09-normal")
    source = {"key": "DL-94015", "url": "http://127.0.0.1:8080/browse/DL-94015",
              "descriptionHtml": "prod.dw.customer_v1을 prod.dw.customers로 rename. RENAME-1 동일 테이블 확인."}
    answer = {"reply": "prod.dw.customer_v1과 prod.dw.customers는 RENAME-1로 확인된 같은 테이블 1개입니다.",
              "resultKeys": ["DL-94015"], "reportedTotal": 1, "progress": None, "operations": [],
              "sources": [{"kind": "ticket", "id": source["key"], "url": source["url"]}]}
    calls = [{"name": "get_ticket", "arguments": {"key": "DL-94015"}, "result": {"ok": True, "data": source}}]
    output = {"ok": True, "reply": answer["reply"], "structuredAnswer": answer}
    truth = build_work_data("2026-11-09")["oracle"]
    assert work_cases.check(case, output, calls, truth)["pass"]
    calls += [stream()[0]] * 3
    result = work_cases.check(case, {**output, "mcpToolCalls": calls}, calls, truth)
    assert not result["pass"] and "read_continuation_not_advancing" in result["failures"]

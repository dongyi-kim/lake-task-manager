from app.jira.comment_snapshot import embedded_comment_coverage, embedded_comment_rows


def test_embedded_comments_keep_every_raw_row_and_require_explicit_coverage():
    rows = [{"id": str(i), "body": "raw", "created": "2026-11-18T00:00:00+0000"} for i in range(25)]
    assert embedded_comment_rows(rows) is rows
    assert not embedded_comment_coverage(rows, "DL-1")["complete"]
    partial = {"comments": rows, "startAt": 0, "total": 100}
    coverage = embedded_comment_coverage(partial, "DL-1")
    assert coverage["returned"] == 25 and coverage["hasMore"] is True
    assert coverage["nextCall"]["arguments"]["start_at"] == 0
    assert embedded_comment_coverage({**partial, "total": 25}, "DL-1")["complete"]


def test_unknown_invalid_and_duplicate_comments_never_claim_complete():
    for value in (None, {}, {"comments": None}, [None]):
        assert embedded_comment_rows(value) is None
        assert not embedded_comment_coverage(value, "DL-1")["complete"]
    for rows in ([{}], [{"id": "1"}, {"id": "1"}]):
        assert not embedded_comment_coverage({"comments": rows, "startAt": 0, "total": len(rows)}, "DL-1")["complete"]
    assert embedded_comment_coverage({"comments": [], "startAt": 0, "total": 0}, "DL-1")["complete"]


def test_incomplete_content_and_contradictory_source_flags_are_not_complete():
    good = {"id": "1", "body": "", "created": "2026-11-18T00:00:00+0000"}
    for row in ({"id": "1"}, {**good, "body": None}, {**good, "created": None},
                {**good, "created": "not-a-time"}):
        assert not embedded_comment_coverage({"comments": [row], "startAt": 0, "total": 1}, "DL-1")["complete"]
    for extra in ({"complete": False}, {"hasMore": True}, {"isLast": False},
                  {"errorMessages": ["denied"]}, {"partial": True}):
        assert not embedded_comment_coverage({"comments": [good], "startAt": 0, "total": 1, **extra}, "DL-1")["complete"]


def test_invalid_container_does_not_prove_positions_and_continuation_preserves_policy():
    from app.infra.cache_policy import read_scope
    value = {"comments": "redacted", "total": 100, "startAt": 20}
    coverage = embedded_comment_coverage(value, "DL-1")
    assert coverage["returned"] == 0 and coverage["total"] is None
    assert coverage["startAt"] is None and coverage["hasMore"] is None
    for mode in ("live", "hot"):
        with read_scope(cache_mode=mode):
            assert embedded_comment_coverage([], "DL-1")["nextCall"]["arguments"]["cache_mode"] == mode

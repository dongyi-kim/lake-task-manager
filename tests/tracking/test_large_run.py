"""Large discussion bodies survive a durable manager pause, restart and warm run."""
import hashlib
import json
from datetime import datetime

from app.auth.base import UpstreamBusy
from app.tracking.analysis import KST, period_bounds
from app.tracking.storage import encode
from test_backend_manager import Provider, manager


def test_month_run_preserves_giant_comments_across_restart_polling_and_warm_cache(tmp_path, monkeypatch):
    # Keep the full fixture month valid even when the test runs before November ends.
    monkeypatch.setattr("app.tracking.manager.period_bounds", lambda start, end:
                        period_bounds(start, end, now=datetime(2026, 12, 1, tzinfo=KST)))
    bodies = ["<p>" + "가나다라마바사🙂 " * 50000 + f"끝-{index}-UNIQUE-COMMENT-TAIL</p>"
              for index in range(2)]
    fingerprints = [(len(body), len(body.encode("utf-8")), hashlib.sha256(body.encode("utf-8")).digest())
                    for body in bodies]
    assert all(1024 * 1024 < size < 2 * 1024 * 1024 for _, size, _ in fingerprints)

    class LargeCommentsProvider(Provider):
        interrupted = False

        def get_json(self, path, params=None):
            if not path.endswith("/comment"):
                return super().get_json(path, params)
            self.calls.append((path, dict(params or {})))
            offset = int((params or {}).get("startAt", 0))
            if offset == 1 and not self.interrupted:
                self.interrupted = True
                raise UpstreamBusy("comment page temporarily busy")
            rows = [{"id": str(index + 1), "created": "2026-11-11T10:00:00+0900",
                     "author": {"name": "a"}, "body": body, "renderedBody": body}
                    for index, body in enumerate(bodies)]
            return {"startAt": offset, "maxResults": 1, "total": 2,
                    "comments": rows[offset:offset + 1]}

    def assert_complete_body(value, index):
        encoded = value.encode("utf-8")
        assert (len(value), len(encoded), hashlib.sha256(encoded).digest()) == fingerprints[index]

    def assert_result(result, expected_count):
        comments = sorted((event for event in result["events"] if event["kind"] == "comment"),
                          key=lambda event: int(event["commentId"]))
        assert result["summary"]["comments"] == len(comments) == expected_count
        for index, event in enumerate(comments):
            assert event["commentId"] == str(index + 1)
            assert_complete_body(event["body"], index)
            assert_complete_body(event["bodyHtml"], index)
            assert event["bodyHtml"].endswith(f"끝-{index}-UNIQUE-COMMENT-TAIL</p>")

    provider = LargeCommentsProvider()
    initial = manager(tmp_path, provider)
    paused = initial.create({"startDate": "2026-11-01", "endDate": "2026-11-30"}, background=False)
    assert paused["status"] == "paused" and not paused["coverage"]["complete"]
    assert_result(paused, 1)
    _, _, snapshot, checkpoint = initial.store.read(paused["id"])
    checkpoint = json.loads(encode(checkpoint))
    source = checkpoint["sources"]["P-1"]
    assert len(source["comments"]) == 1
    assert_complete_body(source["comments"][0]["body"], 0)
    assert len(source["commentPages"]) == 1 and source["commentPages"][0]["rowCount"] == 1
    assert "comments" not in source["commentPages"][0]
    assert not source.get("commentsComplete")
    initial.store.save(paused["id"], snapshot, checkpoint)
    restarted = manager(tmp_path, provider)
    calls_before_poll = len(provider.calls)
    assert_result(restarted.get(paused["id"]), 1)
    assert len(provider.calls) == calls_before_poll
    resumed = restarted.resume(paused["id"], background=False)
    assert resumed["status"] == "complete" and resumed["coverage"]["complete"]
    assert resumed["asOf"] == paused["asOf"]
    assert_result(resumed, 2)
    comment_calls = [(path, params) for path, params in provider.calls if path.endswith("/comment")]
    assert [params["startAt"] for _, params in comment_calls] == [0, 1, 1]
    assert all(params["maxResults"] == 100 for _, params in comment_calls)
    completed_source = restarted.store.read(paused["id"])[3]["sources"]["P-1"]
    assert len(completed_source["comments"]) == 2
    for index, comment in enumerate(completed_source["comments"]):
        assert_complete_body(comment["body"], index)
        assert_complete_body(comment["renderedBody"], index)
    calls_before_poll = len(provider.calls)
    assert_result(restarted.get(paused["id"]), 2)
    assert len(provider.calls) == calls_before_poll
    warm = manager(tmp_path, provider).create(paused["request"], background=False)
    assert warm["status"] == "complete" and warm["metrics"]["cacheHits"] > 0
    assert_result(warm, 2)
    assert [(path, params) for path, params in provider.calls if path.endswith("/comment")] == comment_calls

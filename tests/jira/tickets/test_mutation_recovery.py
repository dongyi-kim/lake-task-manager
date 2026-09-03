"""Response-lost Jira writes reconcile before retrying.

These tests model the production failure precisely: Jira commits a comment/issue, then the
transport raises before LTM receives the response.  A retry with the same client mutation id must
return that object without a second POST.
"""

import itertools
import queue
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.auth.base import MutationOutcomeUnknown, SessionExpired, UpstreamUnavailable
from app.auth.sso_session import SsoSessionProvider
from app.infra.cache import Cache
from app.jira.jira_client import JiraClient
from app.jira.mutation_recovery import (
    ABSENT,
    UNKNOWN,
    reconcile_attachment,
    reconcile_comment,
    reconcile_created_issue,
    reconcile_transition,
)


_ACTOR = {"id": "tester", "aliases": ["tester", "user-key-1"]}
_AUTHOR = {"name": "tester", "key": "user-key-1"}


def _settings():
    return SimpleNamespace(
        jira_env="prod", project_key="DL", epic_link_field_id="customfield_10014",
        epic_name_field_id="customfield_10015", sp_field_id="customfield_10016",
        cache_ttl_seconds=900,
    )


def _now():
    # Fake Jira commits after LTM has recorded attemptedAt. Keep that causal order explicit rather
    # than relying on two wall-clock APIs whose sub-microsecond rounding can invert equal instants.
    return datetime.fromtimestamp(time.time() + 0.01, timezone.utc).isoformat()


def _client(provider):
    client = JiraClient(_settings(), Cache(":memory:"))
    client._provider = provider
    client._provider_built = True
    # The tests isolate transport/reconciliation. Cache dependency behavior has its own suite.
    client._invalidate_ticket = lambda *_args, **_kwargs: None
    client._invalidate_people_views = lambda: None
    client._record_mutation = lambda event: event
    client.mutation_actor_identity = lambda: dict(_ACTOR)
    client.assert_mutation_actor_identity = lambda expected: expected == _ACTOR
    return client


class _CommentProvider:
    def __init__(self, *, commit_before_failure=True):
        self.comments = []
        self.posts = 0
        self.gets = 0
        self.commit_before_failure = commit_before_failure

    def post_json(self, path, payload):
        self.posts += 1
        if self.posts == 1:
            if self.commit_before_failure:
                self.comments.insert(0, {
                    "id": "c-1", "body": payload["body"], "created": _now(),
                    "author": dict(_AUTHOR),
                })
            raise UpstreamUnavailable("response lost after Jira commit")
        row = {"id": f"c-{self.posts}", "body": payload["body"], "created": _now(),
               "author": dict(_AUTHOR)}
        self.comments.insert(0, row)
        return row

    def get_json(self, path, params=None, **_kwargs):
        self.gets += 1
        return {"startAt": 0, "total": len(self.comments), "comments": self.comments}


def test_comment_response_loss_recovers_same_comment_without_second_post():
    provider = _CommentProvider()
    client = _client(provider)
    mutation_id = "ltm-comment:11111111-1111-1111-1111-111111111111"

    with pytest.raises(MutationOutcomeUnknown):
        client.add_comment("DL-1", "동일 본문", mutation_id=mutation_id)

    recovered = client.add_comment("DL-1", "동일 본문", mutation_id=mutation_id)
    replayed = client.add_comment("DL-1", "동일 본문", mutation_id=mutation_id)

    assert recovered["id"] == replayed["id"] == "c-1"
    assert provider.posts == 1
    assert provider.gets == 1                 # third call is a durable success-receipt hit


def test_proven_absent_comment_is_written_once_on_retry():
    provider = _CommentProvider(commit_before_failure=False)
    client = _client(provider)
    mutation_id = "ltm-comment:22222222-2222-2222-2222-222222222222"

    with pytest.raises(MutationOutcomeUnknown):
        client.add_comment("DL-2", "재시도 본문", mutation_id=mutation_id)
    receipt_key = "v1:prod:comment-create:" + mutation_id
    receipt = client.cache.mutation_receipt(receipt_key)
    client.cache.set_mutation_receipt(
        receipt_key, receipt["fingerprint"], "pending", None,
        receipt["attemptedAt"] - 60, 3600,
    )
    result = client.add_comment("DL-2", "재시도 본문", mutation_id=mutation_id)

    assert result["id"] == "c-2"
    assert provider.posts == 2                # first uncertain + one proven-safe retry


def test_fresh_empty_reconciliation_waits_for_jira_index_instead_of_duplicate_post():
    provider = _CommentProvider(commit_before_failure=False)
    client = _client(provider)
    mutation_id = "ltm-comment:77777777-7777-7777-7777-777777777777"

    with pytest.raises(MutationOutcomeUnknown):
        client.add_comment("DL-2", "색인 대기", mutation_id=mutation_id)
    with pytest.raises(MutationOutcomeUnknown, match="검색 반영"):
        client.add_comment("DL-2", "색인 대기", mutation_id=mutation_id)

    assert provider.posts == 1


class _DelayedCommitSsoProvider(SsoSessionProvider):
    """Minimal owner-thread harness for the exact JOB_TIMEOUT/late-commit race."""

    JOB_TIMEOUT = 0.04

    def __init__(self):
        # Deliberately avoid Playwright startup; _submit and its real queue/handle contract are the
        # unit under test. The owner performs one Jira-like write after the caller has timed out.
        self.base = "http://jira.example"
        self._jobs = queue.PriorityQueue()
        self._seq = itertools.count()
        self._broken = threading.Event()
        self._closed = threading.Event()
        self._broken_reason = ""
        self.started = threading.Event()
        self.release = threading.Event()
        self.owner_done = threading.Event()
        self.posts = 0
        self._thread = threading.Thread(target=self._owner, daemon=True)
        self._thread.start()

    def _owner(self):
        _priority, _sequence, job = self._jobs.get(timeout=1)
        fn, done, box = job
        try:
            box[0] = fn()
        except BaseException as exc:  # noqa: BLE001 - mirrors the production owner loop
            box[1] = exc
        finally:
            done.set()
            self.owner_done.set()

    def _write(self, method, path, json_body=None, params=None, want_json=True):
        assert method == "post" and path.endswith("/comment")
        self.posts += 1
        self.started.set()
        assert self.release.wait(2), "test did not release delayed Jira commit"
        return {
            "id": "late-c-1", "body": json_body["body"], "created": _now(),
            "author": dict(_AUTHOR),
        }

    def get_json(self, *_args, **_kwargs):
        raise AssertionError("an in-process live write must be observed before reconciliation")


def test_sso_job_timeout_never_reposts_while_old_owner_can_still_commit():
    provider = _DelayedCommitSsoProvider()
    client = _client(provider)
    mutation_id = "ltm-comment:eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"

    with pytest.raises(MutationOutcomeUnknown):
        client.add_comment("DL-99", "늦게 반영되는 댓글", mutation_id=mutation_id)
    assert provider.started.is_set() and provider.posts == 1

    # Even an artificially old receipt may not use authoritative-absence logic while the actual
    # owner thread is still able to commit this request.
    receipt_key = "v1:prod:comment-create:" + mutation_id
    receipt = client.cache.mutation_receipt(receipt_key)
    client.cache.set_mutation_receipt(
        receipt_key, receipt["fingerprint"], "pending", None,
        receipt["attemptedAt"] - 120, 3600,
    )
    started = time.monotonic()
    with pytest.raises(MutationOutcomeUnknown, match="아직 처리 중"):
        client.add_comment("DL-99", "늦게 반영되는 댓글", mutation_id=mutation_id)
    assert time.monotonic() - started < 0.2
    assert provider.posts == 1

    provider.release.set()
    assert provider.owner_done.wait(1)
    recovered = client.add_comment(
        "DL-99", "늦게 반영되는 댓글", mutation_id=mutation_id)
    replayed = client.add_comment(
        "DL-99", "늦게 반영되는 댓글", mutation_id=mutation_id)

    assert recovered == replayed
    assert recovered["id"] == "late-c-1"
    assert provider.posts == 1
    assert client.cache.mutation_receipt(receipt_key)["state"] == "success"


def test_timed_out_preflight_read_is_not_exposed_as_a_committed_write_handle():
    provider = _DelayedCommitSsoProvider()

    def delayed_read():
        provider.started.set()
        assert provider.release.wait(2)
        return {"transitions": [{"id": "31"}]}

    with pytest.raises(UpstreamUnavailable) as raised:
        provider._submit(delayed_read)

    assert provider.started.is_set()
    assert not hasattr(raised.value, "pending_operation")
    provider.release.set()
    assert provider.owner_done.wait(1)


class _IssueProvider:
    def __init__(self):
        self.posts = 0
        self.issues = []

    def post_json(self, path, payload):
        self.posts += 1
        issue = {
            "id": "9200", "key": "DL-9200", "self": "http://jira/issue/9200",
            "fields": {**payload["fields"], "created": _now(),
                       "creator": dict(_AUTHOR)},
        }
        self.issues.insert(0, issue)
        raise UpstreamUnavailable("response lost after create")

    def get_json(self, path, params=None, **_kwargs):
        assert path == "/rest/api/2/search"
        return {"startAt": 0, "total": len(self.issues), "issues": self.issues}


def test_task_response_loss_recovers_created_key_without_duplicate_issue():
    provider = _IssueProvider()
    client = _client(provider)
    mutation_id = "ltm-issue:33333333-3333-3333-3333-333333333333"

    with pytest.raises(MutationOutcomeUnknown):
        client.create_child(
            None, "Task", "응답 유실 Task", priority="P2-Major",
            components=["Workbench"], mutation_id=mutation_id,
        )
    result = client.create_child(
        None, "Task", "응답 유실 Task", priority="P2-Major",
        components=["Workbench"], mutation_id=mutation_id,
    )

    assert result == {"key": "DL-9200"}
    assert provider.posts == 1


def test_pending_issue_reconciles_before_parent_permission_preflight_runs_again():
    provider = _IssueProvider()
    client = _client(provider)
    mutation_id = "ltm-issue:99999999-9999-9999-9999-999999999999"
    preflight_calls = []

    with pytest.raises(MutationOutcomeUnknown):
        client.create_child(
            "DL-10", "Sub-Task", "응답 유실 SubTask", mutation_id=mutation_id,
            parent_is_epic=False, before_write=lambda: preflight_calls.append("first"),
        )
    result = client.create_child(
        "DL-10", "Sub-Task", "응답 유실 SubTask", mutation_id=mutation_id,
        parent_is_epic=False,
        before_write=lambda: (_ for _ in ()).throw(AssertionError("must not rerun")),
    )

    assert result == {"key": "DL-9200"}
    assert preflight_calls == ["first"]
    assert provider.posts == 1


def test_issue_reconciliation_ignores_jira_defaults_for_omitted_optional_fields():
    provider = _IssueProvider()
    client = _client(provider)
    mutation_id = "ltm-issue:88888888-8888-8888-8888-888888888888"

    with pytest.raises(MutationOutcomeUnknown):
        client.create_child(None, "Task", "기본값 Task", mutation_id=mutation_id)
    # Jira project defaults were not part of the user's logical payload and must not disqualify it.
    fields = provider.issues[0]["fields"]
    fields["priority"] = {"name": "P2-Major"}
    fields["assignee"] = {"name": "auto-assignee"}
    fields["components"] = [{"name": "Default component"}]
    fields["labels"] = ["jira-default"]

    result = client.create_child(None, "Task", "기본값 Task", mutation_id=mutation_id)

    assert result == {"key": "DL-9200"}
    assert provider.posts == 1


def test_same_mutation_id_cannot_be_reused_for_different_payload():
    provider = _IssueProvider()
    client = _client(provider)
    mutation_id = "ltm-issue:44444444-4444-4444-4444-444444444444"
    with pytest.raises(MutationOutcomeUnknown):
        client.create_child(None, "Task", "첫 제목", mutation_id=mutation_id)

    with pytest.raises(ValueError, match="다른 요청"):
        client.create_child(None, "Task", "바뀐 제목", mutation_id=mutation_id)
    assert provider.posts == 1


class _AuthThenSuccessProvider:
    def __init__(self):
        self.posts = 0

    def post_json(self, _path, payload):
        self.posts += 1
        if self.posts == 1:
            raise SessionExpired("HTTP 401")
        return {"id": "c-ok", "body": payload["body"], "created": _now(),
                "author": dict(_AUTHOR)}


def test_explicit_401_clears_pending_receipt_for_login_retry():
    provider = _AuthThenSuccessProvider()
    client = _client(provider)
    mutation_id = "ltm-comment:55555555-5555-5555-5555-555555555555"

    with pytest.raises(SessionExpired):
        client.add_comment("DL-3", "로그인 뒤 재시도", mutation_id=mutation_id)
    result = client.add_comment("DL-3", "로그인 뒤 재시도", mutation_id=mutation_id)

    assert result["id"] == "c-ok"
    assert provider.posts == 2


@pytest.mark.parametrize(
    ("reconcile_error", "expected"),
    [
        (SessionExpired("HTTP 401 while checking"), SessionExpired),
        (UpstreamUnavailable("timeout while checking"), MutationOutcomeUnknown),
        (ValueError("malformed reconciliation response"), MutationOutcomeUnknown),
    ],
)
def test_pending_reconciliation_failure_never_authorizes_duplicate_write(
        reconcile_error, expected):
    provider = _CommentProvider(commit_before_failure=False)
    client = _client(provider)
    mutation_id = "ltm-comment:66666666-6666-6666-6666-666666666666"

    with pytest.raises(MutationOutcomeUnknown):
        client.add_comment("DL-4", "확인 중인 댓글", mutation_id=mutation_id)
    provider.get_json = lambda *_args, **_kwargs: (_ for _ in ()).throw(reconcile_error)

    with pytest.raises(expected):
        client.add_comment("DL-4", "확인 중인 댓글", mutation_id=mutation_id)

    assert provider.posts == 1
    receipt = client.cache.mutation_receipt(
        "v1:prod:comment-create:" + mutation_id)
    assert receipt and receipt["state"] == "pending"


def test_pending_reconciliation_401_then_login_recovers_with_same_id():
    provider = _CommentProvider()
    client = _client(provider)
    mutation_id = "ltm-comment:cccccccc-cccc-cccc-cccc-cccccccccccc"

    with pytest.raises(MutationOutcomeUnknown):
        client.add_comment("DL-4", "로그인 복구", mutation_id=mutation_id)
    original_get = provider.get_json
    calls = 0

    def auth_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise SessionExpired("HTTP 401 while reconciling")
        return original_get(*args, **kwargs)

    provider.get_json = auth_once
    with pytest.raises(SessionExpired):
        client.add_comment("DL-4", "로그인 복구", mutation_id=mutation_id)
    result = client.add_comment("DL-4", "로그인 복구", mutation_id=mutation_id)

    assert result["id"] == "c-1"
    assert provider.posts == 1


def test_refreshing_normal_cache_does_not_remove_mutation_receipt():
    cache = Cache(":memory:")
    cache.set_mutation_receipt(
        "write-1", "hash", "pending", None, 10.0, 3600,
        context={"transitionId": "31", "targetStatusCategory": "done"},
    )
    # Moving a receipt's reconciliation timestamp must preserve its frozen Jira authority.
    cache.set_mutation_receipt("write-1", "hash", "pending", None, 20.0, 3600)
    cache.set("issue:prod:DL-1", {"key": "DL-1"}, 60)

    cache.invalidate()

    assert cache.get("issue:prod:DL-1") is None
    receipt = cache.mutation_receipt("write-1")
    assert receipt["state"] == "pending"
    assert receipt["context"] == {
        "transitionId": "31", "targetStatusCategory": "done",
    }


class _MalformedSuccessCommentProvider(_CommentProvider):
    def post_json(self, path, payload):
        self.posts += 1
        self.comments.insert(0, {
            "id": "c-created", "body": payload["body"], "created": _now(),
            "author": dict(_AUTHOR),
        })
        return {}                              # 2xx body was lost/malformed after Jira committed


def test_malformed_success_body_stays_pending_and_reconciles_without_second_post():
    provider = _MalformedSuccessCommentProvider()
    client = _client(provider)
    mutation_id = "ltm-comment:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

    with pytest.raises(MutationOutcomeUnknown):
        client.add_comment("DL-5", "malformed 2xx", mutation_id=mutation_id)
    result = client.add_comment("DL-5", "malformed 2xx", mutation_id=mutation_id)

    assert result["id"] == "c-created"
    assert provider.posts == 1


class _AttachmentProvider:
    def __init__(self, error=UpstreamUnavailable("attachment response lost")):
        self.error = error
        self.posts = 0
        self.attachments = []

    def post_multipart(self, _path, filename, data, _content_type):
        self.posts += 1
        row = {
            "id": "a-1", "filename": filename, "size": len(data), "created": _now(),
            "author": dict(_AUTHOR),
        }
        self.attachments.append(row)
        if self.posts == 1 and self.error:
            raise self.error
        return [row]

    def get_json(self, *_args, **_kwargs):
        return {"fields": {"attachment": list(self.attachments)}}


@pytest.mark.parametrize("error", [
    UpstreamUnavailable("attachment response lost"), TimeoutError("raw socket timeout"),
])
def test_attachment_response_loss_reconciles_without_duplicate_upload(error):
    provider = _AttachmentProvider(error)
    client = _client(provider)
    mutation_id = "ltm-attachment:dddddddd-dddd-dddd-dddd-dddddddddddd"

    with pytest.raises(MutationOutcomeUnknown):
        client.upload_attachment(
            "DL-8", "proof.png", b"image-bytes", "image/png", mutation_id=mutation_id)
    result = client.upload_attachment(
        "DL-8", "proof.png", b"image-bytes", "image/png", mutation_id=mutation_id)

    assert result[0]["id"] == "a-1"
    assert provider.posts == 1


def test_committed_receipt_retries_cache_cleanup_but_success_replay_does_not_repeat_it():
    provider = _AuthThenSuccessProvider()
    provider.posts = 1                       # next POST returns a normal success
    client = _client(provider)
    mutation_id = "ltm-comment:bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    calls = []

    def cleanup(*_args, **_kwargs):
        calls.append("cleanup")
        if len(calls) == 1:
            raise RuntimeError("cache temporarily locked")

    client._invalidate_ticket = cleanup
    with pytest.raises(MutationOutcomeUnknown, match="캐시 갱신"):
        client.add_comment("DL-6", "cleanup resume", mutation_id=mutation_id)
    recovered = client.add_comment("DL-6", "cleanup resume", mutation_id=mutation_id)
    replayed = client.add_comment("DL-6", "cleanup resume", mutation_id=mutation_id)

    assert recovered == replayed
    assert provider.posts == 2               # one actual POST after the seeded counter
    assert calls == ["cleanup", "cleanup"]  # success replay performs no third cleanup


def test_partial_comment_page_without_descending_order_cannot_prove_absence():
    now = datetime.now(timezone.utc).timestamp()
    rows = [
        {"id": str(i), "body": "other", "created": datetime.fromtimestamp(
            now - 3600 + i, timezone.utc).isoformat()}
        for i in range(100)
    ]

    class Provider:
        def get_json(self, *_args, **_kwargs):
            return {"startAt": 0, "total": 101, "comments": rows}

    result = reconcile_comment(
        Provider(), "DL-7", "missing", {"attemptedAt": now}, _ACTOR, cap=100)
    assert result.state == UNKNOWN


def test_comment_reconciliation_does_not_claim_another_authors_identical_body():
    class Provider:
        def get_json(self, *_args, **_kwargs):
            return {"startAt": 0, "total": 1, "comments": [{
                "id": "foreign-c", "body": "동시 동일 댓글", "created": _now(),
                "author": {"name": "someone.else"},
            }]}

    checked = reconcile_comment(
        Provider(), "DL-70", "동시 동일 댓글", {"attemptedAt": time.time()}, _ACTOR)

    assert checked.state == ABSENT


def test_comment_reconciliation_fails_closed_when_matching_author_is_unidentifiable():
    class Provider:
        def get_json(self, *_args, **_kwargs):
            return {"startAt": 0, "total": 1, "comments": [{
                "id": "ambiguous-c", "body": "동시 동일 댓글", "created": _now(),
            }]}

    checked = reconcile_comment(
        Provider(), "DL-70", "동시 동일 댓글", {"attemptedAt": time.time()}, _ACTOR)

    assert checked.state == UNKNOWN


def test_comment_reconciliation_never_claims_same_actor_content_from_before_attempt():
    attempted = time.time()

    class Provider:
        def get_json(self, *_args, **_kwargs):
            return {"startAt": 0, "total": 1, "comments": [{
                "id": "old-c", "body": "반복 템플릿", "author": dict(_AUTHOR),
                "created": datetime.fromtimestamp(
                    attempted - 60, timezone.utc).isoformat(),
            }]}

    checked = reconcile_comment(
        Provider(), "DL-70", "반복 템플릿", {"attemptedAt": attempted}, _ACTOR)

    assert checked.state == UNKNOWN


def test_comment_reconciliation_rejects_multiple_post_attempt_candidates():
    attempted = time.time()

    class Provider:
        def get_json(self, *_args, **_kwargs):
            return {"startAt": 0, "total": 2, "comments": [
                {"id": "c-2", "body": "동일 댓글", "author": dict(_AUTHOR),
                 "created": datetime.fromtimestamp(
                     attempted + 2, timezone.utc).isoformat()},
                {"id": "c-1", "body": "동일 댓글", "author": dict(_AUTHOR),
                 "created": datetime.fromtimestamp(
                     attempted + 1, timezone.utc).isoformat()},
            ]}

    checked = reconcile_comment(
        Provider(), "DL-70", "동일 댓글", {"attemptedAt": attempted}, _ACTOR)

    assert checked.state == UNKNOWN


def test_attachment_reconciliation_requires_the_original_uploader():
    class Provider:
        def __init__(self, author):
            self.author = author

        def get_json(self, *_args, **_kwargs):
            row = {
                "id": "a-foreign", "filename": "same.png", "size": 42,
                "created": _now(),
            }
            if self.author is not None:
                row["author"] = self.author
            return {"fields": {"attachment": [row]}}

    foreign = reconcile_attachment(
        Provider({"name": "someone.else"}), "DL-71", "same.png", 42,
        {"attemptedAt": time.time()}, _ACTOR)
    ambiguous = reconcile_attachment(
        Provider(None), "DL-71", "same.png", 42,
        {"attemptedAt": time.time()}, _ACTOR)

    assert foreign.state == ABSENT
    assert ambiguous.state == UNKNOWN


def test_attachment_reconciliation_never_claims_same_actor_file_from_before_attempt():
    attempted = time.time()

    class Provider:
        def get_json(self, *_args, **_kwargs):
            return {"fields": {"attachment": [{
                "id": "old-a", "filename": "template.png", "size": 42,
                "author": dict(_AUTHOR),
                "created": datetime.fromtimestamp(
                    attempted - 60, timezone.utc).isoformat(),
            }]}}

    checked = reconcile_attachment(
        Provider(), "DL-71", "template.png", 42,
        {"attemptedAt": attempted}, _ACTOR)

    assert checked.state == UNKNOWN


def test_issue_reconciliation_requires_the_original_creator():
    expected = {"summary": "동시 동일 Task", "issuetype": {"name": "Task"}}

    class Provider:
        def __init__(self, creator):
            self.creator = creator

        def get_json(self, *_args, **_kwargs):
            fields = {**expected, "created": _now()}
            if self.creator is not None:
                fields["creator"] = self.creator
            return {"startAt": 0, "total": 1, "issues": [{
                "id": "foreign-i", "key": "DL-777", "fields": fields,
            }]}

    foreign = reconcile_created_issue(
        Provider({"name": "someone.else"}), "DL", expected, "",
        {"attemptedAt": time.time()}, _ACTOR)
    ambiguous = reconcile_created_issue(
        Provider(None), "DL", expected, "",
        {"attemptedAt": time.time()}, _ACTOR)

    assert foreign.state == ABSENT
    assert ambiguous.state == UNKNOWN


def test_issue_reconciliation_never_claims_same_actor_template_from_before_attempt():
    attempted = time.time()
    expected = {"summary": "반복 Task", "issuetype": {"name": "Task"}}

    class Provider:
        def get_json(self, *_args, **_kwargs):
            return {"startAt": 0, "total": 1, "issues": [{
                "id": "old-i", "key": "DL-700",
                "fields": {
                    **expected, "creator": dict(_AUTHOR),
                    "created": datetime.fromtimestamp(
                        attempted - 60, timezone.utc).isoformat(),
                },
            }]}

    checked = reconcile_created_issue(
        Provider(), "DL", expected, "", {"attemptedAt": attempted}, _ACTOR)

    assert checked.state == UNKNOWN


def test_identity_failure_before_first_attempt_creates_no_pending_receipt():
    provider = _CommentProvider()
    client = _client(provider)
    mutation_id = "ltm-comment:abababab-abab-abab-abab-abababababab"
    client.mutation_actor_identity = lambda: (_ for _ in ()).throw(
        SessionExpired("identity unavailable"))

    with pytest.raises(SessionExpired):
        client.add_comment("DL-72", "전송 전 인증 실패", mutation_id=mutation_id)

    assert provider.posts == 0
    assert client.cache.mutation_receipt(
        "v1:prod:comment-create:" + mutation_id) is None


def test_proven_absent_retry_refuses_a_different_authenticated_actor():
    provider = _CommentProvider(commit_before_failure=False)
    client = _client(provider)
    mutation_id = "ltm-comment:cdcdcdcd-cdcd-cdcd-cdcd-cdcdcdcdcdcd"

    with pytest.raises(MutationOutcomeUnknown):
        client.add_comment("DL-73", "계정 변경 중", mutation_id=mutation_id)
    receipt_key = "v1:prod:comment-create:" + mutation_id
    receipt = client.cache.mutation_receipt(receipt_key)
    client.cache.set_mutation_receipt(
        receipt_key, receipt["fingerprint"], "pending", None,
        receipt["attemptedAt"] - 120, 3600,
    )
    client.assert_mutation_actor_identity = lambda _expected: (_ for _ in ()).throw(
        ValueError("Jira 로그인 사용자가 이전 요청과 다릅니다."))

    with pytest.raises(ValueError, match="이전 요청과 다릅니다"):
        client.add_comment("DL-73", "계정 변경 중", mutation_id=mutation_id)

    assert provider.posts == 1
    assert client.cache.mutation_receipt(receipt_key)["state"] == "pending"


@pytest.mark.parametrize("method", ["priorities", "issue_types", "project_issue_types"])
def test_creation_options_propagate_idle_auth_instead_of_caching_empty_list(method):
    class Provider:
        def get_json(self, *_args, **_kwargs):
            raise SessionExpired("idle session expired")

    client = _client(Provider())
    with pytest.raises(SessionExpired):
        getattr(client, method)()


class _TransitionProvider:
    def __init__(self, *, commit_before_failure=True, keep_available_after_commit=False):
        self.posts = 0
        self.status = {"id": "1", "name": "Open", "statusCategory": {"key": "new"}}
        self.available = True
        self.commit_before_failure = commit_before_failure
        self.keep_available_after_commit = keep_available_after_commit
        self.last_body = None
        self.comments = []

    def _transition(self):
        return {
            "id": "31", "name": "Resolve",
            "to": {"id": "5", "name": "Resolved",
                   "statusCategory": {"key": "done"}},
            # Models the reported prod shape: resolution is declared but comment is omitted.
            "fields": {
                "customfield_resolution": {
                    "name": "Resolution", "required": True,
                    "schema": {"type": "resolution", "system": "resolution"},
                    "allowedValues": [{"id": "1", "name": "Done"}],
                },
            },
        }

    def get_json(self, path, params=None, **_kwargs):
        if path.endswith("/transitions"):
            return {"transitions": [self._transition()] if self.available else []}
        if path.endswith("/comment"):
            return {"startAt": 0, "total": len(self.comments),
                    "comments": list(reversed(self.comments))}
        if path.endswith("/issue/DL-30"):
            return {"key": "DL-30", "fields": {"status": dict(self.status)}}
        raise AssertionError(path)

    def post_json(self, path, payload):
        assert path.endswith("/issue/DL-30/transitions")
        self.posts += 1
        self.last_body = payload
        comment_updates = ((payload.get("update") or {}).get("comment") or [])
        comment = (((comment_updates[0] or {}).get("add") or {}).get("body")
                   if comment_updates else "")
        if self.posts == 1:
            if self.commit_before_failure:
                self.status = {
                    "id": "5", "name": "Resolved", "statusCategory": {"key": "done"},
                }
                self.available = self.keep_available_after_commit
                if comment:
                    self.comments.append({
                        "id": "c-1", "body": comment, "created": _now(),
                        "author": dict(_AUTHOR),
                    })
            raise UpstreamUnavailable("response lost after transition commit")
        self.status = {"id": "5", "name": "Resolved", "statusCategory": {"key": "done"}}
        self.available = False
        if comment:
            self.comments.append({
                "id": f"c-{self.posts}", "body": comment, "created": _now(),
                "author": dict(_AUTHOR),
            })
        return {}                              # Jira transition normally responds 204


def _transition_kwargs(mutation_id):
    return {
        "resolution": "Done", "comment": "완료 기록",
        "target_status_id": "5", "target_status_name": "Resolved",
        "target_status_category": "done", "mutation_id": mutation_id,
    }


def test_transition_response_loss_reconciles_status_without_duplicate_transition():
    provider = _TransitionProvider()
    client = _client(provider)
    mutation_id = "ltm-transition:11111111-1111-1111-1111-111111111111"

    with pytest.raises(MutationOutcomeUnknown):
        client.do_transition("DL-30", "31", **_transition_kwargs(mutation_id))
    recovered = client.do_transition("DL-30", "31", **_transition_kwargs(mutation_id))
    replayed = client.do_transition("DL-30", "31", **_transition_kwargs(mutation_id))

    assert recovered == replayed == {"ok": True}
    assert provider.posts == 1
    assert [comment["body"] for comment in provider.comments] == ["완료 기록"]


def test_transition_reconciliation_fails_closed_when_same_id_remains_available():
    provider = _TransitionProvider(keep_available_after_commit=True)
    client = _client(provider)
    mutation_id = "ltm-transition:22222222-2222-2222-2222-222222222222"

    with pytest.raises(MutationOutcomeUnknown):
        client.do_transition("DL-30", "31", **_transition_kwargs(mutation_id))
    with pytest.raises(MutationOutcomeUnknown, match="확인 중"):
        client.do_transition("DL-30", "31", **_transition_kwargs(mutation_id))

    assert provider.posts == 1                 # ambiguous global/self transition is never repeated


def test_transition_reconciliation_does_not_claim_success_when_required_comment_is_missing():
    provider = _TransitionProvider()
    client = _client(provider)
    mutation_id = "ltm-transition:aaaaaaaa-1111-1111-1111-111111111111"

    with pytest.raises(MutationOutcomeUnknown):
        client.do_transition("DL-30", "31", **_transition_kwargs(mutation_id))
    provider.comments.clear()                  # models Jira accepting status but losing the note

    with pytest.raises(MutationOutcomeUnknown, match="확인 중"):
        client.do_transition("DL-30", "31", **_transition_kwargs(mutation_id))

    assert provider.posts == 1                 # status already changed: never risk a second transition


def test_proven_absent_transition_can_retry_after_consistency_grace():
    provider = _TransitionProvider(commit_before_failure=False)
    client = _client(provider)
    mutation_id = "ltm-transition:33333333-3333-3333-3333-333333333333"

    with pytest.raises(MutationOutcomeUnknown):
        client.do_transition("DL-30", "31", **_transition_kwargs(mutation_id))
    receipt_key = "v1:prod:transition:" + mutation_id
    receipt = client.cache.mutation_receipt(receipt_key)
    client.cache.set_mutation_receipt(
        receipt_key, receipt["fingerprint"], "pending", None,
        receipt["attemptedAt"] - 60, 3600,
    )

    result = client.do_transition("DL-30", "31", **_transition_kwargs(mutation_id))

    assert result == {"ok": True}
    assert provider.posts == 2


def test_done_transition_sends_comment_when_prod_screen_metadata_omits_comment():
    provider = _TransitionProvider(commit_before_failure=False)
    provider.posts = 1                         # next POST is a confirmed 204 success
    client = _client(provider)

    transitions = client.transitions("DL-30")
    result = client.do_transition(
        "DL-30", "31", resolution="Done", comment="완료 근거",
        target_status_id="5", target_status_name="Resolved",
        target_status_category="done",
    )

    assert transitions[0]["toId"] == "5"
    assert result == {"ok": True}
    assert provider.last_body["fields"]["resolution"] == {"name": "Done"}
    assert provider.last_body["update"]["comment"] == [{"add": {"body": "완료 근거"}}]


def test_done_policy_comes_from_fresh_jira_metadata_not_client_hint():
    provider = _TransitionProvider(commit_before_failure=False)
    client = _client(provider)

    with pytest.raises(ValueError, match="상태 분류"):
        client.do_transition(
            "DL-30", "31", resolution="Done", comment="완료 근거",
            target_status_id="5", target_status_name="Resolved",
            # A stale/forged browser cannot disguise the authoritative Done destination.
            target_status_category="todo",
            mutation_id="ltm-transition:bbbbbbbb-1111-1111-1111-111111111111",
        )

    assert provider.posts == 0
    assert client.cache.mutation_receipt(
        "v1:prod:transition:ltm-transition:bbbbbbbb-1111-1111-1111-111111111111") is None


def test_done_comment_is_server_required_even_when_client_omits_target_hints():
    provider = _TransitionProvider(commit_before_failure=False)
    client = _client(provider)

    with pytest.raises(ValueError, match="코멘트"):
        client.do_transition("DL-30", "31", resolution="Done")

    assert provider.posts == 0


def test_pending_transition_receipt_freezes_authoritative_target_and_screen():
    provider = _TransitionProvider()
    client = _client(provider)
    mutation_id = "ltm-transition:cccccccc-1111-1111-1111-111111111111"

    with pytest.raises(MutationOutcomeUnknown):
        client.do_transition("DL-30", "31", **_transition_kwargs(mutation_id))

    receipt = client.cache.mutation_receipt("v1:prod:transition:" + mutation_id)
    assert receipt["context"] == {
        "actor": _ACTOR,
        "authority": {
            "transitionId": "31",
            "targetStatusId": "5",
            "targetStatusName": "Resolved",
            "targetStatusCategory": "done",
            "allowedFields": ["customfield_resolution", "resolution"],
            "requiredFields": ["resolution"],
            "resolutionValues": ["Done"],
        },
    }


def test_non_done_transition_cannot_gain_comment_policy_from_forged_client_hint():
    provider = _TransitionProvider(commit_before_failure=False)
    provider._transition = lambda: {
        "id": "31", "name": "Start Progress",
        "to": {"id": "3", "name": "In Progress",
               "statusCategory": {"key": "indeterminate"}},
        "fields": {},
    }
    client = _client(provider)

    with pytest.raises(ValueError, match="상태 분류"):
        client.do_transition(
            "DL-30", "31", comment="강제 전송 시도",
            target_status_category="done",
            mutation_id="ltm-transition:dddddddd-1111-1111-1111-111111111111",
        )

    assert provider.posts == 0


@pytest.mark.parametrize(
    ("status", "available", "expected"),
    [
        ({"id": "5", "name": "Resolved"}, False, "found"),
        ({"id": "1", "name": "Open"}, True, "absent"),
        ({"id": "5", "name": "Resolved"}, True, "unknown"),
        ({"id": "1", "name": "Open"}, False, "unknown"),
    ],
)
def test_transition_reconciliation_requires_status_and_availability_proof(
        status, available, expected):
    provider = _TransitionProvider()
    provider.status = status
    provider.available = available

    checked = reconcile_transition(provider, "DL-30", "31", "5", "Resolved")

    assert checked.state == expected

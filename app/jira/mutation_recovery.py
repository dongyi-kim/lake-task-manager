"""Idempotent recovery for Jira creates whose HTTP response may be lost.

Atlassian Server APIs used by LTM do not expose a general idempotency-key header.  A transport
timeout therefore has three states: definitely rejected, definitely committed, or committed but
the response never reached LTM.  This module stores a small durable receipt and serializes equal
client mutation ids.  On a retry it asks a caller-provided reconciler to prove FOUND/ABSENT before
ever issuing the write again.

The receipt contains a SHA-256 payload fingerprint, minimal Jira actor aliases, optional workflow
authority, and the eventual Jira response. The submitted logical payload itself is never stored.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from app.auth.base import (MutationOutcomeUnknown, SessionExpired,
                           UpstreamUnavailable, write_upstream)


_MUTATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
FOUND = "found"
ABSENT = "absent"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Reconciliation:
    state: str
    result: object = None


class MutationReceiptStore:
    """Cache-backed mutation receipt coordinator.

    Locks are process-local; the desktop app is single-instance.  Durable receipts cover browser
    retries and application restarts, while the lock also covers a retry arriving before the
    original timed-out server handler has finished.
    """

    # New-comment drafts live for seven days. The duplicate-prevention receipt must outlive the
    # browser draft or an old recovered draft could legally re-POST after its receipt expired.
    TTL_SECONDS = 8 * 24 * 3600
    # Jira search/comment indexes may lag a committed write.  A complete-but-empty read directly
    # after response loss is therefore not yet proof of absence.  During this short window we ask
    # the UI to preserve input and retry reconciliation, never the mutation itself.
    ABSENT_RETRY_GRACE_SECONDS = 30

    def __init__(self, cache, environment):
        self.cache = cache
        self.environment = str(environment or "")
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        # A timed-out SSO call can keep running in Playwright's owner thread after its FastAPI
        # handler has returned. Durable receipts survive process restarts; this process-local map
        # covers the different hazard where the *old* worker is still able to commit. Never
        # authorize a replacement POST while one of these handles is unfinished.
        self._inflight: dict[str, dict] = {}

    @staticmethod
    def fingerprint(value) -> str:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _key(self, kind, mutation_id):
        mid = str(mutation_id or "").strip()
        if not mid:
            return ""
        if not _MUTATION_ID.fullmatch(mid):
            raise ValueError("올바르지 않은 clientMutationId 입니다.")
        return f"v1:{self.environment}:{str(kind or '').strip()}:{mid}"

    def _lock(self, key):
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def _inflight_record(self, key):
        with self._guard:
            return self._inflight.get(key)

    def _remember_inflight(self, key, operation, fingerprint, attempted_at, context):
        if operation is None or not hasattr(operation, "result_now"):
            return
        with self._guard:
            self._inflight[key] = {
                "operation": operation,
                "fingerprint": fingerprint,
                "attemptedAt": attempted_at,
                "context": context,
            }

    def _forget_inflight(self, key, operation):
        with self._guard:
            if (self._inflight.get(key) or {}).get("operation") is operation:
                self._inflight.pop(key, None)

    def receipt(self, kind, mutation_id):
        """Return a durable receipt for callers that need its recovery context before execute."""
        key = self._key(kind, mutation_id)
        return self.cache.mutation_receipt(key) if key else None

    def execute(self, *, kind, mutation_id, payload, write, reconcile, after_success,
                context=None, recover_inflight=None, before_retry=None):
        """Run or safely recover one create operation.

        ``reconcile(receipt)`` must return FOUND only for a proven matching Jira object, ABSENT
        only after a complete authoritative read, and UNKNOWN otherwise.  Known authentication
        rejection clears a newly-pending receipt; transport failure deliberately leaves it for
        reconciliation on the next retry.
        """
        key = self._key(kind, mutation_id)
        if not key:                         # legacy callers remain compatible
            result = write()
            try:
                after_success(result)
            except Exception:
                pass                        # a committed write must not look failed due to cache work
            return result

        fingerprint = self.fingerprint(payload)
        with self._lock(key):
            def finish_cleanup(result, attempted_at):
                # Keep a durable committed state until all local invalidations succeed.  A retry
                # never repeats the Jira write; it only resumes cleanup, then marks the receipt as
                # fully successful.  Ordinary success replays therefore do not keep bumping JQL
                # generations forever.
                try:
                    after_success(result)
                except Exception as exc:
                    raise MutationOutcomeUnknown(
                        mutation_id,
                        "Jira 반영은 확인했지만 화면 캐시 갱신을 마치지 못했습니다. 같은 요청으로 다시 시도해 주세요.",
                    ) from exc
                self.cache.set_mutation_receipt(
                    key, fingerprint, "success", result, attempted_at, self.TTL_SECONDS,
                    context=context)
                return result

            inflight = self._inflight_record(key)
            if inflight and inflight.get("fingerprint") != fingerprint:
                raise ValueError("같은 clientMutationId가 다른 요청에 재사용되었습니다.")
            if (inflight and context is not None and inflight.get("context") is not None
                    and inflight.get("context") != context):
                raise ValueError("실행 중인 Jira 요청 복구 정보와 현재 요청이 일치하지 않습니다.")

            receipt = self.cache.mutation_receipt(key)
            if receipt and receipt.get("fingerprint") != fingerprint:
                raise ValueError("같은 clientMutationId가 다른 요청에 재사용되었습니다.")
            if (receipt and context is not None and receipt.get("context") is not None
                    and receipt.get("context") != context):
                raise ValueError("저장된 Jira 요청 복구 정보와 현재 요청이 일치하지 않습니다.")
            if receipt and receipt.get("state") == "success":
                return receipt.get("result")
            if receipt and receipt.get("state") == "committed":
                return finish_cleanup(
                    receipt.get("result"), receipt.get("attemptedAt") or time.time())

            if inflight:
                operation = inflight.get("operation")
                if not bool(getattr(operation, "done", False)):
                    raise MutationOutcomeUnknown(
                        mutation_id,
                        "이전 Jira 요청이 아직 처리 중입니다. 입력은 보존되며 완료 여부를 다시 확인합니다.",
                        upstream_failed=True,
                    )
                try:
                    raw_result = operation.result_now()
                    result = (recover_inflight or (lambda value: value))(raw_result)
                except BaseException:  # noqa: BLE001 - owner-thread failure only permits reconcile
                    # The old worker is conclusively finished, but its exception does not prove
                    # whether Jira committed before the response failed. Fall through to the
                    # authoritative reconciler and retain the durable pending receipt.
                    self._forget_inflight(key, operation)
                else:
                    attempted_at = inflight.get("attemptedAt") or time.time()
                    # Persist proof before dropping the only in-memory reference to the result.
                    # If SQLite fails, retain the handle so the next retry can recover it again.
                    self.cache.set_mutation_receipt(
                        key, fingerprint, "committed", result, attempted_at,
                        self.TTL_SECONDS, context=context)
                    self._forget_inflight(key, operation)
                    return finish_cleanup(result, attempted_at)

            if receipt and receipt.get("state") == "pending":
                # This read is the continuation of a user write, not background enrichment. Put
                # it ahead of large Task/Workload reads so an idle-auth recovery is responsive.
                try:
                    with write_upstream():
                        checked = reconcile(receipt)
                except SessionExpired as exc:
                    # The original write may already have committed.  An authentication failure
                    # while *checking* that fact says nothing about the write outcome, so keep the
                    # pending receipt and the same client id for the post-login retry.
                    if int(getattr(exc, "status", 0) or 0) >= 500:
                        raise MutationOutcomeUnknown(
                            mutation_id,
                            "Jira 반영 여부 확인 중 서버 응답이 끊겼습니다. 같은 요청으로 다시 확인합니다.",
                            upstream_failed=True,
                        ) from exc
                    raise
                except UpstreamUnavailable as exc:
                    # A second transport failure is still an unknown outcome, not a proven failed
                    # mutation.  Returning the explicit uncertain contract prevents the browser
                    # from replacing the id and blindly creating a duplicate on its next try.
                    raise MutationOutcomeUnknown(
                        mutation_id,
                        "Jira 반영 여부 확인도 지연되고 있습니다. 입력은 보존되며 재시도 시 중복 여부를 다시 확인합니다.",
                        upstream_failed=True,
                    ) from exc
                except Exception as exc:
                    # Reconciliation is deliberately fail-closed: parsing/provider surprises must
                    # never be treated as ABSENT because that would authorize a duplicate write.
                    raise MutationOutcomeUnknown(
                        mutation_id,
                        "Jira 반영 여부를 안전하게 확인하지 못했습니다. 입력은 보존되며 잠시 후 다시 시도해 주세요.",
                    ) from exc
                if not isinstance(checked, Reconciliation):
                    checked = Reconciliation(UNKNOWN)
                if checked.state == FOUND:
                    result = checked.result
                    self.cache.set_mutation_receipt(
                        key, fingerprint, "committed", result,
                        receipt.get("attemptedAt") or time.time(), self.TTL_SECONDS,
                        context=context)
                    return finish_cleanup(
                        result, receipt.get("attemptedAt") or time.time())
                if checked.state == ABSENT and time.time() - float(
                        receipt.get("attemptedAt") or 0) < self.ABSENT_RETRY_GRACE_SECONDS:
                    raise MutationOutcomeUnknown(
                        mutation_id,
                        "Jira 검색 반영을 기다리는 중입니다. 입력은 보존되며 잠시 후 다시 시도해 주세요.",
                    )
                if checked.state != ABSENT:
                    raise MutationOutcomeUnknown(
                        mutation_id,
                        "Jira 반영 여부를 아직 확인 중입니다. 입력은 보존되며 잠시 후 다시 시도해 주세요.",
                    )
                # The receipt freezes the original authenticated writer. A recovered Jira session
                # may belong to a different account; only re-authorize a proven-absent POST after
                # the caller confirms that the current actor still matches that receipt.
                if before_retry is not None:
                    try:
                        before_retry()
                    except SessionExpired as exc:
                        if int(getattr(exc, "status", 0) or 0) >= 500:
                            raise MutationOutcomeUnknown(
                                mutation_id,
                                "Jira 사용자 확인 중 서버 응답이 끊겼습니다. 같은 요청으로 다시 확인합니다.",
                                upstream_failed=True,
                            ) from exc
                        raise
                    except UpstreamUnavailable as exc:
                        raise MutationOutcomeUnknown(
                            mutation_id,
                            "Jira 사용자 확인이 지연되고 있습니다. 입력은 보존되며 같은 요청으로 다시 확인합니다.",
                            upstream_failed=True,
                        ) from exc
                    except ValueError:
                        raise  # authenticated actor mismatch is an actionable, definitive refusal
                    except Exception as exc:
                        raise MutationOutcomeUnknown(
                            mutation_id,
                            "Jira 사용자 정보를 안전하게 확인하지 못했습니다. 같은 요청으로 다시 확인합니다.",
                        ) from exc

            attempted_at = time.time()
            self.cache.set_mutation_receipt(
                key, fingerprint, "pending", None, attempted_at, self.TTL_SECONDS,
                context=context)
            try:
                result = write()
            except SessionExpired as exc:
                if int(getattr(exc, "status", 0) or 0) >= 500:
                    # Basic/local providers historically wrap server 5xx in UpstreamError, whose
                    # base class is SessionExpired. A 5xx still has uncertain commit semantics.
                    raise MutationOutcomeUnknown(
                        mutation_id,
                        "Jira가 요청을 처리했는지 확인할 수 없습니다. 재시도 시 중복 여부를 먼저 확인합니다.",
                        upstream_failed=True,
                    ) from exc
                # Jira가 명시적으로 인증을 거절했다. 요청은 처리되지 않았으므로 로그인 뒤 같은
                # mutation id가 정상적으로 새 쓰기를 할 수 있어야 한다.
                self.cache.delete_mutation_receipt(key)
                raise
            except UpstreamUnavailable as exc:
                # 5xx/timeout은 응답만 잃었을 수 있다. receipt를 남겨 다음 시도에서 먼저 확인한다.
                self._remember_inflight(
                    key, getattr(exc, "pending_operation", None), fingerprint,
                    attempted_at, context)
                raise MutationOutcomeUnknown(
                    mutation_id,
                    "Jira가 요청을 받았는지 확인할 수 없습니다. 입력은 보존되며 재시도 시 중복 여부를 먼저 확인합니다.",
                    upstream_failed=True,
                ) from exc
            except (TimeoutError, ConnectionError) as exc:
                # Provider boundaries should normalize these, but a browser/socket can still
                # surface a raw transport exception. It has the same uncertain-commit semantics.
                raise MutationOutcomeUnknown(
                    mutation_id,
                    "Jira 응답 연결이 끊겼습니다. 재시도 시 중복 여부를 먼저 확인합니다.",
                    upstream_failed=True,
                ) from exc
            except Exception:
                # validation/known 4xx 등은 확정 실패. 수정 뒤 동일 UI 요청을 다시 제출할 수 있다.
                self.cache.delete_mutation_receipt(key)
                raise

            # Jira 응답을 받았다면 먼저 committed receipt를 확정한다. 뒤의 캐시 무효화가 실패해도
            # 다음 시도는 쓰기를 반복하지 않고 cleanup만 재개한다.
            self.cache.set_mutation_receipt(
                key, fingerprint, "committed", result, attempted_at, self.TTL_SECONDS,
                context=context)
            return finish_cleanup(result, attempted_at)


def _jira_time(value):
    """Parse Jira's ISO timestamps (``+0900``, ``Z`` and ordinary ISO forms)."""
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    if re.search(r"[+-]\d{4}$", raw):
        raw = raw[:-2] + ":" + raw[-2:]
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _same_text(left, right):
    return str(left or "").replace("\r\n", "\n").strip() == \
        str(right or "").replace("\r\n", "\n").strip()


def _actor_aliases(value):
    """Return non-display Jira identity aliases from a receipt or response user object."""
    if not isinstance(value, dict):
        return set()
    aliases = {
        str(item or "").strip().casefold()
        for item in (value.get("aliases") or value.get("actorAliases") or [])
        if str(item or "").strip()
    }
    aliases.update(
        str(value.get(field) or "").strip().casefold()
        for field in ("id", "name", "key", "accountId")
        if str(value.get(field) or "").strip()
    )
    return aliases


def _same_actor(actual, expected):
    """Return True/False when identifiable, or None when either side lacks identity proof."""
    wanted = _actor_aliases(expected)
    observed = _actor_aliases(actual)
    if not wanted or not observed:
        return None
    return bool(wanted & observed)


def _window_is_complete(data, rows, *, cutoff, date_field):
    """Whether an ordered first page proves no matching row exists in the retry window."""
    if not isinstance(data, dict) or not isinstance(rows, list):
        return False
    start = data.get("startAt", 0)
    total = data.get("total")
    if type(start) is not int or start != 0 or type(total) is not int or total < 0:
        return False
    if total <= len(rows):
        return True
    # There are more old rows, but descending order plus an oldest fetched timestamp before the
    # relevant window proves none of the omitted rows can be our mutation.
    if rows:
        stamps = [_jira_time(date_field(row)) for row in rows]
        # The cutoff shortcut is sound only when Jira really honored descending order. Missing or
        # ascending timestamps mean an omitted newer row could still be our committed mutation.
        if any(stamp is None for stamp in stamps):
            return False
        if any(left < right for left, right in zip(stamps, stamps[1:])):
            return False
        return stamps[-1] < cutoff
    return False


def reconcile_comment(provider, issue_key, body, receipt, expected_actor=None, *, cap=100):
    """Reconcile a possibly-created comment through a fresh authoritative Jira page."""
    if not _actor_aliases(expected_actor):
        return Reconciliation(UNKNOWN)
    attempted = float((receipt or {}).get("attemptedAt") or 0)
    cutoff = attempted - 5 * 60
    data = provider.get_json(
        f"/rest/api/2/issue/{issue_key}/comment",
        params={"startAt": 0, "maxResults": int(cap), "orderBy": "-created"},
    ) or {}
    rows = data.get("comments") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return Reconciliation(UNKNOWN)
    uncertain_match = False
    confirmed = []
    for row in rows:
        if not isinstance(row, dict) or not _same_text(row.get("body"), body):
            continue
        created = _jira_time(row.get("created"))
        if created is not None and created < cutoff:
            continue
        actor_match = _same_actor(row.get("author"), expected_actor)
        if actor_match is False:
            continue
        # A same-actor/same-body row created before this attempt may be an ordinary earlier
        # comment, not the response-lost POST. Keep the five-minute window only as clock-skew
        # ambiguity: it can force UNKNOWN but can never prove FOUND.
        if (created is None or created < attempted or actor_match is None
                or not row.get("id")):
            uncertain_match = True
        elif actor_match:
            confirmed.append(row)
    # More than one post-attempt candidate is also not attributable to this mutation id (another
    # tab can submit identical content as the same user). Never choose the first arbitrarily.
    if uncertain_match or len(confirmed) > 1:
        return Reconciliation(UNKNOWN)
    if len(confirmed) == 1:
        return Reconciliation(FOUND, confirmed[0])
    complete = _window_is_complete(
        data, rows, cutoff=cutoff, date_field=lambda row: (row or {}).get("created"))
    return Reconciliation(ABSENT if complete else UNKNOWN)


def reconcile_attachment(provider, issue_key, filename, size, receipt, expected_actor=None):
    """Reconcile an attachment POST through the issue's authoritative attachment field."""
    if not _actor_aliases(expected_actor):
        return Reconciliation(UNKNOWN)
    attempted = float((receipt or {}).get("attemptedAt") or 0)
    cutoff = attempted - 5 * 60
    data = provider.get_json(
        f"/rest/api/2/issue/{issue_key}", params={"fields": "attachment"}) or {}
    fields = data.get("fields") if isinstance(data, dict) else None
    rows = fields.get("attachment") if isinstance(fields, dict) else None
    if not isinstance(rows, list):
        return Reconciliation(UNKNOWN)
    uncertain_match = False
    confirmed = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("filename") or "") != str(filename or ""):
            continue
        actual_size = row.get("size")
        if actual_size is not None and int(actual_size) != int(size or 0):
            continue
        created = _jira_time(row.get("created"))
        if created is not None and created < cutoff:
            continue
        actor_match = _same_actor(row.get("author"), expected_actor)
        if actor_match is False:
            continue
        if (created is None or created < attempted or actor_match is None
                or not row.get("id")):
            uncertain_match = True
        elif actor_match:
            confirmed.append(row)
    if uncertain_match or len(confirmed) > 1:
        return Reconciliation(UNKNOWN)
    if len(confirmed) == 1:
        return Reconciliation(FOUND, [confirmed[0]])
    # Jira's issue attachment field is not paginated. A structurally valid full list can prove
    # absence, subject to the coordinator's index-consistency grace period.
    return Reconciliation(ABSENT)


def reconcile_transition(provider, issue_key, transition_id, target_status_id="",
                         target_status_name="", receipt=None, expected_comment="",
                         expected_actor=None):
    """Reconcile a response-lost workflow transition without executing it twice.

    A status match by itself is not enough: global/self transitions can remain executable in the
    destination state, and a required completion note must also be visible in the comment feed.
    We only declare success when the exact target status is current, the submitted transition id
    is no longer available, and any expected comment is proven. Conversely, retry is safe only
    when the target is not current and the same transition is still available. Every
    malformed/ambiguous combination fails closed.
    """
    issue = provider.get_json(
        f"/rest/api/2/issue/{issue_key}", params={"fields": "status"}) or {}
    fields = issue.get("fields") if isinstance(issue, dict) else None
    status = fields.get("status") if isinstance(fields, dict) else None
    if not isinstance(status, dict):
        return Reconciliation(UNKNOWN)

    wanted_id = str(target_status_id or "").strip()
    wanted_name = str(target_status_name or "").strip().casefold()
    if wanted_id:
        at_target = str(status.get("id") or "").strip() == wanted_id
    elif wanted_name:
        at_target = str(status.get("name") or "").strip().casefold() == wanted_name
    else:
        # Without an exact destination, a changed status cannot be attributed to this request.
        return Reconciliation(UNKNOWN)

    data = provider.get_json(
        f"/rest/api/2/issue/{issue_key}/transitions",
        params={"expand": "transitions.fields"},
    ) or {}
    rows = data.get("transitions") if isinstance(data, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return Reconciliation(UNKNOWN)
    tid = str(transition_id or "").strip()
    if not tid:
        return Reconciliation(UNKNOWN)
    still_available = any(str(row.get("id") or "").strip() == tid for row in rows)

    if at_target and not still_available:
        if expected_comment:
            comment_check = reconcile_comment(
                provider, issue_key, expected_comment, receipt or {}, expected_actor)
            # A status-changing transition must never be repeated at this point. But a required
            # completion note is part of LTM's logical mutation, so don't call it successful until
            # the fresh Jira comment feed proves that note exists as well.
            if comment_check.state != FOUND:
                return Reconciliation(UNKNOWN)
        # The transition endpoint normally returns 204, so synthesize the same harmless result
        # shape used by the route.  The caller only needs proof that the write was committed.
        return Reconciliation(FOUND, {"ok": True})
    if not at_target and still_available:
        return Reconciliation(ABSENT)
    return Reconciliation(UNKNOWN)


def _name(value):
    if isinstance(value, dict):
        return str(value.get("name") or value.get("key") or value.get("accountId") or "")
    return str(value or "")


def _names(values):
    return sorted(_name(value).casefold() for value in (values or []) if _name(value))


def _issue_matches(issue, expected_fields, epic_field):
    fields = (issue or {}).get("fields") or {}
    if str(fields.get("summary") or "") != str(expected_fields.get("summary") or ""):
        return False
    if _name(fields.get("issuetype")).casefold() != _name(expected_fields.get("issuetype")).casefold():
        return False
    if _name(fields.get("parent")).upper() != _name(expected_fields.get("parent")).upper():
        return False
    if epic_field and str(fields.get(epic_field) or "").upper() != \
            str(expected_fields.get(epic_field) or "").upper():
        return False
    # Jira may apply project defaults to fields omitted by LTM (notably priority/assignee).
    # Compare optional values only when the user actually sent them; otherwise a successfully
    # created issue would look absent and an idempotent retry could create a duplicate.
    scalar_names = ("priority", "assignee")
    if any(name in expected_fields
           and _name(fields.get(name)).casefold() != _name(expected_fields.get(name)).casefold()
           for name in scalar_names):
        return False
    if "duedate" in expected_fields and str(fields.get("duedate") or "") != \
            str(expected_fields.get("duedate") or ""):
        return False
    if "components" in expected_fields and \
            _names(fields.get("components")) != _names(expected_fields.get("components")):
        return False
    if "labels" in expected_fields and \
            sorted(str(value).casefold() for value in (fields.get("labels") or [])) != \
            sorted(str(value).casefold() for value in (expected_fields.get("labels") or [])):
        return False
    return True


def reconcile_created_issue(provider, project_key, expected_fields, epic_field, receipt,
                            expected_actor=None, *, cap=100):
    """Find a ticket created by an earlier response-lost request without creating another one."""
    if not _actor_aliases(expected_actor):
        return Reconciliation(UNKNOWN)
    attempted = float((receipt or {}).get("attemptedAt") or 0)
    cutoff = attempted - 5 * 60
    project = str(project_key or "").replace("\\", "\\\\").replace('"', '\\"')
    field_names = {
        "summary", "issuetype", "parent", "priority", "assignee", "duedate",
        "components", "labels", "created", "creator",
    }
    if epic_field:
        field_names.add(str(epic_field))
    data = provider.get_json("/rest/api/2/search", params={
        "jql": f'project = "{project}" AND created >= -2d ORDER BY created DESC',
        "fields": ",".join(sorted(field_names)), "startAt": 0, "maxResults": int(cap),
    }) or {}
    rows = data.get("issues") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return Reconciliation(UNKNOWN)
    uncertain_match = False
    confirmed = []
    for issue in rows:
        if not isinstance(issue, dict) or not _issue_matches(issue, expected_fields, epic_field):
            continue
        created = _jira_time(((issue.get("fields") or {}).get("created")))
        if created is not None and created < cutoff:
            continue
        actor_match = _same_actor(
            ((issue.get("fields") or {}).get("creator")), expected_actor)
        if actor_match is False:
            continue
        if (created is None or created < attempted or actor_match is None
                or not issue.get("key")):
            uncertain_match = True
        elif actor_match:
            confirmed.append({
                "id": issue.get("id"), "key": issue.get("key"), "self": issue.get("self"),
            })
    if uncertain_match or len(confirmed) > 1:
        return Reconciliation(UNKNOWN)
    if len(confirmed) == 1:
        return Reconciliation(FOUND, confirmed[0])
    complete = _window_is_complete(
        data, rows, cutoff=cutoff,
        date_field=lambda issue: ((issue or {}).get("fields") or {}).get("created"),
    )
    return Reconciliation(ABSENT if complete else UNKNOWN)

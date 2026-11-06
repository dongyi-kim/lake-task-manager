"""Confluence commands sharing LTM transport, durable receipts and read caches.

There is no distributed transaction or draft lock in the DC REST API. Published
version preconditions protect published edits; a successful write never proves
that another user's unpublished Synchrony draft is absent.
"""

from __future__ import annotations

import hashlib
import html
import re
import threading
from contextvars import ContextVar
from urllib.parse import unquote, urlencode

from app.auth.base import (MutationOutcomeUnknown, MutationPreflightUnavailable, UpstreamError,
                           UpstreamUnavailable, write_upstream)
from app.jira import confluence_service as reads
from app.jira.mutation_recovery import FOUND, UNKNOWN, MutationReceiptStore, Reconciliation
from app.services.ticket_commands import CommandError


_STORE_LOCK = threading.Lock()
_WRITE_STARTED = ContextVar("confluence_write_started", default=False)
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


def identifier(value):
    value = str(value)
    if not re.fullmatch(r"[0-9]{1,30}", value):
        raise CommandError("Confluence에서 조회한 숫자 문서·댓글·첨부 ID가 필요합니다.")
    return value


def storage_value(value, format="storage"):
    if not isinstance(value, str):
        raise CommandError("본문은 문자열이어야 합니다.")
    if format == "text":
        return "<p>" + html.escape(value).replace("\n", "<br />") + "</p>"
    if format != "storage":
        raise CommandError("format은 storage 또는 text여야 합니다.")
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", value, re.I):
        raise CommandError("Confluence 본문에 DTD나 외부 entity를 넣을 수 없습니다.")
    # Do not round-trip through Jira's HTML sanitizer: it drops ac:/ri: macros.
    # Confluence validates storage XHTML on write; rejected markup remains a 4xx.
    return value


class ConfluenceCommands:
    def __init__(self, get_client, settings):
        self.get_client, self.settings = get_client, settings

    @property
    def client(self):
        return self.get_client()

    def check_space(self, space):
        allowed = {str(item).casefold() for item in self.settings.search_confluence_spaces or []}
        if not allowed:
            raise CommandError("search.confluence.spaces에 조회·작성 범위를 설정하세요.")
        if not space or str(space).casefold() not in allowed:
            raise CommandError("설정된 Confluence Space 범위 밖입니다.", 403)
        return str(space)

    def require_compatible(self, operation):
        if str(self.client.env).split("@", 1)[0] == "mock":
            return
        features = getattr(self.settings, "confluence_verified_features", ()) or ()
        if isinstance(features, str):
            features = [value.strip() for value in features.split(",")]
        if (getattr(self.settings, "confluence_verification_revision", "") != "9.2.4"
                or operation not in features):
            raise CommandError(
                "이 서버의 Confluence 9.2.4 공동편집 호환성 검증이 아직 완료되지 않았습니다.", 409,
                reason="confluence_compatibility_unverified")

    def request(self, method, path, body=None, params=None, *, filename=None, data=None, content_type=None):
        if not path.startswith("/rest/api/") or ".." in unquote(path).split("/"):
            raise CommandError("허용된 Confluence REST 경로가 아닙니다.")
        client = self.client
        base = str(getattr(self.settings, "confluence_base", "") or "").rstrip("/")
        if not base and str(client.env).split("@", 1)[0] != "mock":
            raise CommandError("Confluence 서버 주소가 설정되지 않았습니다.")
        target = base + path
        try:
            if method == "GET":
                return reads.raw_read(client, path, params or {})
            with write_upstream():
                _WRITE_STARTED.set(True)
                if method == "POST_FILE":
                    # SSO's multipart adapter deliberately ignores params. Put these
                    # fixed Confluence options in the URL so production honors them.
                    if params:
                        target += "?" + urlencode(params)
                    return client.provider.post_multipart(target, filename, data,
                        content_type=content_type)
                if method == "DELETE":
                    return client.provider.delete(target, params=params)
                if method not in ("POST", "PUT"):
                    raise CommandError("지원하지 않는 Confluence 명령입니다.")
                return getattr(client.provider, method.lower() + "_json")(target, json_body=body, params=params)
        except Exception as exc:
            exc.service = "confluence"
            raise

    def content(self, content_id, expected_version=None, status="current"):
        content_id = identifier(content_id)
        if status == "current":
            item = reads.head(self.client, content_id, force=True)
        elif status == "trashed":
            item = self.request("GET", f"/rest/api/content/{content_id}",
                                params={"status": status, "expand": "space,version,ancestors,container"})
        else:
            raise CommandError("지원하지 않는 문서 상태입니다.")
        if not isinstance(item, dict) or str(item.get("id")) != content_id:
            raise UpstreamUnavailable("Confluence 문서 응답이 불완전합니다.")
        self.check_space((item.get("space") or {}).get("key"))
        actual = (item.get("version") or {}).get("number")
        if type(actual) is not int or actual < 1:
            raise UpstreamUnavailable("Confluence 문서의 발행 버전을 확인하지 못했습니다.")
        if expected_version is not None:
            if type(expected_version) is not int or expected_version < 1:
                raise CommandError("expected_version은 조회한 양의 정수 버전이어야 합니다.")
            if expected_version != actual:
                raise CommandError(f"문서 버전이 변경됐습니다: 요청 v{expected_version}, 현재 v{actual}.", 409,
                    reason="version_conflict",
                    next_action={"tool": "read_confluence_document", "arguments": {"page": content_id, "format": "storage"}})
        if item.get("draftConflict") or item.get("draftState") == "conflict":
            raise CommandError("미발행 초안과의 충돌이 확인되어 변경을 중단했습니다.", 409,
                               reason="draft_conflict")
        return item

    def invalidate(self, affected_ids):
        reads.invalidate(self.client, list(dict.fromkeys(str(value) for value in affected_ids if value)))

    def _receipts(self):
        client = self.client
        scope = {"service": "confluence", "base": str(self.settings.confluence_base or "").rstrip("/")}
        with _STORE_LOCK:
            registry = getattr(client, "_confluence_receipt_stores", None)
            if registry is None:
                registry = client._confluence_receipt_stores = {}
            key = (client.env, scope["base"])
            if key not in registry:
                registry[key] = MutationReceiptStore(client.cache, client.env, scope=scope)
            return registry[key]

    def execute(self, *, kind, mutation_id, payload, write, reconcile=None, affected_ids=(), invalidate_result=True,
                recover_inflight=None, invalidate_permissions=False):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", mutation_id or ""):
            raise CommandError("8~128자의 고유 client_mutation_id가 필요합니다. 재시도에는 같은 ID를 사용하세요.")
        def guarded_write():
            token = _WRITE_STARTED.set(False)
            try:
                return write()
            except (UpstreamUnavailable, UpstreamError, TimeoutError, ConnectionError) as exc:
                if (not _WRITE_STARTED.get()
                        and (not isinstance(exc, UpstreamError) or exc.status >= 500)):
                    raise MutationPreflightUnavailable(
                        "변경에 필요한 Confluence 정보를 확인하지 못했습니다. 같은 요청으로 다시 시도하세요.") from exc
                raise
            finally:
                _WRITE_STARTED.reset(token)
        def require_reconciliation(_raw):
            # The owner-thread handle completes the HTTP operation, not this command's
            # normalization/postchecks. Never save raw REST JSON as a command receipt.
            raise ValueError("Completed transport requires command reconciliation")
        def after_success(result):
            if invalidate_result:
                self.invalidate([*affected_ids, (result or {}).get("id")])
            if invalidate_permissions:
                scope = reads.scope_key(self.client)
                self.client.cache.forget_prefixes([f"{kind}:{scope}:" for kind in
                                                   ("confhead", "confview", "confrelated")])
        try:
            return self._receipts().execute(kind="confluence." + kind, mutation_id=mutation_id,
                payload=payload, write=guarded_write, reconcile=reconcile or (lambda receipt: Reconciliation(UNKNOWN)),
                recover_inflight=recover_inflight or require_reconciliation,
                after_success=after_success)
        except Exception as exc:
            exc.service = "confluence"
            if isinstance(exc, MutationOutcomeUnknown):
                exc.args = (str(exc).replace("Jira", "Confluence"),)
            raise

    def _result(self, item, operation, *, parent_id=None, expected_id=None):
        if (not isinstance(item, dict) or not str(item.get("id") or "").isdigit()
                or expected_id is not None and str(item.get("id")) != str(expected_id)):
            raise UpstreamUnavailable("Confluence 변경 응답에 문서 ID가 없습니다. 같은 요청으로 결과를 확인하세요.")
        return {"id": str(item["id"]), "operation": operation, "status": "success",
                "version": (item.get("version") or {}).get("number"), "title": item.get("title") or "",
                "parentId": parent_id, "draftState": "unknown"}

    @staticmethod
    def _marker(mutation_id):
        return "LTM request " + mutation_id

    def _recover_content(self, content_id, version, marker, operation, parent_id=None):
        try:
            item = self.request("GET", f"/rest/api/content/{identifier(content_id)}",
                params={"version": version, "expand": "version,space"})
            if (str(item.get("id")) == str(content_id)
                    and (item.get("version") or {}).get("number") == version
                    and (item.get("version") or {}).get("message") == marker):
                self.check_space((item.get("space") or {}).get("key"))
                return Reconciliation(FOUND, self._result(item, operation, parent_id=parent_id))
        except UpstreamError as exc:
            if exc.status != 404:
                raise
        return Reconciliation(UNKNOWN)

    def create_page(self, *, space, title, body, mutation_id, parent_id=None, format="storage"):
        space = self.check_space(space)
        if not str(title).strip():
            raise CommandError("문서 제목이 필요합니다.")
        text, marker = storage_value(body, format), self._marker(mutation_id)
        parent_id = identifier(parent_id) if parent_id else None
        payload = {"type": "page", "status": "current", "title": title, "space": {"key": space},
                   "body": {"storage": {"value": text, "representation": "storage"}},
                   "version": {"number": 1, "message": marker}}
        if parent_id:
            payload["ancestors"] = [{"id": parent_id}]
        normalize = lambda raw: self._result(raw, "create_page", parent_id=parent_id)
        def write():
            if parent_id:
                parent = self.content(parent_id)
                if parent.get("type") != "page" or (parent.get("space") or {}).get("key") != space:
                    raise CommandError("같은 Space의 페이지를 부모로 지정하세요.")
            return normalize(self.request("POST", "/rest/api/content", payload))
        def reconcile(receipt):
            result = self.request("GET", "/rest/api/content", params={"spaceKey": space, "title": title,
                "type": "page", "status": "current", "expand": "space,version", "limit": 100})
            found = [item for item in result.get("results", [])
                     if item.get("type") == "page" and item.get("title") == title
                     and (item.get("space") or {}).get("key") == space
                     and (item.get("version") or {}).get("message") == marker]
            return Reconciliation(FOUND, self._result(found[0], "create_page", parent_id=parent_id)) if len(found) == 1 else Reconciliation(UNKNOWN)
        return self.execute(kind="create_page", mutation_id=mutation_id, payload=payload, write=write,
                            reconcile=reconcile, affected_ids=[parent_id], recover_inflight=normalize)

    def update_page(self, *, page_id, expected_version, mutation_id, body=None, title=None, format="storage"):
        page_id = identifier(page_id)
        if body is None and title is None:
            raise CommandError("변경할 제목 또는 본문이 필요합니다.")
        if title is not None and not title.strip():
            raise CommandError("문서 제목은 비울 수 없습니다.")
        text = storage_value(body, format) if body is not None else None
        marker = self._marker(mutation_id)
        logical = {"pageId": page_id, "expectedVersion": expected_version, "body": text, "title": title}
        normalize = lambda raw: self._result(raw, "update_page", expected_id=page_id)
        def write():
            self.require_compatible("update_page")
            current = self.content(page_id, expected_version)
            if current.get("type") != "page":
                raise CommandError("페이지 수정에는 page 타입 ID가 필요합니다.")
            payload = {"id": page_id, "type": "page", "status": "current",
                       "title": title if title is not None else current["title"],
                       "version": {"number": expected_version + 1, "message": marker}}
            if text is not None:
                payload["body"] = {"storage": {"value": text, "representation": "storage"}}
            return normalize(self.request("PUT", f"/rest/api/content/{page_id}", payload,
                {"status": "current", "conflictPolicy": "abort", "asyncReconciliation": "false"}))
        return self.execute(kind="update_page", mutation_id=mutation_id, payload=logical, write=write,
            reconcile=lambda receipt: self._recover_content(page_id, expected_version + 1, marker, "update_page"),
            affected_ids=[page_id], recover_inflight=normalize)

    def create_comment(self, *, page_id, body, mutation_id, format="storage"):
        page_id, text = identifier(page_id), storage_value(body, format)
        if not text.strip():
            raise CommandError("댓글 내용이 필요합니다.")
        marker = self._marker(mutation_id)
        payload = {"type": "comment", "container": {"id": page_id, "type": "page"},
                   "body": {"storage": {"value": text, "representation": "storage"}},
                   "version": {"number": 1, "message": marker}}
        normalize = lambda raw: self._result(raw, "create_comment", parent_id=page_id)
        def write():
            self.content(page_id)
            return normalize(self.request("POST", "/rest/api/content", payload))
        # No index or absence guess authorizes a repeat POST after response loss.
        def reconcile(receipt):
            for item in self._children(page_id, "comment", expand="version,container"):
                if (item.get("version") or {}).get("message") == marker:
                    return Reconciliation(FOUND, self._result(item, "create_comment", parent_id=page_id))
            return Reconciliation(UNKNOWN)
        return self.execute(kind="create_comment", mutation_id=mutation_id, payload=payload,
                            write=write, reconcile=reconcile, affected_ids=[page_id], recover_inflight=normalize)

    def _child(self, page_id, child_id, kind, expected_version=None):
        if expected_version is not None and (type(expected_version) is not int or expected_version < 1):
            raise CommandError("expected_version은 조회한 양의 정수 버전이어야 합니다.")
        self.content(page_id)
        item = self.request("GET", f"/rest/api/content/{identifier(child_id)}",
                            params={"expand": "container,version,space"})
        if (str(item.get("id")) != str(child_id) or item.get("type") != kind
                or str((item.get("container") or {}).get("id")) != str(page_id)):
            raise CommandError("이 페이지에 속한 댓글·첨부 ID가 아닙니다.", 404)
        if expected_version is not None and (item.get("version") or {}).get("number") != expected_version:
            raise CommandError("댓글·첨부 버전이 변경되었습니다. 다시 조회하세요.", 409, reason="version_conflict")
        return item

    def update_comment(self, *, page_id, comment_id, body, expected_version, mutation_id, format="storage"):
        page_id, comment_id = identifier(page_id), identifier(comment_id)
        text, marker = storage_value(body, format), self._marker(mutation_id)
        payload = {"id": comment_id, "type": "comment", "version": {"number": expected_version + 1, "message": marker},
                   "body": {"storage": {"value": text, "representation": "storage"}}}
        normalize = lambda raw: self._result(raw, "update_comment", parent_id=page_id, expected_id=comment_id)
        def write():
            self._child(page_id, comment_id, "comment", expected_version)
            return normalize(self.request("PUT", f"/rest/api/content/{comment_id}", payload,
                {"conflictPolicy": "abort", "status": "current"}))
        return self.execute(kind="update_comment", mutation_id=mutation_id,
            payload={"pageId": page_id, **payload}, write=write, affected_ids=[page_id, comment_id], recover_inflight=normalize,
            reconcile=lambda receipt: self._recover_content(comment_id, expected_version + 1, marker, "update_comment", page_id))

    def delete_child(self, *, page_id, child_id, kind, expected_version, mutation_id):
        page_id, child_id = identifier(page_id), identifier(child_id)
        if kind not in ("comment", "attachment"):
            raise CommandError("삭제할 댓글 또는 첨부 타입이 필요합니다.")
        def normalize(raw):
            if raw is not None and (type(raw) is not int or not 200 <= raw < 300):
                raise UpstreamUnavailable("Confluence 삭제 결과를 확인하지 못했습니다.")
            return {"id": child_id, "operation": "delete_" + kind, "status": "success", "parentId": page_id}
        def write():
            self._child(page_id, child_id, kind, expected_version)
            path = f"/rest/api/content/{child_id}" if kind == "comment" else f"/rest/api/content/{page_id}/child/attachment/{child_id}"
            return normalize(self.request("DELETE", path))
        return self.execute(kind="delete_" + kind, mutation_id=mutation_id,
            payload={"pageId": page_id, "childId": child_id, "expectedVersion": expected_version},
            write=write, affected_ids=[page_id, child_id], recover_inflight=normalize)

    def _children(self, page_id, kind, *, expand="version"):
        start = 0
        while True:
            page = self.request("GET", f"/rest/api/content/{identifier(page_id)}/child/{kind}",
                                params={"start": start, "limit": 100, "expand": expand})
            rows = page.get("results")
            if not isinstance(rows, list):
                raise UpstreamUnavailable("Confluence 하위 목록 응답이 불완전합니다.")
            yield from rows
            if not (page.get("_links") or {}).get("next") and len(rows) < 100:
                break
            if not rows:
                raise UpstreamUnavailable("Confluence 하위 목록 pagination이 완료되지 않았습니다.")
            start += len(rows)

    def upload_attachment(self, *, page_id, filename, data, mutation_id, content_type="application/octet-stream"):
        page_id = identifier(page_id)
        if not filename or filename in (".", "..") or any(char in filename for char in ("/", "\\", "\r", "\n", "\x00")):
            raise CommandError("경로가 아닌 파일 이름만 지정하세요.")
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_ATTACHMENT_BYTES:
            raise CommandError("첨부는 0바이트 초과, 10MiB 이하이어야 합니다.")
        payload = {"pageId": page_id, "filename": filename, "sha256": hashlib.sha256(data).hexdigest(),
                   "size": len(data), "mimeType": content_type}
        def normalize(response):
            rows = response.get("results") if isinstance(response, dict) else response
            if not isinstance(rows, list) or len(rows) != 1:
                raise UpstreamUnavailable("Confluence 첨부 결과가 불완전합니다.")
            result = self._result(rows[0], "upload_attachment", parent_id=page_id)
            result.update(filename=filename, mimeType=content_type, size=len(data))
            return result
        def write():
            self.content(page_id)
            if any(item.get("title") == filename for item in self._children(page_id, "attachment")):
                raise CommandError("같은 이름의 첨부가 이미 있습니다. 다른 파일 이름을 사용하세요.", 409,
                                   reason="attachment_exists")
            response = self.request("POST_FILE", f"/rest/api/content/{page_id}/child/attachment",
                                    filename=filename, data=data, content_type=content_type,
                                    params={"allowDuplicated": "false", "status": "current", "expand": "version,container"})
            return normalize(response)
        return self.execute(kind="upload_attachment", mutation_id=mutation_id, payload=payload,
                            write=write, affected_ids=[page_id], recover_inflight=normalize)

    def read_attachment(self, **kwargs):
        from .confluence_content_reads import read_attachment
        return read_attachment(self, **kwargs)

    def attachment_bytes(self, **kwargs):
        from .confluence_content_reads import attachment_bytes
        return attachment_bytes(self, **kwargs)

    def read_comment(self, **kwargs):
        from .confluence_content_reads import read_comment
        return read_comment(self, **kwargs)

    def update_labels(self, *, page_id, labels, mutation_id, remove=False):
        page_id = identifier(page_id)
        if not labels or any(not isinstance(label, str) or not label.strip() for label in labels):
            raise CommandError("비어 있지 않은 라벨 목록이 필요합니다.")
        labels = list(dict.fromkeys(labels))
        if remove and len(labels) != 1:
            raise CommandError("라벨 삭제는 요청당 한 개씩 수행하세요.")
        def normalize(raw):
            if isinstance(raw, dict) and (raw.get("errors") or raw.get("error")):
                raise UpstreamUnavailable("Confluence 라벨 변경 결과를 확인하지 못했습니다.")
            return {"id": page_id, "operation": "remove_label" if remove else "add_labels", "status": "success", "labels": labels}
        def write():
            self.content(page_id)
            path = f"/rest/api/content/{page_id}/label"
            if remove:
                raw = self.request("DELETE", path, params={"name": labels[0]})
            else:
                raw = self.request("POST", path, [{"prefix": "global", "name": value} for value in labels])
            return normalize(raw)
        return self.execute(kind="remove_label" if remove else "add_labels", mutation_id=mutation_id,
            payload={"pageId": page_id, "labels": labels}, write=write, affected_ids=[page_id], recover_inflight=normalize)

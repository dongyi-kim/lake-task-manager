"""Typed ticket commands. JiraClient remains the owner of transport, receipts and caches."""

from contextlib import nullcontext
from urllib.parse import urlsplit

from app.auth.base import write_upstream
from app.domain.ticket_actions import field_update_error, is_done
from app.jira.mutation_recovery import Reconciliation, UNKNOWN

try:
    from app.infra.cache_policy import read_scope
except ModuleNotFoundError as exc:
    if exc.name != "app.infra.cache_policy":
        raise
    read_scope = None


class CommandError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class TicketCommands:
    def __init__(self, get_client, settings, require_edit=lambda key: None):
        self.get_client, self.settings, self.require_edit = get_client, settings, require_edit

    @property
    def client(self):
        return self.get_client()

    def _once(self, kind, mutation_id, payload, action):
        """Replay confirmed writes; never repeat an ambiguous non-create write automatically.

        Creates/comment-add/transition/uploads retain their stronger Jira reconcilers. For other
        writes, an unknown outcome stays unknown until the user reads Jira and resolves it.
        """
        def strict_action():
            # Display stale data is useful for reading, never for authorizing writes.
            # Keep fresh cache hits; require upstream confirmation once they expire.
            with read_scope(strict=True) if read_scope else nullcontext():
                return action()
        if not mutation_id:
            return strict_action()
        client = self.client
        actor, _receipt = client._mutation_actor_for("command-" + kind, mutation_id)
        def write():
            with write_upstream():
                return strict_action()
        return client._mutation_receipts.execute(
            kind="command-" + kind, mutation_id=mutation_id,
            payload={**payload, "actor": actor}, write=write,
            reconcile=lambda receipt: Reconciliation(UNKNOWN), after_success=lambda result: None,
            context={"actor": actor} if actor else None,
            before_retry=(lambda: client.assert_mutation_actor_identity(actor)) if actor else None,
        )

    def create(self, body, parent=None):
        client = self.client
        issue_type, summary = (body.type or "").strip(), (body.summary or "").strip()
        if not summary:
            raise CommandError("제목을 입력하세요.")
        def validate():
            types = client.child_types(parent) if parent else client.task_types()
            if issue_type not in types:
                raise CommandError(f"{issue_type} 타입은 이 위치에 만들 수 없습니다.")
        result = client.create_child(
            parent, issue_type, summary, priority=body.priority or None,
            duedate=body.duedate or None, assignee=body.assignee or None,
            components=body.components or None, labels=body.labels or None,
            description=client.desc_field_value(body.descriptionHtml) or None,
            mutation_id=body.clientMutationId, parent_is_epic=body.parentIsEpic,
            before_write=validate,
        )
        key = (result or {}).get("key")
        response = {"ok": True, "key": key}
        if parent:
            try:
                response["cascade"] = client.cascade_suggestion(key, created=True) if key else None
            except Exception:
                response["cascade"] = None
        return response

    def create_epic(self, body):
        client = self.client
        summary = (body.summary or "").strip()
        if not summary:
            raise CommandError("제목을 입력하세요.")
        result = client.create_epic(
            summary, priority=body.priority or None, duedate=body.duedate or None,
            assignee=body.assignee or None, components=body.components or None,
            description=client.desc_field_value(body.descriptionHtml) or None,
            epic_name=(body.epicName or "").strip() or None, mutation_id=body.clientMutationId,
        )
        key = (result or {}).get("key")
        linked = client.set_epic_link(key, body.taskKeys) if key and body.taskKeys else None
        return {"ok": True, "key": key, "link": linked}

    def bulk_validate(self, mode, items):
        from app.domain.bulk import validate_bulk
        return validate_bulk(mode, items, self.client.bulk_lookup())

    def bulk_create(self, body, *, write_fence=None):
        from app.content.mdhtml import markdown_to_html
        check = self.bulk_validate(body.mode, body.items)
        if not check.get("ok"):
            return {"ok": False, "created": [], "failed": [], "errors": check.get("errors", []),
                    "warnings": check.get("warnings", [])}
        if write_fence:
            write_fence.assert_current()
        mutation_id = body.clientMutationId
        if mutation_id:
            # Bind the entire ordered batch before any writes. Per-item receipts below
            # can then resume failures without allowing the same ID to mean a new batch.
            self._once("bulk-create-manifest", mutation_id, {"mode": body.mode, "items": body.items},
                       lambda: {"ok": True})
        options = {"mutation_id": mutation_id} if mutation_id else {}
        if write_fence:
            options["write_fence"] = write_fence
        result = self.client.bulk_create(body.mode, check.get("items") or body.items,
            desc_to_field=lambda markdown: self.client.desc_field_value(markdown_to_html(markdown)), **options)
        return {**result, "warnings": check.get("warnings", [])}

    def set_vit_tracking(self, key, tracked, mutation_id):
        from app.services.ticket_models import FieldsBody
        def write():
            # Fetch current labels before changing membership, so an old display
            # copy cannot remove labels another Jira user has just added.
            current = self.client.ticket_view(key, fresh=True)
            if not current:
                raise CommandError("티켓을 찾을 수 없습니다.", 404)
            labels = list(current.get("labels") or [])
            labels = list(dict.fromkeys(labels + ["PMO_VIT"])) if tracked else [label for label in labels if label != "PMO_VIT"]
            result = self.update_fields(key, FieldsBody(labels=labels))
            return {**result, "key": key, "tracked": tracked, "labels": labels}
        return self._once("vit-tracking", mutation_id, {"key": key, "tracked": tracked}, write)

    def update_fields(self, key, body):
        client = self.client
        def write():
            requested = {name: getattr(body, name) for name in (
                "priority", "assignee", "reporter", "duedate", "labels", "components",
                "summary", "descriptionHtml", "epic") if getattr(body, name) is not None}
            # The display badge intentionally suppresses read errors. Authorizing a
            # write must propagate them and must not treat missing status as Open.
            current = client.get_issue_light(key, strict=True)
            if not (current or {}).get("fields", {}).get("status"):
                raise CommandError("티켓 상태를 확인하지 못해 수정할 수 없습니다.", 503)
            error = field_update_error(current, ["description" if name == "descriptionHtml" else name
                                                 for name in requested if name not in ("reporter", "epic")])
            if is_done(current):
                raise CommandError(error or "완료된 티켓의 속성은 바꿀 수 없습니다.", 409)
            meta, fields, denied = client.editmeta(key), {}, []
            for name, value in requested.items():
                field = self.settings.epic_link_field_id if name == "epic" else name
                if name in ("assignee", "reporter"):
                    value = {"name": value} if value else None
                elif name == "priority":
                    value = {"name": value}
                elif name == "components":
                    value = [{"name": item} for item in value]
                elif name == "descriptionHtml":
                    field, value = "description", client.desc_field_value(value)
                elif name in ("duedate", "epic"):
                    value = value or None
                if field not in meta:
                    denied.append(field)
                else:
                    fields[field] = value
            if denied:
                raise CommandError("편집 권한이 없는 항목입니다: " + ", ".join(denied), 403)
            return client.update_fields(key, fields) if fields else {"ok": True, "note": "변경 없음"}
        return self._once("fields", getattr(body, "clientMutationId", None),
                          {"key": key, "body": body.model_dump()}, write)

    def transition(self, key, body):
        client = self.client
        duration = " ".join(f"{int(value)}{unit}" for value, unit in (
            (body.days, "d"), (body.hours, "h"), (body.minutes, "m")) if value)
        # The Jira workflow validates transition-specific fields, including optional comment.
        client.do_transition(
            key, body.id, time_spent=duration or None, assignee=body.assignee or None,
            resolution=body.resolution or None,
            comment=client.comment_field_value(body.commentHtml) if body.commentHtml else None,
            target_status_id=body.targetStatusId or None,
            target_status_name=body.targetStatusName or None,
            target_status_category=body.targetStatusCategory or None,
            mutation_id=body.clientMutationId or None,
        )
        try:
            cascade = client.cascade_suggestion(key)
        except Exception:
            cascade = None
        return {"ok": True, "cascade": cascade}

    def comment(self, key, html="", *, comment_id=None, delete=False, mutation_id=None):
        client = self.client
        if delete:
            return self._once("comment-delete", mutation_id, {"key": key, "id": comment_id},
                              lambda: client.delete_comment(key, comment_id))
        value = client.comment_field_value(html or "")
        if not (value or "").strip():
            raise CommandError("빈 코멘트")
        if comment_id:
            return self._once("comment-edit", mutation_id, {"key": key, "id": comment_id, "body": value},
                              lambda: client.update_comment(key, comment_id, value))
        return client.add_comment(key, value, mutation_id=mutation_id)

    def delete_ticket(self, key, mutation_id=None):
        def write():
            self.require_edit(key)
            return self.client.delete_issue(key)
        return self._once("delete", mutation_id, {"key": key}, write)

    def attachment(self, key, filename, data, content_type=None, mutation_id=None):
        if not filename or "/" in filename or "\\" in filename or filename in (".", ".."):
            raise CommandError("첨부 파일 이름에는 경로를 넣을 수 없습니다.")
        result = self.client.upload_attachment(key, filename, data, content_type, mutation_id=mutation_id)
        attachment = result[0] if isinstance(result, list) and result else (result or {})
        identifier = str(attachment.get("id") or "")
        name = attachment.get("filename") or filename
        return {"id": identifier, "filename": name,
                "path": f"/secure/attachment/{identifier}/{name}" if identifier else name}

    def delete_attachment(self, key, attachment_id, mutation_id=None):
        def write():
            self.require_edit(key)
            fields = self.client.get_issue(key).get("fields") or {}
            if str(attachment_id) not in {str(row.get("id")) for row in fields.get("attachment", [])}:
                raise CommandError("이 티켓에 속한 첨부 ID가 아닙니다.", 404)
            return self.client.delete_attachment(attachment_id, key=key)
        return self._once("attachment-delete", mutation_id, {"key": key, "id": attachment_id}, write)

    def link(self, key, other, type_name="Relates", direction="outward", mutation_id=None):
        if direction not in ("inward", "outward"):
            raise CommandError("direction은 inward 또는 outward여야 합니다.")
        return self._once("link", mutation_id,
                          {"key": key, "other": other, "type": type_name, "direction": direction},
                          lambda: self.client.add_issue_link(key, other, type_name, direction))

    def delete_link(self, key, link_id, other=None, mutation_id=None):
        def write():
            fields = self.client.get_issue(key).get("fields") or {}
            link = next((row for row in fields.get("issuelinks", []) if str(row.get("id")) == str(link_id)), None)
            if link is None:
                raise CommandError("이 티켓에 속한 링크 ID가 아닙니다.", 404)
            endpoint = (link.get("outwardIssue") or link.get("inwardIssue") or {}).get("key")
            if not endpoint or other and endpoint != other:
                raise CommandError("링크의 대상 티켓이 일치하지 않습니다.")
            return self.client.delete_issue_link(link_id, key, endpoint)
        return self._once("link-delete", mutation_id, {"key": key, "id": link_id, "other": other},
                          write)

    def document(self, key, url, title="", mutation_id=None):
        parsed = urlsplit(url)
        if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
            raise CommandError("문서 링크는 사용자 인증정보가 없는 HTTP(S) URL이어야 합니다.")
        return self._once("document", mutation_id, {"key": key, "url": url, "title": title},
                          lambda: self.client.add_remote_link(key, url, title))

    def delete_document(self, key, link_id, mutation_id=None):
        def write():
            self.require_edit(key)
            return self.client.delete_remote_link(key, link_id)
        return self._once("document-delete", mutation_id, {"key": key, "id": link_id}, write)

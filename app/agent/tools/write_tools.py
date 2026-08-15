"""agent/tools/write_tools.py — 검증(Auditor)과 실행(ActionExecutor).

두 종류를 한 파일에 두되 성격은 정반대다.

  · **검증 도구**는 부작용이 없다. 모델이 마음껏, 몇 번이고 부른다. 오히려 자주 불러야 한다 —
    초안을 고칠 때마다 다시 걸어 보는 게 Self-Check 의 실체다.
  · **쓰기 도구**는 `approval_token` 없이는 아무것도 못 한다(`agent/approval.py`). 토큰은 그
    **내용에만** 유효해서, 승인 화면에 보인 것과 다른 걸 만들 수 없다.

검증을 LLM 에게 맡기지 않고 `domain/bulk.validate_bulk` 를 그대로 쓴다. 화면의 Bulk 생성이
쓰는 바로 그 함수다 — 규칙이 두 벌이 되면 반드시 갈라지고, 그때 더 관대한 쪽이 사고를 낸다.
"""

from __future__ import annotations

from langchain_core.tools import tool

from app.agent import approval
from app.agent.tools._ctx import client, compact, trim

# 쓰기 도구는 자기가 어느 대화에 속하는지 알아야 한다(토큰이 thread 에 묶인다).
# 도구 인자로 받으면 모델이 남의 thread 를 적을 수 있으므로 **서버가 심어 준다**.
_thread = {"id": ""}


def set_thread(thread_id: str):
    _thread["id"] = str(thread_id or "")


# ── 검증 (부작용 없음) ─────────────────────────────────────────────
@tool
def validate_ticket_plan(mode: str, items: list) -> dict:
    """Validate a proposed ticket batch against Jira rules without side effects.

    This must pass before presenting a draft to the user. `mode` is either `task` for top-level or
    Epic children, or `subtask` for children of an existing ticket; do not mix modes in one call.

    `items` has the shape
    `[{summary, type, epic|parent, priority, duedate, assignee, components, labels, description}]`.
    `summary` and `type` are required. In `task` mode, always provide `epic`: use the parent key or
    explicitly set `null` for intentional top-level work. In `subtask` mode, `parent` must be an
    existing ticket key. Story Point is not accepted here and is valid only for Story. Maximum 100
    items.

    Returns `{"ok": bool, "errors": [{index,field,message}], "warnings": [...]}`. Call
    `create_tickets` only when `errors` is empty.
    """
    from app.domain.bulk import validate_bulk
    try:
        r = validate_bulk(mode, items, client().bulk_lookup())
    except Exception as e:
        return {"ok": False, "errors": [{"index": None, "field": None, "message": str(e)[:300]}],
                "warnings": []}
    return r


@tool
def list_ticket_options(kind: str = "") -> dict:
    """List valid values for `components`, `priorities`, `labels`, and `taskTypes`.

    Call this before drafting instead of inventing field values. Leave `kind` empty for every
    category, or pass one of `components`, `priorities`, `labels`, or `taskTypes`.
    """
    c, out = client(), {}
    want = (kind or "").strip()
    for name, fn in (("components", lambda: [x.get("name") for x in (c.components() or [])]),
                     ("priorities", c.priorities),
                     ("labels", lambda: c.label_suggestions("")),
                     ("taskTypes", c.task_types)):
        if want and want != name:
            continue
        try:
            vals = fn() or []
            out[name] = [v.get("name") if isinstance(v, dict) else v for v in vals][:60]
        except Exception as e:
            out[name + "_error"] = str(e)[:150]
    return out


@tool
def list_child_types(parent_key: str) -> list:
    """List the ticket types valid under a specific parent; never infer them from another tier."""
    try:
        return list(client().child_types(parent_key) or [])
    except Exception as e:
        return [f"error: {str(e)[:150]}"]


# ── 쓰기 (승인 토큰 필수) ──────────────────────────────────────────
def _denied(why: str) -> dict:
    return {"ok": False, "needsApproval": True, "error": why}


@tool
def create_tickets(mode: str, items: list, approval_token: str, children: list = None) -> dict:
    """Create a validated ticket batch after explicit user approval.

    `approval_token` is issued by the approval card and must exactly match the displayed payload;
    it cannot be invented. Without a token, present the draft and request approval. Jira has no
    batch rollback, so report every item in `failed` instead of implying complete success.

    `children` contains Sub-Tasks created with their parents. Each child uses `parent_index` to
    identify its parent item. The tool creates parents first, then uses their real keys. One token
    covers the exact tree shown in the approval UI.

    Returns `{"ok", "created": [{index,key,summary}], "failed": [{index,summary,error}]}`.
    """
    from app.domain.bulk import validate_bulk
    c = client()
    # 승인 전에 한 번 더 검증한다. 모델이 validate_ticket_plan 을 건너뛸 수도 있고, Jira 는
    # 롤백이 없어서 **반쯤 만들어진 배치**가 가장 나쁘다 — 하나라도 어긋나면 시작을 안 한다.
    pre = validate_bulk(mode, items, c.bulk_lookup())
    if not pre.get("ok"):
        return {"ok": False, "created": [], "failed": [], "errors": pre.get("errors"),
                "error": "규칙에 맞지 않아 만들지 않았습니다. 고쳐서 다시 승인을 받으세요."}

    kids = [k for k in (children or []) if isinstance(k, dict) and k.get("summary")]
    payload = {"mode": mode, "items": items}
    if kids:
        payload["children"] = kids
    ok, why = approval.consume(approval_token, "create_tickets", payload)
    if not ok:
        return _denied(why)
    try:
        r = c.bulk_create(mode, items, desc_to_field=c.desc_field_value)
    except Exception as e:
        return {"ok": False, "created": [], "failed": [], "error": str(e)[:300]}
    if not kids:
        return r

    # ── 2단계: 만들어진 부모 키로 Sub-Task 를 붙인다 ──────────────────
    made = [x for x in (r.get("created") or []) if isinstance(x, dict) and x.get("key")]
    rows = []
    for ch in kids:
        i = ch.get("parent_index")
        if not isinstance(i, int) or not (0 <= i < len(made)):
            continue                      # 부모가 안 만들어졌으면 자식도 만들지 않는다
        row = {k: v for k, v in ch.items() if k != "parent_index"}
        row["type"] = "Sub-Task"
        row["parent"] = made[i]["key"]
        rows.append(row)
    if not rows:
        return r
    sub = validate_bulk("subtask", rows, c.bulk_lookup())
    if not sub.get("ok"):
        # 부모는 이미 만들어졌다(롤백 없음) — 자식 실패를 **그대로 보고**한다.
        r.setdefault("failed", []).append(
            {"summary": f"Sub-Task {len(rows)}건",
             "error": "규칙 위반으로 만들지 않았습니다: "
                      + "; ".join(str(e.get("message")) for e in (sub.get("errors") or [])[:3])})
        return r
    try:
        r2 = c.bulk_create("subtask", rows, desc_to_field=c.desc_field_value)
    except Exception as e:
        r.setdefault("failed", []).append({"summary": f"Sub-Task {len(rows)}건",
                                           "error": str(e)[:200]})
        return r
    r["created"] = (r.get("created") or []) + (r2.get("created") or [])
    r["failed"] = (r.get("failed") or []) + (r2.get("failed") or [])
    r["ok"] = bool(r.get("ok")) and bool(r2.get("ok"))
    return r


@tool
def update_ticket(key: str, approval_token: str, assignee: str = None, duedate: str = None,
                  priority: str = None, summary: str = None, labels: list = None,
                  components: list = None, description: str = None) -> dict:
    """Update editable fields on one existing ticket after explicit user approval.

    Only provided fields change. Use an empty string or array to clear a field; `assignee=""`
    unassigns, and `duedate` uses `YYYY-MM-DD`.

    Fields cannot be changed while `statusCategory=done`. If `list_transitions` exposes a real
    Reopened transition, reopen it under a separate approval and then obtain new approval for the
    field change. Comments are still allowed on completed tickets. Fields not editable in the Jira
    screen are rejected.
    """
    changes = compact({"assignee": assignee, "duedate": duedate, "priority": priority,
                       "summary": summary, "labels": labels, "components": components,
                       "description": description})
    # None 이 아닌데 빈 값인 것(=지우기)은 compact 가 떨궈 버리므로 되살린다.
    for k, v in (("assignee", assignee), ("duedate", duedate), ("summary", summary),
                 ("labels", labels), ("components", components), ("description", description)):
        if v is not None and k not in changes:
            changes[k] = v
    if not changes:
        return {"ok": False, "error": "바꿀 필드를 하나도 주지 않았습니다."}

    c = client()
    try:
        from app.domain.ticket_actions import field_update_error
        current = c.ticket_badge(key)
        if not current:
            return {"ok": False, "error": "티켓 상태를 확인할 수 없어 속성을 바꾸지 않았습니다."}
        state_error = field_update_error(current, changes)
        if state_error:
            return {"ok": False, "error": state_error}
    except Exception as e:
        return {"ok": False, "error": f"티켓 상태를 확인하지 못했습니다: {str(e)[:200]}"}

    ok, why = approval.consume(approval_token, "update_ticket", {"key": key, "changes": changes})
    if not ok:
        return _denied(why)

    try:
        meta = c.editmeta(key)                      # 화면에 없는 필드를 밀어 넣으면 Jira 가 400 을 낸다
    except Exception as e:
        return {"ok": False, "error": f"편집 가능 필드를 확인하지 못했습니다: {str(e)[:200]}"}

    fields, denied = {}, []

    def put(fid, value):
        if fid not in meta:
            denied.append(fid)
            return
        fields[fid] = value

    if assignee is not None:
        put("assignee", {"name": assignee} if assignee else None)
    if duedate is not None:
        put("duedate", duedate or None)
    if priority is not None:
        put("priority", {"name": priority})
    if summary is not None:
        put("summary", summary)
    if labels is not None:
        put("labels", list(labels))
    if components is not None:
        put("components", [{"name": x} for x in components])
    if description is not None:
        # description 은 HTML 로 받는다 — 환경별 저장 형식 변환(desc_field_value)은 본체가 한다.
        put("description", c.desc_field_value(description))

    if not fields:
        return {"ok": False, "error": f"이 티켓에서 편집할 수 없는 필드입니다: {', '.join(denied)}"}
    try:
        c.update_fields(key, fields)
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
    return compact({"ok": True, "key": key, "updated": sorted(fields),
                    "skipped": sorted(denied) or None})


@tool
def create_epic(summary: str, approval_token: str, epic_name: str = "",
                description: str = "", components: list = None, priority: str = "",
                duedate: str = "", assignee: str = "") -> dict:
    """Create one top-level Epic after explicit user approval.

    `summary` is the Epic title. `epic_name` is the short WBS/badge label and defaults to `summary`.
    `description` is HTML with background, objective, and completion criteria; references are
    merged automatically. Create Task and Sub-Task items with `create_tickets`. The Epic must exist
    before children can be attached, so child creation requires a subsequent approval.
    """
    from app.agent import approval
    payload = compact({"summary": summary, "epic_name": epic_name,
                       "description": description, "components": components,
                       "priority": priority, "duedate": duedate, "assignee": assignee})
    ok, why = approval.consume(approval_token, "create_epic", payload)
    if not ok:
        return _denied(why)
    c = client()
    try:
        r = c.create_epic(summary=summary, epic_name=epic_name or None,
                          description=c.desc_field_value(description) if description else None,
                          components=[x for x in (components or []) if x] or None,
                          priority=priority or None, duedate=duedate or None,
                          assignee=assignee or None)
        key = (r or {}).get("key")
        if not key:
            return {"ok": False, "error": "Epic 생성 응답에 키가 없습니다."}
        return {"ok": True, "created": [{"key": key, "summary": summary}], "failed": []}
    except Exception as e:
        return {"ok": False, "created": [], "failed": [], "error": str(e)[:300]}


@tool
def add_ticket_comment(key: str, body: str, approval_token: str) -> dict:
    """Add a comment to one ticket after explicit user approval.

    Comments may preserve decisions such as decomposition or assignee rationale for readers who did
    not see this conversation. Comments are allowed when `statusCategory=done`. Never add one unless
    the user requested it.
    """
    ok, why = approval.consume(approval_token, "add_ticket_comment", {"key": key, "body": body})
    if not ok:
        return _denied(why)
    try:
        client().add_comment(key, body)
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
    return {"ok": True, "key": key, "body": trim(body, 120)}


@tool
def link_tickets(key: str, other_key: str, relation: str, approval_token: str) -> dict:
    """Link two tickets after explicit user approval.

    `relation` must be an existing Jira link type such as `Relates`, `Blocks`, `Duplicate`, or
    `Cloners`; do not invent a type. Direction is outward from `key` to `other_key`. For “DL-1
    blocks DL-2,” pass `key="DL-1"` and `relation="Blocks"`.
    """
    ok, why = approval.consume(approval_token, "link_tickets",
                               {"key": key, "other": other_key, "relation": relation})
    if not ok:
        return _denied(why)
    try:
        client().add_issue_link(key, other_key, relation or "Relates")
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
    return {"ok": True, "key": key, "other": other_key, "relation": relation or "Relates"}


@tool
def attach_document(key: str, url: str, title: str, approval_token: str) -> dict:
    """Attach a related Confluence or web document to a ticket after explicit user approval.

    Use this to preserve design or requirement evidence with the ticket. Reattaching the same URL
    updates the existing link instead of creating duplicates.
    """
    ok, why = approval.consume(approval_token, "attach_document",
                               {"key": key, "url": url, "title": title})
    if not ok:
        return _denied(why)
    try:
        client().add_remote_link(key, url, title=title or "")
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
    return {"ok": True, "key": key, "url": url, "title": trim(title, 80)}


@tool
def list_transitions(key: str) -> list:
    """List transitions currently available for one ticket without side effects.

    Workflow names vary by project. Never invent a transition; pass an id returned here to
    `transition_ticket`.
    """
    try:
        # client.transitions 는 이미 정규화된 모양({"id","name","to"(문자열)})을 준다 —
        # 원시 Jira 모양((t["to"]["name"]))으로 다시 벗기면 문자열에 .get 을 불러 죽는다
        # (실측: 이 도구가 mock 에서 늘 error 만 돌려주고 있었다).
        return [compact({"id": t.get("id"), "name": t.get("name"), "to": t.get("to"),
                         "toCategory": t.get("toCategory")})
                for t in (client().transitions(key) or [])]
    except Exception as e:
        return [{"error": str(e)[:200]}]


@tool
def transition_ticket(key: str, transition_id: str, approval_token: str,
                      comment: str = None, assignee: str = None) -> dict:
    """Move a ticket through one workflow transition after explicit user approval.

    `transition_id` must come from `list_transitions`, including when reopening a completed ticket.
    A transition and a field update are separate approval/execution operations. Pass `comment` or
    `assignee` only if that transition screen supports the field; otherwise Jira rejects it.
    """
    payload = compact({"key": key, "transition": transition_id,
                       "comment": comment, "assignee": assignee})
    ok, why = approval.consume(approval_token, "transition_ticket", payload)
    if not ok:
        return _denied(why)
    try:
        client().do_transition(key, transition_id, comment=comment, assignee=assignee)
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
    return {"ok": True, "key": key, "transition": transition_id}


@tool
def update_tickets(items: list, approval_token: str) -> dict:
    """Update editable fields on multiple tickets after one explicit user approval.

    Use this instead of repeated `update_ticket` calls when the request targets several tickets.
    `items` has the shape
    `[{"key": "DL-123", "changes": {"duedate": "2026-09-01", "priority": "P2-Major"}}]`.
    Each ticket may receive different values. Only provided fields change; use an empty string or
    array to clear a field.

    If any item has `statusCategory=done`, the entire batch is rejected before starting. Reopen such
    tickets in a separate approved operation. Non-editable fields or tickets are also rejected.
    Returns `{"ok", "updated": [{index,key,fields}], "failed": [{index,summary,error}]}`.
    """
    from app.domain.bulk import validate_bulk_update
    c = client()
    rows = [{"key": str((it or {}).get("key") or "").strip(),
             "changes": dict((it or {}).get("changes") or {})}
            for it in (items or []) if isinstance(it, dict)]
    pre = validate_bulk_update(rows, c.bulk_lookup())
    if not pre.get("ok"):
        return {"ok": False, "updated": [], "failed": [], "errors": pre.get("errors"),
                "error": "규칙에 맞지 않아 하나도 바꾸지 않았습니다. 고쳐서 다시 승인을 받으세요."}
    ok, why = approval.consume(approval_token, "update_tickets", {"items": rows})
    if not ok:
        return _denied(why)
    try:
        from app.main import _fields_for_update
    except Exception:      # 라우트 없이 도구만 쓰는 환경(테스트) — 최소 변환으로 대신한다
        def _fields_for_update(key, changes):
            out = {}
            for name, value in (changes or {}).items():
                if name == "priority":
                    out["priority"] = {"name": value}
                elif name == "assignee":
                    out["assignee"] = {"name": value} if value else None
                elif name == "components":
                    out["components"] = [{"name": x} for x in (value or [])]
                elif name == "description":
                    out["description"] = client().desc_field_value(value)
                else:
                    out[name] = value
            return out
    try:
        return c.bulk_update(rows, _fields_for_update)
    except Exception as e:
        return {"ok": False, "updated": [], "failed": [], "error": str(e)[:300]}


@tool
def add_ticket_comments(items: list, approval_token: str) -> dict:
    """Add comments to multiple tickets after one explicit user approval.

    Use this for meeting outcomes, batch notices, or one decision recorded across several tickets.
    `items` has the shape `[{"key": "DL-123", "body": "..."}]`, and each body may differ. Mention
    a person as `[~user_id]`, for example `[~skcc.x1042]`; a plain ticket key such as `DL-123`
    becomes a link.

    Comments are allowed on completed tickets. Never add comments unless the user requested them,
    because they may notify multiple people. Returns
    `{"ok", "created": [{index,key}], "failed": [{index,summary,error}]}`.
    """
    from app.domain.bulk import validate_bulk_comment
    c = client()
    rows = [{"key": str((it or {}).get("key") or "").strip(),
             "body": str((it or {}).get("body") or "")}
            for it in (items or []) if isinstance(it, dict)]
    pre = validate_bulk_comment(rows, c.bulk_lookup())
    if not pre.get("ok"):
        return {"ok": False, "created": [], "failed": [], "errors": pre.get("errors"),
                "error": "규칙에 맞지 않아 하나도 남기지 않았습니다."}
    ok, why = approval.consume(approval_token, "add_ticket_comments", {"items": rows})
    if not ok:
        return _denied(why)
    try:
        return c.bulk_comment(rows, to_body=c.desc_field_value)
    except Exception as e:
        return {"ok": False, "created": [], "failed": [], "error": str(e)[:300]}

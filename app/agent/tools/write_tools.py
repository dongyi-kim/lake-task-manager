"""agent/tools/write_tools.py — 검증(Reviewer)과 실행(Operator).

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
    """만들려는 티켓 목록이 **Jira 규칙에 맞는지** 미리 검사한다. 부작용 없음 — 자주 불러라.

    사용자에게 초안을 보여 주기 **전에** 반드시 통과시킨다. 여기서 걸리는 걸 그대로 보여 주면
    사용자가 대신 디버깅하게 된다.

    mode: "task"(최상위/Epic 밑) 또는 "subtask"(기존 티켓의 Sub-Task). **한 번에 하나만** —
          Sub-Task 는 부모가 이미 있어야 하므로 Task 를 먼저 만들고 두 번째 호출로 붙인다.
    items: [{summary, type, epic|parent, priority, duedate, assignee, components, labels, description}]
           - summary·type 은 필수.
           - task 모드: **epic 을 반드시 적는다.** 상위 Epic 밑에 넣으면 그 키를, Epic 없이
             최상위로 만들 거면 `"epic": null` 을 **명시**한다. 생략하면 거부된다 — 빠뜨려서
             고아 티켓이 생기는 것과 일부러 최상위로 두는 것을 구분하기 위해서다.
           - subtask 모드: parent 가 **이미 존재하는** 티켓 키여야 한다.
           - Story Point 는 여기서 못 넣는다. **Story 타입에만** 설정 가능해서 생성 후 따로 건다.
           - 최대 100건.

    돌려주는 것: {"ok": bool, "errors": [{index,field,message}], "warnings": [...]}
    errors 가 비어야 create_tickets 로 넘어갈 수 있다.
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
    """티켓 필드에 **넣을 수 있는 값들** — components / priorities / labels / taskTypes.

    초안을 짜기 전에 본다. 없는 컴포넌트나 우선순위를 지어내면 validate_ticket_plan 에서 막힌다.
    kind 를 비우면 전부, 주면 그것만("components" | "priorities" | "labels" | "taskTypes").
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
    """그 부모 **밑에 만들 수 있는 티켓 타입**. Epic 밑과 Story 밑이 다르다 — 지어내지 말고 확인한다."""
    try:
        return list(client().child_types(parent_key) or [])
    except Exception as e:
        return [f"error: {str(e)[:150]}"]


# ── 쓰기 (승인 토큰 필수) ──────────────────────────────────────────
def _denied(why: str) -> dict:
    return {"ok": False, "needsApproval": True, "error": why}


@tool
def create_tickets(mode: str, items: list, approval_token: str) -> dict:
    """검증을 통과한 티켓들을 **실제로 만든다**. 사용자 승인 토큰이 반드시 필요하다.

    토큰은 승인 카드가 발급한다 — **지어낼 수 없고**, 승인 화면에 보인 items 와 한 글자라도
    다르면 거부된다. 토큰이 없으면 만들지 말고 사용자에게 초안을 보여 주고 승인을 요청하라.

    Jira 에는 롤백이 없다 — 하나가 실패해도 나머지는 계속 만들어진다. 결과의 failed 를
    사용자에게 그대로 알려라(조용히 넘어가면 사용자는 다 만들어진 줄 안다).

    돌려주는 것: {"ok", "created": [{index,key,summary}], "failed": [{index,summary,error}]}
    """
    from app.domain.bulk import validate_bulk
    c = client()
    # 승인 전에 한 번 더 검증한다. 모델이 validate_ticket_plan 을 건너뛸 수도 있고, Jira 는
    # 롤백이 없어서 **반쯤 만들어진 배치**가 가장 나쁘다 — 하나라도 어긋나면 시작을 안 한다.
    pre = validate_bulk(mode, items, c.bulk_lookup())
    if not pre.get("ok"):
        return {"ok": False, "created": [], "failed": [], "errors": pre.get("errors"),
                "error": "규칙에 맞지 않아 만들지 않았습니다. 고쳐서 다시 승인을 받으세요."}

    ok, why = approval.consume(approval_token, "create_tickets",
                               {"mode": mode, "items": items})
    if not ok:
        return _denied(why)
    try:
        return c.bulk_create(mode, items, desc_to_field=c.desc_field_value)
    except Exception as e:
        return {"ok": False, "created": [], "failed": [], "error": str(e)[:300]}


@tool
def update_ticket(key: str, approval_token: str, assignee: str = None, duedate: str = None,
                  priority: str = None, summary: str = None, labels: list = None,
                  components: list = None) -> dict:
    """기존 티켓의 **속성을 바꾼다**(담당자·마감일·우선순위·제목·라벨·컴포넌트). 승인 토큰 필요.

    준 것만 바뀐다. 비우려면 빈 문자열/빈 배열을 명시적으로 준다.
    담당자를 뗄 때는 assignee="" 다. duedate 는 YYYY-MM-DD.

    그 티켓 화면에서 **편집할 수 없는 필드는 거부**된다(권한·워크플로우). 실패하면 사유가 돌아온다.
    """
    changes = compact({"assignee": assignee, "duedate": duedate, "priority": priority,
                       "summary": summary, "labels": labels, "components": components})
    # None 이 아닌데 빈 값인 것(=지우기)은 compact 가 떨궈 버리므로 되살린다.
    for k, v in (("assignee", assignee), ("duedate", duedate), ("summary", summary),
                 ("labels", labels), ("components", components)):
        if v is not None and k not in changes:
            changes[k] = v
    if not changes:
        return {"ok": False, "error": "바꿀 필드를 하나도 주지 않았습니다."}

    ok, why = approval.consume(approval_token, "update_ticket", {"key": key, "changes": changes})
    if not ok:
        return _denied(why)

    c = client()
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

    if not fields:
        return {"ok": False, "error": f"이 티켓에서 편집할 수 없는 필드입니다: {', '.join(denied)}"}
    try:
        c.update_fields(key, fields)
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
    return compact({"ok": True, "key": key, "updated": sorted(fields),
                    "skipped": sorted(denied) or None})


@tool
def add_ticket_comment(key: str, body: str, approval_token: str) -> dict:
    """티켓에 **코멘트를 남긴다**. 승인 토큰 필요.

    티켓을 만든 뒤 "왜 이렇게 쪼갰는지 / 왜 이 담당자인지"를 남겨 두면, 나중에 이 대화를 못 본
    사람도 맥락을 안다. 다만 사용자가 요청하지 않은 코멘트를 임의로 달지는 않는다.
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
    """두 티켓을 **잇는다**(Relates/Blocks/Duplicate/Cloners 등). 승인 토큰 필요.

    버그를 만들면 원인 Task·중복 티켓과 이어 둬야 다음 사람이 맥락을 좇을 수 있다.
    relation 은 이 Jira 에 실재하는 링크 타입 이름이어야 한다 — 지어내지 말고,
    Relates 가 아니면 list_ticket_options 대신 서버가 아는 타입(link_types)에 맞춰라.
    방향은 outward(key → other_key). "DL-1 이 DL-2 를 막는다"면 key=DL-1, relation=Blocks.
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
    """티켓에 **관련 문서 링크**(Confluence·웹)를 단다. 승인 토큰 필요.

    조사에서 찾은 설계 문서·요구사항 문서를 티켓에 걸어 두면, 티켓만 연 사람도 근거를
    바로 좇을 수 있다. 같은 URL 을 다시 걸면 한 줄로 갱신된다(중복이 쌓이지 않는다).
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
    """그 티켓에서 **지금 가능한 상태 전이** 목록. 부작용 없음.

    상태 이름은 프로젝트마다 다르다 — 지어내지 말고 여기서 얻은 id 를 transition_ticket 에 넘긴다.
    """
    try:
        return [compact({"id": t.get("id"), "name": t.get("name"),
                         "to": (t.get("to") or {}).get("name")})
                for t in (client().transitions(key) or [])]
    except Exception as e:
        return [{"error": str(e)[:200]}]


@tool
def transition_ticket(key: str, transition_id: str, approval_token: str,
                      comment: str = None, assignee: str = None) -> dict:
    """티켓 **상태를 옮긴다**(예: 진행중 → 완료). 승인 토큰 필요.

    transition_id 는 list_transitions 에서 얻은 값이어야 한다. 그 전이 화면에 없는 필드
    (comment·assignee 등)를 주면 Jira 가 거부하므로, 전이 목록에서 확인한 것만 넘긴다.
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

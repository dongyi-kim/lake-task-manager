"""Existing-ticket mutation planning for the Work Architect facade.

The facade supplies policy callbacks at invocation time.  That explicit seam preserves the
legacy monkeypatch surface while keeping the large mutation pipeline out of the orchestration
class.
"""

from __future__ import annotations

import re as _re
from dataclasses import dataclass
from typing import Any, Callable, Mapping


Policy = Callable[..., Any]


@dataclass(frozen=True)
class ChangePlanPolicies:
    """Facade-owned policies used while assembling one reviewed mutation plan."""

    priority_map: Mapping[str, str]
    bulk_comment_preview: Policy
    comment_forbidden: Policy
    comment_input_missing: Policy
    current_request_boundary_text: Policy
    explicit_meeting_update_fields: Policy
    is_change_action: Policy
    materialize_requested_update_effects: Policy
    meeting_decision_comment: Policy
    meeting_unchanged_fields: Policy
    normalize_priority: Policy
    relative_due: Policy
    requested_assignee_name: Policy
    same_field_value: Policy
    ticket_exists: Policy
    typed_target_keys: Policy
    work_action: Policy


def build_change_plan(state, out, items, questions, policies: ChangePlanPolicies):
    """Finalize an existing-ticket change plan and return ``(plan, questions)``."""
    _PRI = policies.priority_map
    _bulk_comment_preview = policies.bulk_comment_preview
    _comment_forbidden = policies.comment_forbidden
    _comment_input_missing = policies.comment_input_missing
    _current_request_boundary_text = policies.current_request_boundary_text
    _explicit_meeting_update_fields = policies.explicit_meeting_update_fields
    _is_change_action = policies.is_change_action
    materialize_requested_update_effects = policies.materialize_requested_update_effects
    _meeting_decision_comment = policies.meeting_decision_comment
    _meeting_unchanged_fields = policies.meeting_unchanged_fields
    _normalize_priority = policies.normalize_priority
    _relative_due = policies.relative_due
    _requested_assignee_name = policies.requested_assignee_name
    _same_field_value = policies.same_field_value
    _ticket_exists = policies.ticket_exists
    _typed_target_keys = policies.typed_target_keys
    _work_action = policies.work_action
    qs = questions

    action = _work_action(state)
    update_allowed = action in {"update", "mixed"}
    change = out.get("change") if isinstance(out.get("change"), dict) else {}
    typed_targets = _typed_target_keys(state)
    if typed_targets and not (change.get("key") or change.get("keys")):
        if len(typed_targets) == 1:
            change["key"] = typed_targets[0]
        else:
            change["keys"] = typed_targets
    if (change.get("key") or change.get("keys")) and not _is_change_action(state):
        target = change.get("key") or ", ".join(change.get("keys") or [])
        if (state.get("draft") or {}).get("items"):
            note_text = ("승인 대기 초안에 대한 수정 요청 — 기존 티켓 변경이 아니라 "
                         "초안을 고쳐야 한다")
        else:
            note_text = (f"참고: {target} 가 비슷한 일이지만, 요청은 새로 만드는 것이라 "
                         "변경하지 않았다")
        out["rationale"] = ((out.get("rationale") or "")
                            + f"\n({note_text})").strip()
        change = {}
    plan = {}
    if change.get("key"):
        fields = {key: change[key] for key in (
            "assignee", "duedate", "priority", "summary", "labels", "components",
            "description",
        ) if key in change and change[key] is not None}
        if update_allowed:
            fields.update(_explicit_meeting_update_fields(state))
        else:
            fields = {}
        for unchanged in _meeting_unchanged_fields(state):
            fields.pop(unchanged, None)
        said = _current_request_boundary_text(state)
        wipe = _re.search(r"(담당|assignee)\w*\s*(해제|비워|없애|제거)", said)
        fields = {key: value for key, value in fields.items()
                  if (isinstance(value, list) and value) or str(value or "").strip()
                  or (key == "assignee" and wipe)}
        words = {
            "priority": r"우선순위|priority|P[0-4]|긴급|중요|사소",
            "duedate": r"마감|기한|due|날짜|미뤄|당겨|연장|늦춰|앞당",
            "assignee": r"담당|배정|할당|넘겨|맡",
            "summary": r"제목|이름|타이틀|summary",
            "labels": r"라벨|label|태그",
            "description": r"본문|설명|내용|description",
        }
        extra = [key for key in list(fields)
                 if key in words and not _re.search(words[key], said, _re.I)] \
            if said.strip() else []
        for key in extra:
            fields.pop(key, None)
        if extra:
            out["rationale"] = ((out.get("rationale") or "")
                                + f"\n(요청에 없던 {', '.join(extra)} 변경은 뺐다 — "
                                  "말한 것만 바꾼다)").strip()
        if str(fields.get("priority") or "").strip():
            fields["priority"] = _normalize_priority(fields["priority"])
        relative = _relative_due(_current_request_boundary_text(state))
        if relative and str(fields.get("duedate") or "") != relative:
            if fields.get("duedate"):
                out["rationale"] = ((out.get("rationale") or "")
                                    + f"\n(마감을 {relative} 로 계산해 바로잡았다 — 상대 날짜는 "
                                      "코드가 계산한다)").strip()
            fields["duedate"] = relative
        comment = (change.get("comment") or "").strip()
        if _comment_forbidden(said):
            comment = ""
        else:
            comment = _meeting_decision_comment(state, comment)
        if fields or comment:
            plan = {
                "key": str(change["key"]).strip(),
                "changes": fields,
                "comment": comment,
                "why": out.get("rationale") or "",
            }
            try:
                from app.agent import tools as agent_tools
                current = agent_tools.BY_NAME["get_ticket"].invoke({"key": plan["key"]}) or {}
                if not current.get("error"):
                    plan["before"] = {key: (current.get(key) or "") for key in fields}
                    noops = [key for key, value in fields.items()
                             if _same_field_value(plan["before"].get(key), value)]
                    for field in noops:
                        fields.pop(field, None)
                        plan["before"].pop(field, None)
                    plan["changes"] = fields
                    if noops:
                        actual = ", ".join(fields) or "없음"
                        out["rationale"] = (
                            f"실제 변경 필드: {actual}. "
                            f"이미 같은 {', '.join(noops)} 값은 변경에서 제외"
                        )
                        plan["why"] = out["rationale"]
                    old_due = str(plan["before"].get("duedate") or "")
                    new_due = str(fields.get("duedate") or "")
                    if _re.match(r"^\d{4}-\d{2}-\d{2}$", old_due) and new_due and old_due != new_due:
                        later = _re.search(r"미뤄|미루|연장|늦춰|늦추|뒤로", said)
                        sooner = _re.search(r"당겨|앞당|땡겨|앞으로", said)
                        warning = ("앞당기는" if (later and new_due < old_due)
                                   else ("미루는" if (sooner and new_due > old_due) else ""))
                        if warning:
                            out["rationale"] = (
                                (out.get("rationale") or "")
                                + f"\n(확인 필요: 현재 마감이 {old_due} 라 {new_due} 로 바꾸면 "
                                  f"말씀과 반대로 {warning} 셈이다 — 날짜가 맞는지 봐 달라)"
                            ).strip()
                            plan["why"] = out["rationale"]
            except Exception:
                pass
        key = str(change.get("key") or "").strip()
        wanted_status = str(change.get("status") or "").strip()
        user_status = _re.search(
            r"([가-힣A-Za-z ]{2,16}?)\s*(?:상태)?\s*로\s*(?:옮겨|바꿔|전이|이동)",
            _current_request_boundary_text(state),
        )
        if user_status:
            wanted_status = user_status.group(1).strip()
        if update_allowed and key and wanted_status and not fields and not plan:
            try:
                from app.agent import tools as agent_tools
                candidates = [row for row in (
                    agent_tools.BY_NAME["list_transitions"].invoke({"key": key}) or []
                ) if isinstance(row, dict) and not row.get("error")]
                hit = next((row for row in candidates
                            if wanted_status.lower() in str(row.get("name", "")).lower()
                            or str(row.get("name", "")).lower() in wanted_status.lower()
                            or wanted_status.lower() in str(row.get("to", "")).lower()), None)
                if hit:
                    plan = {
                        "key": key,
                        "transition": {
                            "id": str(hit.get("id")),
                            "name": hit.get("to") or hit.get("name"),
                        },
                        "comment": comment,
                        "why": out.get("rationale") or "",
                    }
                elif candidates:
                    options, seen = [], set()
                    for row in candidates:
                        name = str(row.get("to") or row.get("name") or "").strip()
                        name = _re.sub(r"^(?:To|이동|전이)\s+", "", name).strip()
                        if name and name not in seen:
                            seen.add(name)
                            options.append(name)
                    qs = [{
                        "question": f"{key} 를 '{wanted_status}' 상태로 옮길 수는 없습니다. "
                                    "지금 갈 수 있는 상태는 다음뿐입니다 — 고르시면 "
                                    "그대로 변경 카드를 만들어 드립니다.",
                        "kind": "choice",
                        "field": "status",
                        "required_input": True,
                        "why_required": "요청한 상태 전이가 없어 유효한 도착 상태 선택이 필요함",
                        "options": options[:5],
                    }]
            except Exception:
                pass
        link_payload = change.get("link") if isinstance(change.get("link"), dict) else {}
        if update_allowed and key and link_payload.get("other"):
            link = {
                "other": str(link_payload["other"]).strip(),
                "relation": str(link_payload.get("relation") or "Relates").strip(),
            }
            if not plan:
                plan = {"key": key, "link": link, "comment": comment,
                        "why": out.get("rationale") or ""}
            elif (str(plan.get("key") or "") == key and not (plan.get("changes") or {})
                  and not plan.get("transition") and not plan.get("link")):
                plan["link"] = link
                plan["comment"] = comment
            else:
                plan = {}
                qs = [{
                    "question": f"{key} 링크와 다른 필드·상태 변경을 한 번에 실행할 수 없습니다. "
                                "링크와 나머지 변경을 별도 승인 작업으로 나눌까요?",
                    "kind": "choice",
                    "field": "action_split",
                    "required_input": True,
                    "why_required": "서로 다른 write effect를 별도 승인 지문으로 분리해야 함",
                    "options": ["링크 먼저", "필드·상태 변경 먼저"],
                }]

    bulk_comment = str(change.get("comment") or "").strip()
    bulk_said = _current_request_boundary_text(state)
    if _comment_forbidden(bulk_said):
        bulk_comment = ""
    else:
        bulk_comment = _meeting_decision_comment(state, bulk_comment)
    bulk_keys = [str(key).strip() for key in (change.get("keys") or []) if str(key).strip()]
    if state.get("bulk_targets") and (
            (update_allowed and (change.get("assignee") is not None
                                 or change.get("duedate") is not None
                                 or change.get("priority") is not None
                                 or change.get("labels") is not None))
            or bulk_comment or bulk_keys):
        bulk_keys = [str(key) for key in state["bulk_targets"]]
    if bulk_keys and not plan:
        fields = ({key: change[key] for key in (
            "assignee", "duedate", "priority", "labels", "components",
        ) if key in change and change[key] is not None} if update_allowed else {})
        if str(fields.get("priority") or "").strip():
            fields["priority"] = _normalize_priority(fields["priority"])
        fields = {key: value for key, value in fields.items()
                  if (isinstance(value, list) and value) or str(value or "").strip()}
        real = [key for key in dict.fromkeys(bulk_keys) if _ticket_exists(key)][:30]
        gone = [key for key in bulk_keys if key not in real]
        if gone:
            out["rationale"] = ((out.get("rationale") or "")
                                + f"\n(실재하지 않아 제외: {', '.join(gone[:5])})").strip()
        if real and (fields or bulk_comment):
            if len(real) == 1:
                plan = {"key": real[0], "changes": fields, "comment": bulk_comment,
                        "why": out.get("rationale") or ""}
            else:
                plan = {"keys": real, "changes": fields, "comment": bulk_comment,
                        "why": out.get("rationale") or ""}
                preview = _bulk_comment_preview(real, plan["comment"])
                if preview:
                    plan["comments"] = preview

    if not plan and update_allowed and (state.get("mentioned_keys") or []):
        request = _current_request_boundary_text(state)
        transition = _re.search(
            r"([가-힣A-Za-z ]{2,16}?)\s*(?:상태)?\s*로\s*(?:옮겨|바꿔|전이|이동)", request,
        )
        if transition:
            wanted = transition.group(1).strip()
            key = str(state["mentioned_keys"][0]).strip()
            try:
                from app.agent import tools as agent_tools
                candidates = [row for row in (
                    agent_tools.BY_NAME["list_transitions"].invoke({"key": key}) or []
                ) if isinstance(row, dict) and not row.get("error")]
                hit = next((row for row in candidates
                            if wanted.lower() in str(row.get("name", "")).lower()
                            or wanted.lower() in str(row.get("to", "")).lower()), None)
                if hit:
                    plan = {
                        "key": key,
                        "transition": {
                            "id": str(hit.get("id")),
                            "name": hit.get("to") or hit.get("name"),
                        },
                        "comment": "",
                        "why": ((out.get("rationale") or "")
                                + "\n(상태 전이 — 전이 id 는 코드가 확정)").strip(),
                    }
                    qs = []
                    items.clear()
                elif candidates:
                    qs = [{
                        "question": f"{key} 를 '{wanted}' 로 옮길 전이가 없습니다. "
                                    "가능한 전이 중에서 골라 주세요.",
                        "kind": "choice",
                        "field": "status",
                        "required_input": True,
                        "why_required": "요청한 상태 전이가 없어 유효한 도착 상태 선택이 필요함",
                        "options": [str(row.get("to") or row.get("name"))
                                    for row in candidates][:5],
                    }]
                    items.clear()
            except Exception:
                pass

    if not plan and update_allowed:
        request = _current_request_boundary_text(state)
        keys = _re.findall(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b", request)
        if len(dict.fromkeys(keys)) >= 2 and _re.search(r"연결|링크|link", request):
            first, second = list(dict.fromkeys(keys))[:2]
            relation = "Blocks" if _re.search(r"막|block", request, _re.I) else "Relates"
            if _ticket_exists(first) and _ticket_exists(second):
                plan = {
                    "key": first,
                    "link": {"other": second, "relation": relation},
                    "comment": "",
                    "why": ((out.get("rationale") or "")
                            + f"\n(링크 {relation}: {first} → {second} — 요청에서 코드가 확정)").strip(),
                }
                qs = []
                items.clear()

    if not plan and state.get("bulk_targets") and update_allowed:
        request = _current_request_boundary_text(state)
        fields = {}
        priority = _re.search(r"(?<![0-9A-Za-z])P([0-4])(?![0-9A-Za-z])", request)
        if priority and ("우선순위" in request or "올려" in request
                         or "내려" in request or "로 바꿔" in request):
            fields["priority"] = _PRI["P" + priority.group(1)]
        relative = _relative_due(request)
        if relative and "마감" in request:
            fields["duedate"] = relative
        assignee = _re.search(
            r"(?<![0-9A-Za-z.])(?:skcc\.)?([a-z]{1,2}\d{2,6})(?![0-9A-Za-z])", request,
        )
        if assignee and ("담당" in request or "에게" in request):
            fields["assignee"] = f"skcc.{assignee.group(1)}"
        if fields:
            plan = {
                "keys": [str(key) for key in state["bulk_targets"]],
                "changes": fields,
                "comment": "",
                "why": ((out.get("rationale") or "")
                        + "\n(조건 일괄 수정 — 대상은 JQL 로, 변경 값은 요청에서 "
                          "코드가 확정했다)").strip(),
            }
            qs = []
            items.clear()

    if update_allowed:
        plan = materialize_requested_update_effects(state, plan)

    full_request = _current_request_boundary_text(state)
    if (_is_change_action(state) and _re.search(r"댓글|코멘트", full_request)
            and _comment_input_missing(state, plan)):
        plan = {}
        items.clear()
        qs = [{
            "question": "남길 댓글의 내용이나 전달 목적을 알려 주세요.",
            "kind": "text",
            "options": [],
            "field": "comment",
            "required_input": True,
            "why_required": "외부에 게시할 댓글 내용은 사용자 의도 없이 발명할 수 없음",
        }]

    person_name = _requested_assignee_name(full_request)
    if update_allowed and person_name:
        try:
            from app.agent import tools as agent_tools
            person = agent_tools.BY_NAME["find_person"].invoke({"name": person_name}) or {}
            candidates = person.get("candidates") or []
            if person.get("ambiguous") and candidates:
                plan = {}
                items.clear()
                qs = [{
                    "question": f"'{person_name}' 이름의 사용자가 여러 명입니다. 담당자를 골라 주세요.",
                    "kind": "choice",
                    "field": "assignee",
                    "options": [" · ".join(value for value in (
                        str(candidate.get("display") or person_name),
                        str(candidate.get("id") or ""),
                        str(candidate.get("module") or ""),
                    ) if value)[:120] for candidate in candidates[:5]],
                    "required_input": True,
                    "why_required": "담당자 변경에는 하나의 exact username이 필요함",
                }]
            elif person.get("resolved"):
                user_id = str(person["resolved"])
                if plan:
                    plan.setdefault("changes", {})["assignee"] = user_id
                elif state.get("mentioned_keys"):
                    plan = {
                        "key": str(state["mentioned_keys"][0]),
                        "changes": {"assignee": user_id},
                        "comment": "",
                        "why": "사용자 디렉토리에서 담당자 username 확인",
                    }
                    qs = []
                    items.clear()
        except Exception:
            pass

    if plan and plan.get("changes"):
        planned_keys = ([str(key) for key in (plan.get("keys") or [])]
                        if plan.get("keys") else [str(plan.get("key") or "")])
        done_keys = []
        try:
            from app.agent.tools._ctx import client as ticket_client
            from app.domain.ticket_actions import is_done, reopen_transition
            done_keys = [key for key in planned_keys
                         if key and is_done(ticket_client().ticket_badge(key))]
        except Exception:
            done_keys = []
        if done_keys:
            reopen = None
            if len(done_keys) == 1:
                try:
                    from app.agent import tools as agent_tools
                    reopen = reopen_transition(
                        agent_tools.BY_NAME["list_transitions"].invoke({"key": done_keys[0]}) or [],
                    )
                except Exception:
                    reopen = None
            if reopen:
                options = [f"{done_keys[0]}를 {reopen.get('to') or 'Reopened'}로 전이한다 "
                           "(권장 — 전이 후 속성 변경은 새 승인)"]
                if str(plan.get("comment") or "").strip():
                    options.append("속성은 바꾸지 않고 요청한 댓글만 남긴다")
                options.append("취소한다")
                qs = [{
                    "question": f"{done_keys[0]}는 이미 Done이라 속성을 바꿀 수 없습니다. "
                                "먼저 다시 연 뒤 새 승인으로 속성을 변경해야 합니다.",
                    "kind": "choice",
                    "field": "",
                    "options": options[:3],
                }]
            else:
                key_text = ", ".join(done_keys[:8])
                qs = [{
                    "question": f"{key_text}는 이미 Done이라 속성을 바꿀 수 없습니다. "
                                "현재 Jira가 제공하는 Reopened 전이를 먼저 실행한 뒤 "
                                "새 승인으로 다시 요청해 주세요.",
                    "kind": "choice",
                    "field": "",
                    "options": ["취소한다"],
                }]
            out["rationale"] = ((out.get("rationale") or "")
                                + "\n(Done 티켓의 field update를 차단했다 — comment는 가능하고, "
                                  "Reopened 전이와 속성 변경은 별도 승인이다)").strip()
            plan = {}
            items.clear()

    assignee = (plan.get("changes") or {}).get("assignee") if plan else None
    if assignee:
        try:
            from app.agent.tools._ctx import client as ticket_client, settings as ticket_settings
            from app.domain.search import search_users
            found = search_users(ticket_client(), ticket_settings(), assignee, 5) or []
            if not any(str(user.get("id") or "") == assignee for user in found):
                plan = {}
                qs = [{
                    "question": f"'{assignee}' 는 존재하지 않는 사번입니다. 올바른 사번을 "
                                "알려 주세요 (skcc.x1042 형식 — 자동완성이 붙습니다).",
                    "kind": "text",
                    "options": [],
                    "field": "assignee",
                }]
        except Exception:
            pass

    if (plan and update_allowed and not plan.get("changes")
            and _re.search(r"삭제|지워\s*줘|없애", _current_request_boundary_text(state))):
        plan = {}
        out["rationale"] = ((out.get("rationale") or "")
                            + "\n(삭제는 지원되지 않는다 — 상태 전이(닫음)나 보관 라벨을 "
                              "대안으로 안내)").strip()
    if not plan and not items and _re.search(
            r"스토리\s*포인트|story\s*point|\bSP\b",
            _current_request_boundary_text(state), _re.I):
        out["rationale"] = ((out.get("rationale") or "")
                            + "\n(스토리포인트는 에이전트가 바꾸지 못한다 — 티켓 화면에서 "
                              "직접 입력해야 하고, 애초에 Story 타입에만 설정된다. "
                              "바꿀 수 있는 것: 담당·마감·우선순위·제목·라벨·컴포넌트·본문)"
                            ).strip()
    if plan and plan.get("key") and ((state.get("draft") or {}).get("items")) and not items:
        said_by_user = _current_request_boundary_text(state)
        if plan["key"] not in said_by_user:
            plan = {}
            out["rationale"] = ((out.get("rationale") or "")
                                + "\n(승인 대기 초안에 대한 수정 요청 — 기존 티켓 변경이 "
                                  "아니라 초안을 고쳐야 한다)").strip()

    if plan and plan.get("key") and (plan.get("comments") or []):
        rows = [row for row in plan.get("comments") or [] if isinstance(row, dict)]
        key = str(plan.get("key") or "").strip().upper()
        valid = [row for row in rows
                 if str(row.get("key") or "").strip().upper() == key
                 and str(row.get("body") or "").strip()]
        if len(rows) == len(valid) == 1:
            plan["comment"] = str(plan.get("comment") or valid[0]["body"]).strip()
            plan.pop("comments", None)
        else:
            plan = {}
            items.clear()
            qs = [{
                "question": "단건 변경에 표시된 댓글 대상이 실행 대상과 일치하지 않습니다. "
                            "댓글 대상과 본문을 다시 알려 주세요.",
                "kind": "text",
                "field": "comment",
                "options": [],
                "required_input": True,
                "why_required": "단건 comment를 exact 변경 대상에 결속해야 함",
            }]

    said_all = _current_request_boundary_text(state)
    for pattern, required_words in (
        (r"삭제[^)\n]{0,20}지원되지\s*않", ("삭제", "지워", "없애")),
        (r"스토리\s*포인트[^)\n]{0,20}(?:설정할 수 없|지원)", ("포인트", "SP")),
    ):
        if (_re.search(pattern, str(out.get("rationale") or ""))
                and not any(word in said_all for word in required_words)):
            out["rationale"] = _re.sub(
                r"\n?\([^)\n]*" + pattern + r"[^)\n]*\)", "", out["rationale"],
            ).strip()
            if isinstance(plan, dict) and plan.get("why"):
                plan["why"] = _re.sub(
                    pattern + r"[^\n]*", "", str(plan["why"]),
                ).strip(" .·\n")
    return plan, qs


__all__ = ["ChangePlanPolicies", "build_change_plan"]

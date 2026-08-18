"""agent/editor_author.py — Editor Author가 에디터 본문·코멘트를 써 준다.

챗과 다른 점이 셋이다.

**쓰기가 아니다.** 결과는 에디터에 꽂힐 뿐이고, 저장 버튼은 사람이 누른다. 그래서 승인
토큰이 없다 — HITL 은 이미 에디터 자체가 하고 있다(사용자가 읽고 고치고 지울 수 있다).

**한 번만 부른다.** ReAct 로 도구를 돌리면 몇 초가 몇십 초가 되고, 그 사이 커서는 멈춰
있다. 대신 **코드가 맥락을 미리 모아** 한 번의 호출로 끝낸다(같은 재료를 챗 쪽에서 이미
쓰고 있다 — `progress_report` 를 그대로 재사용한다).

**맥락이 화면에서 온다.** 어느 티켓의, 본문인지 코멘트인지를 에디터가 알고 있다
(`kind`/`ticketKey` props). 그 둘이면 무엇을 써야 하는지가 거의 정해진다.
"""

from __future__ import annotations

import html as _html
import re

MAX_SEED = 6000        # 사용자가 쓰던 글. 이보다 길면 앞부분만 — 시드는 의도 파악용이다
MAX_CONTEXT = 5000     # 티켓 맥락. 프롬프트가 커지면 응답이 느려지고 커서는 계속 멈춰 있다


def _plain_text(value: str) -> str:
    """HTML/엔티티를 걷은 비교용 평문. 모델 출력과 자료의 상태를 대조할 때 쓴다."""
    return _html.unescape(re.sub(r"<[^>]+>", " ", value or ""))


def _remaining_items(value: str) -> list[str]:
    """자료가 명시한 ``남은 건/남은 일``을 항목 단위로 보존한다.

    모델이 관련 문서의 완료 수치를 현재 티켓 결과로 옮기면서, 같은 자료의 "남은 일"을
    완료로 뒤집은 실측 사고(CMP5)가 있었다. 의미 판단 전체를 정규식에 맡기지 않고 자료가
    직접 미완료라고 표시한 짧은 구절만 뽑는다.
    """
    out: list[str] = []
    plain = _plain_text(value)
    for match in re.finditer(r"남은\s*(?:건|일)\s*(?:은|:)?\s*([^\n.]+)", plain):
        raw = re.split(r"\bh[1-6]\.\s*|(?:미결|참고)\s*:", match.group(1), 1)[0]
        for chunk in re.split(r"\s*[,;]\s*", raw):
            chunk = re.sub(r"\([^)]*\)", "", chunk).strip(" -*:;")
            chunk = re.sub(r"(?:입니다|이다|남았습니다|예정입니다)$", "", chunk).strip()
            # 한국어 접속 조사(성능 측정과 문서 정리). '결과 정리'의 한 글자 '결'처럼
            # 단어 내부의 과를 자르지 않도록 왼쪽이 두 글자 이상일 때만 둘로 나눈다.
            pair = re.match(r"^(.{2,40}?)(?:과|와)\s+(.{2,60})$", chunk)
            pieces = list(pair.groups()) if pair else [chunk]
            for item in pieces:
                item = item.strip(" -*:;")
                if 2 <= len(item) <= 80 and item not in out:
                    out.append(item)
    return out


def _ticket_context(key: str, kind: str) -> str:
    """이 에디터가 붙어 있는 티켓의 맥락. 코멘트인지 본문인지에 따라 필요한 게 다르다."""
    key = (key or "").strip().upper()
    if not key or key.startswith("__"):        # 새 티켓 작성 중 — 아직 맥락이 없다
        return ""
    parts = []
    try:
        from app.agent.tools.survey_tools import progress_report
        r = progress_report(key, comment_limit=6)
        if r.get("error"):
            return ""
        owner = f'[~{r["assigneeId"]}]' if r.get("assigneeId") else "없음"
        parts.append(f'[{r["key"]}] "{r.get("title", "")}" — {r.get("status")}'
                     f' · 담당 {owner}'
                     f' · 마감 {r.get("due") or "없음"}')
        remaining = _remaining_items("\n".join(
            [str(m.get("text") or "") for m in (r.get("comments") or [])]
            + [str(d.get("excerpt") or "") for d in (r.get("documents") or [])]))
        if r.get("children"):
            # child status는 관련 ticket/document의 성공보다 우선하는 현재 작업 상태다.
            # 모델이 "API 개선됨"을 "연동 완료"로 비약한 실측(CMP5)을 후검증할 수 있게
            # 미완료 child 제목도 동일한 deterministic marker에 넣는다.
            for child in r["children"]:
                if child.get("done"):
                    continue
                title = re.sub(r"^\s*\[[^\]]+\]\s*", "", str(child.get("title") or "")).strip()
                if title and title not in remaining:
                    remaining.append(title)
            parts.append(f'하위 {r.get("children_done")} 완료: ' + ", ".join(
                f'{c["key"]} "{c.get("title", "")}"'
                f'{"(완료)" if c.get("done") else "(미완료: " + str(c.get("status") or "상태 미상") + ")"}'
                for c in r["children"][:6]))
            # A prose conjunction must never turn two independent Jira fields into one
            # shared state claim. Keep an exact key→status ledger beside the narrative form.
            parts.append("티켓별 현재 상태: " + " | ".join(
                f'{c.get("key")}={c.get("status") or "상태 미상"}'
                for c in r["children"][:6] if c.get("key")))
        if remaining:
            parts.append("명시적 미완료(완료로 쓰지 말 것): " + " | ".join(remaining))
        if r.get("links"):
            parts.append("연결: " + ", ".join(
                f'{x["key"]}({x.get("rel")}) "{x.get("title", "")}"'
                f'{"·해결됨" if x.get("done") else ""}' for x in r["links"][:5]))
        # 코멘트를 쓰는 중이면 **앞선 대화**가 가장 중요한 재료다(무엇에 이어 말하는가).
        if r.get("comments") and kind != "description":
            parts.append("최근 코멘트(오래된 것부터):\n" + "\n".join(
                f'- {m["date"]} {m.get("who")}: {m.get("text", "")[:260]}'
                for m in r["comments"][-5:]))
        for d in (r.get("documents") or [])[:2]:
            parts.append(f'관련 문서 「{d.get("title")}」 {d.get("url")}'
                         + (f'\n  발췌: {d.get("excerpt", "")[:400]}' if d.get("excerpt") else ""))
    except Exception:
        pass
    try:      # 본문을 쓰는 중이면 원문(있다면)이 곧 고쳐 쓸 대상이다
        if kind == "description":
            from app.agent.tools._ctx import client
            from app.agent.tools.search_tools import _strip
            # ticket_view 는 본문을 **HTML 로만** 준다(descriptionHtml) — 태그를 벗겨 싣는다.
            v = client().ticket_view(key) or {}
            body = _strip(v.get("descriptionHtml") or "").strip()
            if body:
                parts.append("현재 본문(고쳐 쓸 대상):\n" + body[:1200])
            # ── 계보 — 본문은 제목·상위 Epic·자식 유무만으로도 무엇을/어떻게 쓸지 정해진다
            # (사용자 지적: 댓글과 본문의 차이). 자식이 있으면 본문의 역할이 달라진다.
            ek, et = v.get("epicKey") or "", v.get("epicName") or v.get("epicSummary") or ""
            if ek:
                if not et:
                    try:
                        ev = client().ticket_view(ek) or {}
                        et = ev.get("summary") or ev.get("title") or ""
                    except Exception:
                        pass
                parts.append(f"상위 Epic: {ek} \"{et}\" — 배경은 이 이니셔티브에 잇되 "
                             "Epic 본문을 복사하지 말고 키 참조로")
            ancestors = client().ticket_ancestors(key) or []
            direct = next((a for a in reversed(ancestors)
                           if str(a.get("type") or "") != "Epic"), None)
            if direct:
                parts.append(
                    f'직접 상위 {direct.get("type") or "Task"}: {direct.get("key")} '
                    f'"{direct.get("summary") or ""}" — 현재 티켓의 가장 가까운 업무 배경과 '
                    "참고 출처로 우선 사용"
                )
    except Exception:
        pass
    return "\n\n".join(parts)[:MAX_CONTEXT]


def _generic_status_share(prompt: str) -> bool:
    """Whether the user requested a plain current-status comment with no editorial angle."""
    return bool(re.fullmatch(
        r"\s*(?:진행\s*상황\s*공유(?:\s*코멘트)?\s*(?:써\s*줘)?|"
        r"상태\s*공유|진척\s*공유)\s*", str(prompt or ""), re.I,
    ))


def _deterministic_status_comment(ticket_key: str) -> str:
    """Project verified progress into a concise comment without free-form status invention."""
    try:
        from app.agent.tools.survey_tools import progress_report
        report = progress_report(str(ticket_key or "").strip().upper(), comment_limit=6) or {}
    except Exception:
        return ""
    key = str(report.get("key") or "").strip().upper()
    if not key or report.get("error"):
        return ""

    def badge(value: str) -> str:
        safe = _html.escape(str(value or "").strip().upper())
        return (f'<a class="jira-badge tkt" data-key="{safe}" '
                f'href="/browse/{safe}">{safe}</a>')

    status = _html.escape(str(report.get("status") or "상태 미상"))
    due = _html.escape(str(report.get("due") or "미정"))
    rows = [f"<p>{badge(key)} 현재 상태 <strong>{status}</strong> · 마감 {due}</p>"]
    children = [row for row in (report.get("children") or []) if isinstance(row, dict)]
    done = [row for row in children if row.get("done")]
    open_rows = [row for row in children if not row.get("done")]
    if children:
        rows.append("<ul>")
        if done:
            rows.append("<li>완료된 하위 작업: " + " ".join(
                badge(row.get("key")) for row in done if row.get("key")) + "</li>")

        materials = "\n".join(
            [str(row.get("text") or "") for row in (report.get("comments") or [])]
            + [str(row.get("excerpt") or "") for row in (report.get("documents") or [])]
        )
        for row in open_rows:
            title = re.sub(r"^\s*\[[^\]]+\]\s*", "", str(row.get("title") or "")).strip()
            reported_done = (_topic_matches(title, materials)
                             and bool(re.search(r"완료|붙였|마쳤|끝났", materials)))
            if reported_done:
                rows.append(
                    f"<li>상태 확인 필요: {badge(row.get('key'))} Jira는 "
                    f"{_html.escape(str(row.get('status') or '미완료'))}, "
                    "최근 코멘트·문서는 완료로 보고</li>"
                )
            else:
                rows.append(
                    f"<li>진행 중: {badge(row.get('key'))} · "
                    f"{_html.escape(str(row.get('status') or '상태 미상'))}</li>"
                )
        rows.append("</ul>")

    material = "\n".join(
        [str(row.get("text") or "") for row in (report.get("comments") or [])]
        + [str(row.get("excerpt") or "") for row in (report.get("documents") or [])]
    )
    remaining = _remaining_items(material)
    if remaining:
        rows.append("<p><strong>남은 확인 항목</strong>: "
                    + ", ".join(_html.escape(value) for value in remaining) + "</p>")
    return "\n".join(rows)


def _house_rules(kind: str, prompt: str) -> str:
    """사내 작성 규율 — 정적 RAG 에서 끌어온다(프롬프트에 규칙을 복사해 두면 갈라진다)."""
    try:
        from app.agent.tools.rag_tools import search_rules
        q = ("티켓 본문 작성 가이드 배경 작업 범위 완료 조건" if kind == "description"
             else "코멘트 작성 멘션 표기") + " " + (prompt or "")[:80]
        hits = search_rules.invoke({"question": q, "k": 3}) or []
        rows = [str(h.get("rule") or "")[:700] for h in hits if h.get("rule")]
        return "\n\n".join(rows)
    except Exception:
        return ""


class EditorAuthor:
    """Canonical Editor Author entrypoint. API endpoint 이름과 Role 식별자를 섞지 않는다."""

    name = "editor_author"

    def compose(self, ticket_key: str = "", kind: str = "comment", prompt: str = "",
                seed_html: str = "", user_id: str = "") -> dict:
        return compose(ticket_key, kind, prompt, seed_html, user_id)


def compose(ticket_key: str = "", kind: str = "comment", prompt: str = "",
            seed_html: str = "", user_id: str = "") -> dict:
    """에디터에 꽂을 HTML 을 만든다. 돌려주는 것: {ok, html, note}."""
    from app.agent import config as C
    from app.agent.prompts.roles import SYSTEM_EDITOR_AUTHOR
    from app.agent.workflow.prompts import data_block, persona, wrap_data

    ready, why = C.llm_ready()
    if not ready:
        return {"ok": False, "needsSetup": True,
                "error": why + " 설정에서 LLM 연결을 먼저 등록하세요."}
    prompt = (prompt or "").strip()
    seed = (seed_html or "").strip()[:MAX_SEED]
    kind = (kind or "comment").strip()
    if not prompt and not seed:
        return {"ok": False, "error": "무엇을 써 드릴지 알려 주세요."}
    # ── 모호 사전 판정(코드) — 티켓 맥락도 시드도 없는데 프롬프트가 지시어뿐이면
    # LLM 을 부를 것도 없이 보완을 요청한다(모델 판정은 흔들렸다 — 실측 CMP4 회귀).
    # 코멘트는 대화(문맥 필수), 본문은 문서(제목·계보로도 쓸 수 있다) — 사전 판정은
    # **코멘트에 더 엄격**하다. 본문은 티켓이 없을 때만 막는다(새 티켓+지시어뿐).
    bare_key = not (ticket_key or "").strip() or (ticket_key or "").startswith("__")
    seedless = not _re_strip(seed)
    if bare_key and seedless and len(prompt) < 15:
        return {"ok": False, "needsInfo": True,
                "error": ("이대로는 정확한 글을 쓸 수 없습니다 — 무엇에 대한 글인지 목적과 "
                          "대상을 한 줄만 적어 주세요 (예: '수집 파이프라인 개선 작업 본문')")}

    ctx = _ticket_context(ticket_key, kind)
    # A generic status-sharing comment is a projection of progress_report, not a writing
    # task.  Free-form generation repeatedly converted explicit remaining work to completed
    # work and then had to be rejected.  Deterministic rendering is faster and preserves
    # status conflicts instead of choosing one source.
    if kind == "comment" and _generic_status_share(prompt):
        deterministic = _deterministic_status_comment(ticket_key)
        if deterministic:
            return {"ok": True, "html": deterministic, "deterministic": True,
                    "usage": {"calls": 0, "promptTokens": 0,
                              "completionTokens": 0, "totalTokens": 0}}
    rules = _house_rules(kind, prompt)
    what = {"description": "ticket description", "comment": "comment",
            "transition": "comment accompanying a status transition"}.get(kind, "comment")

    task = f"""\
# Task

Write a Korean {what}. The result is inserted directly into the user's editor. Return only the HTML body—no greeting, explanation, quotation wrapper, or code fence.

## Sufficiency Boundary

- If a safe draft is possible, write it. If a material fact prevents one, return `NEED_INFO:` on the first line followed by one concise Korean question.
- A comment is conversational. Use `NEED_INFO:` only when there is no recent comment, ticket state, seed text, or relevant subject to continue from, or when the request concerns a different subject.
- A description is a document. When ticket title, Epic lineage, children, or related tickets establish the work, draft it even if the user prompt is short.
- With ticket context, a review request, confirmation request, or status question can be drafted without knowing the eventual result. Do not invent the result.

## Grounding Rules

- When the user requests an assignee mention, use the verified assignee username from ticket context as `[~username]` and state the review target or request. A missing review result is not a reason for `NEED_INFO:`.
- Preserve the subject and state of every fact. A metric from a linked ticket or document is not automatically the current ticket's result.
- Never convert a context item marked `명시적 미완료`, `남은`, `예정`, or `진행 중` into completed work.
- When sources conflict, state the conflict and what needs confirmation in Korean instead of selecting one state. For example: `구현 완료 보고가 있으나 Jira 상태는 In Progress — 최종 상태 확인 필요`.
- Do not add a feature, UI change, performance target, scope item, or DoD absent from the user request, seed, ticket title, or current body. When a criterion is unspecified, write the precise Korean marker `담당팀 확인 필요`.
- An unfinished seed can be ambiguous. Preserve it verbatim and mark the missing direction or value as `확인 필요`; never complete `높다/낮다`, a cause, or a result from grammar alone.
- A review request must name the review target and any verified criterion and source document present in ticket context. Do not say only "검토해 주세요" when those facts are available.
- When child Sub-Tasks or a split plan exist, the parent description owns the overall why, scope, and DoD. Do not repeat every child summary as parent execution detail; keep the parent scope consistent with its children.
- For a Sub-Task description, explain its purpose through the direct parent Task before the broader Epic. Prefer the closest relevant source in `참고`; do not cite a broad Epic merely because it exists.

## User Request Data

{prompt or "(no separate instruction: complete the existing draft data below)"}
{wrap_data(
    data_block("Existing Editor Draft: Preserve Its Intent and Useful Content", seed),
    data_block("Verified Ticket Context: Do Not Add Facts Absent Here", ctx),
    data_block("Applicable Internal Authoring Rules", rules))}"""

    llm_usage = {}
    meter = None
    try:
        from app.agent.usage import Meter, callback
        from app.agent.workflow.role_manifest import ROLE_SPECS
        meter = Meter()
        handler = callback(meter)
        state = {"user_id": user_id or "", "user_identity": ""}
        role = ROLE_SPECS[EditorAuthor.name]
        layer = role.execution_layer
        invoke_config = {
            "metadata": {
                "ltm_role_id": role.id,
                "ltm_output_contract": "text",
                "ltm_execution_layer": layer,
                "ltm_execution_stage": "synthesis",
            }
        }
        if handler:
            invoke_config["callbacks"] = [handler]
        llm = C.get_llm(tier=C.execution_tier(layer), profile=role.task_profile,
                        role_id=role.id)
        messages = [("system", persona(state, SYSTEM_EDITOR_AUTHOR, role_id=role.id)),
                    ("user", task)]
        try:
            msg = llm.invoke(messages, config=invoke_config)
        except TypeError as exc:
            # 최소 custom/OpenAI-compatible adapter 중 Runnable ``config`` 인자를 구현하지
            # 않은 것도 있다. 계량 때문에 본 기능을 막지 않고 unmetered 호출로 폴백한다.
            if "unexpected keyword argument 'config'" not in str(exc):
                raise
            msg = llm.invoke(messages)
        llm_usage = meter.snapshot()
        html = str(getattr(msg, "content", msg) or "").strip()
    except Exception as e:
        from app.agent.workflow.session import _friendly_error
        # The callback records an error call as soon as the provider invocation fails.
        # Preserve that latency/stage diagnostic instead of dropping the exact call that
        # explains an editor failure.  A factory/configuration error before invocation is
        # still represented by the same zero-call snapshot shape as other compose paths.
        llm_usage = meter.snapshot() if meter is not None else llm_usage
        return {"ok": False, "error": _friendly_error(str(e)), "usage": llm_usage}

    html = _normalize_editor_markup(_unfence(html))
    html = _preserve_ambiguous_seed(html, seed, prompt)
    # ── 피드백 루프: 모호해서 못 쓴다는 신호 — 일반론을 지어내는 것보다 낫다(사용자 요청).
    #    UI 는 팝업을 유지한 채 이 문구를 보여 주고 프롬프트·시드 보완을 유도한다.
    ask = _need_info(html)
    if ask:
        if ctx and (re.search(r"(?:티켓.{0,30}관련|관련.{0,30}티켓)", ask)
                    or _unrelated_information_request(prompt, ctx)):
            ask = ("현재 티켓과 무관한 요청입니다 — 이 티켓에 남길 댓글의 목적이나 "
                   "전달 내용을 알려 주세요")
        return {"ok": False, "needsInfo": True,
                "error": "이대로는 정확한 글을 쓸 수 없습니다 — " + ask,
                "usage": llm_usage}
    if not html:
        return {"ok": False,
                "error": "생성된 내용이 비어 있습니다. 요청을 조금 더 구체적으로 적어 주세요.",
                "usage": llm_usage}
    # 언급은 전부 **뱃지**여야 한다(사용자 지시: plain text 금지). 모델이 평문으로 남긴
    # 티켓 키·[~사번] 을 에디터가 뱃지로 파싱하는 마크업으로 바꾼다 — 보장은 코드가 한다.
    # Composer는 아직 plain HTML을 반환하는 legacy adapter다. 새 role contract의
    # ``{{ref:id}}``/``{{mention:id}}``를 모델이 흉내 내더라도 그대로 badgeify하면
    # ``{{ref:<a ...>DL-1</a>}}``처럼 placeholder 안에 anchor가 생긴다. 이 경로에서는
    # 확인 가능한 key/uid를 legacy 표기로 먼저 내린 뒤 기존 parser가 뱃지화한다.
    html = _ensure_review_context(html, prompt, ctx)
    html = _legacy_reference_tokens(html)
    source = "\n".join((prompt, seed, ctx))
    # A compatible model occasionally drops one project-key character while copying a
    # nearby exact title (for example, `AB-123` -> `A-123`).  Repair only when both the
    # unique numeric candidate and its canonical source title match; numeric suffix alone
    # is never identity evidence.
    html = _badgeify(html, ticket_aliases=_source_ticket_aliases(html, source))
    unsupported_people = _unverified_editor_person_ids(html, prompt, source)
    if unsupported_people:
        return {"ok": False, "contentConflict": True,
                "error": ("AI 생성문에 확인할 수 없는 사람 참조가 있어 삽입하지 않았습니다: "
                          + ", ".join(unsupported_people[:5])),
                "usage": llm_usage}
    html = _ground_editor_person_mentions(html, prompt, source)
    if kind != "description":
        html = _normalize_unfinished_checklist_labels(html, ctx)
    # The editor may mention only entities supplied by the user or the verified ticket context.
    # Existence alone is insufficient: a real but unrelated ticket is still unsupported evidence.
    unsupported_tickets = _unverified_editor_ticket_keys(html, source)
    if unsupported_tickets:
        return {"ok": False, "contentConflict": True,
                "error": ("AI 생성문에 현재 자료로 확인할 수 없는 ticket 참조가 있어 "
                          "삽입하지 않았습니다: " + ", ".join(unsupported_tickets[:5])),
                "usage": llm_usage}
    html = _drop_unverified_editor_ticket_claims(html, source)
    html = _ground_acceptance_metrics(html, source)
    if kind == "description":
        html = _drop_unrequested_description_quality_claims(html, source)
        html = _drop_parent_child_execution_repetition(html, ctx)
        html = _sharpen_editor_dod(html, ctx, prompt)
        html = _dedupe_editor_list_items(html)
    html = _drop_generic_editor_closer(html)
    html = _drop_unverified_editor_dates(html, source)
    html = _repair_dangling_editor_ending(html)
    html = _qualify_non_done_ticket_claims(html, ctx)
    html = _bind_ticket_status_claims(html, ctx)

    # 의미 후검증 — 자료가 명시적으로 '남은 일'이라고 한 대상을 완료로 뒤집은 문장은
    # 사용자가 자기 이름으로 게시하기 전에 차단한다. 경고만 띄우고 삽입하면 토스트를 놓친
    # 사용자가 그대로 저장할 수 있으므로, 이 충돌은 성공 응답으로 내리지 않는다.
    conflicts = _status_conflicts(html, ctx)
    if conflicts:
        html = _qualify_status_conflicts(html, conflicts)
        conflicts = _status_conflicts(html, ctx)
        if conflicts:
            return {"ok": False, "contentConflict": True,
                    "error": ("AI 생성문이 현재 자료와 충돌해 삽입하지 않았습니다 — 자료상 아직 "
                              "남은 항목을 완료로 썼습니다: " + ", ".join(conflicts[:4])),
                    "usage": llm_usage}

    # Resolve first, then replace generated shorthand titles with the canonical labels. A reference can
    # exist while the prose gives it the wrong title; normalizing before grounding avoids a contradictory
    # "resolved but unverified" UI state without hiding an actual unresolved entity.
    references = []
    unresolved = []
    try:
        from app.agent.references import (render_editor_references, resolve_references,
                                          validate_editor_html)
        candidates = _reference_candidates(html)
        allowed_urls = {_html.unescape(value).rstrip(".,;:!?)]}") for value in re.findall(
            r"https?://[^\s<>\"']+", source, re.I)}
        unsupported_urls = sorted({
            str(item.get("url") or "") for item in candidates
            if item.get("kind") in {"document", "external"}
            and str(item.get("url") or "") not in allowed_urls
            and str(item.get("url") or "") not in _html.unescape(source)
        })
        if unsupported_urls:
            return {"ok": False, "contentConflict": True,
                    "error": ("AI 생성문에 현재 자료로 확인할 수 없는 URL이 있어 "
                              "삽입하지 않았습니다: " + ", ".join(unsupported_urls[:3])),
                    "usage": llm_usage}
        resolved = resolve_references(candidates)
        references = resolved.get("references") or []
        unresolved = resolved.get("unresolved") or []
        if unresolved:
            message = ", ".join(str(item.get("id") or "") for item in unresolved[:5])
            return {"ok": False, "contentConflict": True,
                    "error": ("AI 생성문에 해결할 수 없는 참조가 있어 삽입하지 않았습니다: "
                              + message),
                    "references": references, "usage": llm_usage}
        html = _normalize_editor_ticket_titles(html, references)
        html = render_editor_references(html, references)
        final_check = validate_editor_html(html, references)
        if not final_check.get("ok"):
            issue_text = ", ".join(
                f'{item.get("code")}: {item.get("value")}'
                for item in (final_check.get("issues") or [])[:5])
            return {"ok": False, "contentConflict": True,
                    "error": ("AI 생성문의 최종 editor 렌더링 계약이 안전하지 않아 "
                              "삽입하지 않았습니다 — " + issue_text),
                    "references": references, "usage": llm_usage}
    except Exception as exc:
        return {"ok": False, "contentConflict": True,
                "error": ("AI 생성문의 참조를 안전하게 확인하지 못해 삽입하지 않았습니다: "
                          + str(exc)[:180]),
                "usage": llm_usage}

    # 접지 — 챗과 **같은 검사**를 태운다. 에디터에 꽂히는 글이라고 날조를 봐줄 이유가 없다.
    try:
        from app.agent.workflow import grounding
        # Ground visible prose, not HTML attributes such as data-key/href. Attribute copies of a key
        # otherwise look like a quoted title claim and create a false warning after successful resolve.
        bad = grounding.check(_plain_text(html))
        unknown = ((bad.get("fake_keys") or []) + (bad.get("fake_people") or []))
        if unknown:
            return {"ok": False, "contentConflict": True,
                    "error": ("AI 생성문에 확인되지 않은 항목이 있어 삽입하지 않았습니다: "
                              + ", ".join(str(x) for x in unknown[:5])),
                    "references": references, "usage": llm_usage}
        wrong_titles = list((bad.get("wrong_titles") or {}))
        if wrong_titles:
            return {"ok": False, "contentConflict": True,
                    "error": ("AI 생성문에 canonical 제목과 다른 ticket 표기가 남아 있어 "
                              "삽입하지 않았습니다: "
                              + ", ".join(str(x) for x in wrong_titles[:5])),
                    "references": references, "usage": llm_usage}
    except Exception:
        pass
    # UI가 ticket/person/document를 다시 regex 추측하지 않도록 canonical reference bundle을
    # 함께 보낸다. 해결 실패는 위의 final authority gate에서 성공 응답 전에 차단한다.
    return {"ok": True, "html": html, "note": "", "references": references,
            "usage": llm_usage}


def _re_strip(html: str) -> str:
    """태그를 벗긴 실질 텍스트 — 빈 <p></p> 시드를 '내용 있음'으로 오판하지 않기 위해."""
    import re
    return re.sub(r"<[^>]+>", "", html or "").strip()


def _need_info(value: str) -> str:
    """모델의 보완 요청 신호를 HTML·인라인 코드 래퍼와 무관하게 읽는다."""
    plain = _plain_text(_unfence(value)).strip().strip("`'\"“”‘’ ")
    match = re.match(r"NEED_INFO:\s*(.+)", plain, re.S | re.I)
    return match.group(1).strip().strip("`'\"“”‘’ ")[:300] if match else ""


def _normalize_editor_markup(value: str) -> str:
    """Recover a pure Markdown/plain provider response into editor HTML once.

    Existing HTML is never fed through the Markdown renderer because escaping it would
    destroy already-typed badges and task lists.  A hybrid HTML/Markdown response keeps its
    bytes and is rejected by the final validator if raw syntax remains.
    """
    out = str(value or "").strip()
    if not out:
        return ""
    has_editor_html = bool(re.search(
        r"</?(?:p|br|hr|h[1-6]|ul|ol|li|a|span|strong|em|s|code|pre|blockquote|"
        r"table|thead|tbody|tfoot|tr|td|th)\b", out, re.I))
    if has_editor_html:
        return out
    markdown_links = re.findall(r"!?\[[^\]\n]*\]\(([^)\s]+)\)", out)
    if any(not re.match(r"^https?://", destination, re.I)
           for destination in markdown_links):
        # The shared renderer intentionally drops unsupported destinations.  In an Agent
        # draft that would silently erase meaning, so preserve the raw token for the final
        # validator to reject instead.
        return out
    try:
        from app.content.mdhtml import markdown_to_html
        return markdown_to_html(out)
    except Exception:
        return out


def _preserve_ambiguous_seed(rendered: str, seed: str, prompt: str) -> str:
    """Keep an unfinished user observation without guessing its missing direction."""
    plain = _plain_text(seed).strip()
    if not plain or not re.search(r"(?:생각보다|예상보다|기대보다|그런데|하지만|인데)\s*$", plain):
        return rendered
    if not re.search(r"이어|완성|계속", prompt or ""):
        return rendered
    safe = _html.escape(plain)
    return (f"<p>{safe}… <strong>확인 필요</strong>: 비교 기준과 결과 방향"
            "(높음/낮음)을 확인한 뒤 문장을 확정합니다.</p>")


def _ensure_review_context(rendered: str, prompt: str, context: str) -> str:
    """Attach verified review criteria and a real document link when context supplies them."""
    asked = str(prompt or "")
    if not ("검토" in asked and re.search(r"성능\s*측정|측정\s*결과", asked)):
        return rendered
    out = str(rendered or "")
    metric = re.search(r"성능\s*측정\s*\(([^)<>]{2,80})\)", context or "")
    doc = re.search(r"관련\s*문서\s*「([^」]+)」\s*(https?://\S+)", context or "")
    pieces = []
    if metric and metric.group(1) not in _plain_text(out):
        pieces.append("검토 기준: " + _html.escape(metric.group(1).strip()) + "의 측정 결과")
    if doc and doc.group(2) not in out:
        title, url = doc.group(1).strip(), doc.group(2).rstrip(".,)")
        pieces.append(f'근거 문서: <a href="{_html.escape(url, quote=True)}">'
                      f'{_html.escape(title)}</a>')
    if pieces:
        out = out.rstrip() + "<p>" + " · ".join(pieces) + "</p>"
    return out


def _drop_generic_editor_closer(rendered: str) -> str:
    """내용 없는 마지막 인사·업데이트 약속을 제거한다.

    에디터 결과는 사용자가 그대로 게시하므로 `추가 상황이 있으면 업데이트하겠습니다` 같은
    문장은 확인된 다음 행동도 아니고 담당/기한도 없는 상투구다. 마지막 paragraph가 이 패턴일
    때만 제거해 본문 중간의 구체 후속 조치는 보존한다.
    """
    out = str(rendered or "")
    # 마지막 paragraph의 앞부분에 유용한 출처가 있고 끝 문장만 상투구인 경우도 제거한다.
    sentence = (r"\s*(?:추가(?:적인)?|그 밖의)[^<.!?]{0,90}"
                r"(?:업데이트하겠습니다|공유하겠습니다|말씀해\s*주세요|알려\s*주세요)"
                r"[.!?]?\s*(?=</p>\s*$)")
    out = re.sub(sentence, "", out, flags=re.I | re.S)
    out = re.sub(r"\s*<p\b[^>]*>\s*</p>\s*$", "", out, flags=re.I | re.S)
    pattern = (r"\s*<p\b[^>]*>\s*(?:추가(?:적인)?|그 밖의)[^<]{0,90}"
               r"(?:업데이트하겠습니다|공유하겠습니다|말씀해\s*주세요|알려\s*주세요)"
               r"[^<]{0,20}</p>\s*$")
    out = re.sub(pattern, "", out, flags=re.I | re.S).rstrip()
    # Some compatible providers ignore the HTML-only contract and return plain text.
    # Apply the same terminal-only rule there; concrete next actions remain untouched.
    plain_pattern = (r"(?:\s|^)(?:추가(?:적인)?|그 밖의)[^\n.!?]{0,90}"
                     r"(?:업데이트하겠습니다|공유하겠습니다|말씀해\s*주세요|알려\s*주세요)"
                     r"[.!?]?\s*$")
    return re.sub(plain_pattern, "", out, flags=re.I).rstrip()


def _unrelated_information_request(prompt: str, context: str) -> bool:
    """Recognize a topic switch that asks for information, not editor content.

    A comment composer once answered ``김치찌개 레시피`` with a recipe-detail
    interview.  The correct recovery is to return to the open ticket and ask what the
    user intended to post.  New content instructions (``…라고 댓글 남겨줘``) remain
    valid even when their words are absent from the existing ticket.
    """
    request = str(prompt or "").strip()
    if not request or re.search(
            r"댓글|본문|코멘트|남겨|작성|써\s*줘|추가|수정|반영|기록|공유|전달|안내",
            request, re.I):
        return False
    if not re.search(r"알려|설명|추천|어떻게|무엇|뭐|왜|레시피", request, re.I):
        return False
    stop = {"알려줘", "알려", "설명", "추천", "어떻게", "무엇", "뭐", "왜", "관련",
            "현재", "티켓", "작업", "내용", "결과", "요청", "주세요"}
    words = {word.casefold() for word in re.findall(r"[0-9A-Za-z가-힣_.-]{2,}", request)
             if word.casefold() not in stop}
    haystack = re.sub(r"\s+", "", str(context or "")).casefold()
    return bool(words and not any(re.sub(r"\s+", "", word) in haystack for word in words))


def _editor_person_boundary(prompt: str, source: str) -> tuple[set[str], str, bool]:
    allowed = set(re.findall(r"\[~([A-Za-z0-9._-]+)\]", source or ""))
    primary_match = re.search(
        r'^\[[A-Z][A-Z0-9]*-\d+\].*?·\s*담당\s+\[~([A-Za-z0-9._-]+)\]',
        source or "", re.M,
    )
    primary = primary_match.group(1) if primary_match else ""
    asks_assignee = bool(re.search(r"멘션|담당자|담당\s*(?:을|에게|한테)", prompt or ""))
    return allowed, primary, asks_assignee


def _unverified_editor_person_ids(rendered: str, prompt: str, source: str) -> list[str]:
    """Return unsupported ids unless the explicit-assignee request has one exact replacement."""
    allowed, primary, asks_assignee = _editor_person_boundary(prompt, source)
    ids = set(re.findall(
        r'<span\b[^>]*\bdata-(?:id|uid)=["\']([A-Za-z0-9._-]+)["\'][^>]*>',
        str(rendered or ""), re.I))
    unsupported = sorted(ids - allowed)
    generic_request = bool(re.search(
        r"담당자(?:를|에게|한테)?\s*(?:직접\s*)?멘션|담당\s*(?:자를\s*)?멘션",
        prompt or ""))
    # One generic placeholder can safely mean the already verified primary assignee.
    # Multiple actors or a named/id-specific request cannot be collapsed to one person.
    can_replace = (len(ids) == 1 and len(unsupported) == 1 and asks_assignee
                   and generic_request and bool(primary))
    return [] if can_replace else unsupported


def _ground_editor_person_mentions(rendered: str, prompt: str, source: str) -> str:
    """Keep only verified people from editor context and use the primary assignee when asked.

    Existence is insufficient: a model once selected a real but unrelated user for a status
    comment.  The ticket context already carries canonical ids, so enforce that boundary after
    badge rendering.  When the user explicitly requests an assignee mention, an unsupported id
    is replaced with the ticket's verified primary assignee; otherwise its whole prose block is
    removed rather than leaving a broken ``담당자 께서는`` fragment.
    """
    out = str(rendered or "")
    allowed, primary, asks_assignee = _editor_person_boundary(prompt, source)
    pattern = (r'<span\b[^>]*data-type="mention"[^>]*data-id="([^"]+)"[^>]*>'
               r'.*?</span>')

    unsupported = {m.group(1) for m in re.finditer(pattern, out, re.I | re.S)
                   if m.group(1) not in allowed}
    if not unsupported:
        return out
    if asks_assignee and primary:
        for uid in unsupported:
            out = re.sub(
                pattern.replace('([^\"]+)', re.escape(uid)),
                f'<span data-type="mention" data-id="{primary}">@{primary}</span>',
                out, flags=re.I | re.S,
            )
        return out

    def drop_block(match):
        block = match.group(0)
        return "" if any(f'data-id="{uid}"' in block for uid in unsupported) else block

    out = re.sub(r'<(?:p|li)\b[^>]*>.*?</(?:p|li)>', drop_block, out,
                 flags=re.I | re.S)
    return out


def _normalize_unfinished_checklist_labels(rendered: str, context: str) -> str:
    """An unchecked ``X 완료`` item is visually ambiguous in a status comment.

    Descriptions use that wording as a future DoD, but comments describe the present.  When
    deterministic context marks X as unfinished, render the unchecked item as ``X 진행 필요``.
    """
    marker = re.search(r"명시적 미완료\(완료로 쓰지 말 것\):\s*([^\n]+)", context or "")
    if not marker:
        return str(rendered or "")
    topics = [part.strip() for part in marker.group(1).split("|") if part.strip()]
    out = str(rendered or "")
    for topic in topics:
        escaped = re.escape(topic)
        out = re.sub(
            rf'(<li\b[^>]*data-checked="false"[^>]*>\s*)({escaped})\s*완료(?=\s*</li>)',
            rf'\1\2 진행 필요', out, flags=re.I,
        )
    return out


def _drop_unrequested_description_quality_claims(rendered: str, source: str) -> str:
    """본문 source에 없는 일반적 품질 효익을 배경·DoD에서 제거한다.

    `정확하고 신뢰할 수 있는 데이터`, `사용자 경험 향상`은 자연스럽지만 title·Epic·현재
    본문 어디에도 없으면 검증된 배경이 아니다. 사용자/티켓 source가 실제로 말한 차원은
    보존하고, 새로 생긴 차원만 제거한다.
    """
    from app.agent.workflow.agents.work_architect import _QUALITY_DIMENSIONS

    forbidden = [p for p in _QUALITY_DIMENSIONS if not re.search(p, source or "", re.I)]
    if not forbidden:
        return str(rendered or "")

    def unsupported(value: str) -> bool:
        plain = _plain_text(value)
        return any(re.search(p, plain, re.I) for p in forbidden)

    out = str(rendered or "")
    title_match = re.search(r'\[[A-Z][A-Z0-9]*-\d+\]\s*"([^"]+)"', source or "")
    title = re.sub(r"^\s*\[[^\]]+\]\s*", "", title_match.group(1)).strip() \
        if title_match else "요청한 작업"

    has_explicit_quality = any(re.search(p, source or "", re.I) for p in _QUALITY_DIMENSIONS)
    sparse = (not re.search(r"현재 본문|명시적 미완료|하위\s+\d", source or "")
              and not has_explicit_quality)
    bg_match = re.search(r"(<h3>\s*배경\s*</h3>\s*)(.*?)(?=<h3>|$)", out,
                         re.S | re.I)
    if bg_match and (sparse or unsupported(bg_match.group(2))
                     or not _plain_text(bg_match.group(2)).strip()):
        section = bg_match.group(2)

        def clean_paragraph(match):
            inner = match.group(1)
            pieces = re.split(r"(?<=[.!?])\s+", inner)
            kept = [p for p in pieces if p.strip() and not unsupported(p)]
            return "<p>" + " ".join(kept) + "</p>" if kept else ""

        cleaned = ("" if sparse else
                   re.sub(r"<p\b[^>]*>(.*?)</p>", clean_paragraph, section,
                          flags=re.S | re.I))
        if not _plain_text(cleaned).strip():
            cleaned = f"<p>{_html.escape(title)} 요청.</p>"
        out = out[:bg_match.start(2)] + cleaned + out[bg_match.end(2):]

    # A title-only ticket context does not justify inferred integrations, exclusions, documentation,
    # or benefit claims. Keep a usable but conservative scope/DoD until the ticket has material detail.
    if sparse:
        safe = _html.escape(title)
        out = re.sub(
            r"(<h3>\s*작업\s*범위\s*</h3>\s*)(.*?)(?=<h3>|$)",
            lambda m: m.group(1) + f"<ul><li>포함: {safe}</li></ul>",
            out, flags=re.S | re.I)
        out = re.sub(
            r"(<h3>\s*(?:완료\s*조건(?:\s*\(DoD\))?|DoD)\s*</h3>\s*)(.*?)(?=<h3>|$)",
            lambda m: (m.group(1) + '<ul data-type="taskList">'
                       f'<li data-checked="false">{safe} 결과와 테스트 기록을 티켓에서 확인</li>'
                       '</ul>'),
            out, flags=re.S | re.I)

    def clean_dod(match):
        if not unsupported(match.group(1)):
            return match.group(0)
        opening = match.group(0)[:match.group(0).find(">") + 1]
        safe = _html.escape(title)
        return opening + f"{safe} 결과와 테스트 기록을 티켓에서 확인</li>"

    return re.sub(r"<li\b[^>]*data-checked=[\"']?false[\"']?[^>]*>(.*?)</li>",
                  clean_dod, out, flags=re.S | re.I)


def _unverified_editor_ticket_keys(rendered: str, source: str) -> list[str]:
    allowed = {value.upper() for value in re.findall(
        r"\b[A-Z][A-Z0-9]{1,9}-\d+\b", source or "", re.I)}
    visible = {value.upper() for value in re.findall(
        r"\b[A-Z][A-Z0-9]{1,9}-\d+\b", _plain_text(rendered), re.I)}
    return sorted(visible - allowed)


def _drop_unverified_editor_ticket_claims(rendered: str, source: str) -> str:
    """Remove blocks that cite a ticket absent from verified editor inputs.

    A ticket may exist in Jira and still be unrelated to this editor request. Keeping only keys present
    in prompt, seed, or deterministic ticket context prevents the model from using a real incident as
    invented motivation for another task.
    """
    allowed = {x.upper() for x in re.findall(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b", source or "")}
    out = str(rendered or "")

    def clean_block(match):
        keys = {x.upper() for x in re.findall(
            r"\b[A-Z][A-Z0-9]{1,9}-\d+\b", _plain_text(match.group(0)))}
        return "" if keys - allowed else match.group(0)

    out = re.sub(r"<(?:p|li)\b[^>]*>.*?</(?:p|li)>", clean_block, out,
                 flags=re.S | re.I)
    out = re.sub(r"<(?:ul|ol)\b[^>]*>\s*</(?:ul|ol)>", "", out, flags=re.S | re.I)
    return out


def _normalize_editor_ticket_titles(rendered: str, references: list[dict]) -> str:
    """Replace quoted shorthand after a ticket badge with the resolver's canonical title."""
    out = str(rendered or "")
    for ref in references or []:
        if ref.get("kind") != "ticket" or not ref.get("resolved"):
            continue
        key = str(ref.get("key") or "").upper()
        label = str(ref.get("label") or "").strip()
        if not key or not label:
            continue
        pattern = (rf'(<a\b[^>]*data-key=["\']{re.escape(key)}["\'][^>]*>.*?</a>)'
                   r'\s*["“][^"”\n]{1,160}["”]')
        out = re.sub(pattern, lambda m: m.group(1) + " \"" + _html.escape(label) + "\"",
                     out, flags=re.S | re.I)
    return out


def _dedupe_editor_list_items(rendered: str) -> str:
    """Drop duplicate list items introduced when several unsupported claims share one safe fallback."""
    seen = set()

    def item(match):
        plain = re.sub(r"\s+", " ", _plain_text(match.group(0))).strip().lower()
        if plain and plain in seen:
            return ""
        if plain:
            seen.add(plain)
        return match.group(0)

    return re.sub(r"<li\b[^>]*>.*?</li>", item, str(rendered or ""), flags=re.S | re.I)


def _drop_parent_child_execution_repetition(rendered: str, context: str) -> str:
    """Keep a parent description at integration scope instead of copying child execution cards.

    Child titles are deterministic ticket context. When generated scope or DoD repeats those titles, replace
    them with one parent-level tracking contract. This also removes a newly invented hop exclusion when the
    verified context never states that boundary.
    """
    child_line = next((line for line in str(context or "").splitlines()
                       if line.strip().startswith("하위 ")), "")
    titles = re.findall(r'\b[A-Z][A-Z0-9]*-\d+\s+"([^"]+)"', child_line)
    if not titles:
        return str(rendered or "")

    stop = {"작업", "기능", "항목", "구현", "완료", "연동", "진행", "확인"}
    child_tokens = []
    for title in titles:
        plain = re.sub(r"^\s*\[[^]]+\]\s*", "", title)
        child_tokens.append({token.lower() for token in
                             re.findall(r"[A-Za-z0-9가-힣_.-]{2,}", plain)
                             if token.lower() not in stop})
    verified = _plain_text(context).lower()

    def child_detail(value: str) -> bool:
        plain = _plain_text(value).lower()
        tokens = {token.lower() for token in re.findall(r"[A-Za-z0-9가-힣_.-]{2,}", plain)
                  if token.lower() not in stop}
        return any(len(expected & tokens) >= min(2, len(expected))
                   for expected in child_tokens if expected)

    out = str(rendered or "")

    def clean_section(match, canonical: str) -> str:
        head, body = match.group(1), match.group(2)
        removed = False

        def clean_item(item):
            nonlocal removed
            plain = _plain_text(item.group(0)).lower()
            invented_boundary = ("제외" in plain and re.search(r"\d+\s*홉", plain)
                                 and re.sub(r"\s+", "", plain) not in
                                 re.sub(r"\s+", "", verified))
            if child_detail(item.group(0)) or invented_boundary:
                removed = True
                return ""
            return item.group(0)

        body = re.sub(r"<li\b[^>]*>.*?</li>", clean_item, body, flags=re.S | re.I)
        if removed and canonical not in _plain_text(body):
            closing = re.search(r"</(?:ul|ol)>\s*$", body, re.I)
            item = (f'<li data-checked="false">{canonical}</li>'
                    if "결과 근거" in canonical and "완료" in _plain_text(head) else
                    f"<li>{canonical}</li>")
            body = (body[:closing.start()] + item + body[closing.start():]
                    if closing else body + "<ul>" + item + "</ul>")
        return head + body

    out = re.sub(r"(<h3>\s*작업\s*범위\s*</h3>)(.*?)(?=<h3>|$)",
                 lambda m: clean_section(m, "포함: 하위 작업 통합 진행 및 결과 근거 확인"),
                 out, flags=re.S | re.I)
    out = re.sub(r"(<h3>\s*(?:완료\s*조건(?:\s*\(DoD\))?|DoD)\s*</h3>)(.*?)(?=<h3>|$)",
                 lambda m: clean_section(m, "하위 작업 상태와 결과 근거를 이 티켓에서 확인"),
                 out, flags=re.S | re.I)
    return out


def _repair_dangling_editor_ending(rendered: str) -> str:
    """마지막 connective(`검토해 주시고,`)로 잘린 editor 결과를 완결한다."""
    out = str(rendered or "").rstrip()
    out = re.sub(r"(기록|확인|정리|첨부|공유|검증)한다\s*할\s*것",
                 r"\1할 것", out)
    out = re.sub(r"(검토|확인|공유)해\s*주시고\s*,?\s*</p>\s*$",
                 r"\1 부탁드립니다.</p>", out)
    out = re.sub(r",\s*</p>\s*$", ".</p>", out)
    # A provider can stop after an attributive connective (``…하는 데 필요한``).
    # When useful content already precedes that final paragraph, dropping the fragment is
    # safer than inventing its missing object or action.
    paragraphs = list(re.finditer(r"(?s)<p\b[^>]*>.*?</p>", out))
    last = paragraphs[-1] if paragraphs and not out[paragraphs[-1].end():].strip() else None
    if last and last.start() > 0:
        plain = _plain_text(last.group(0)).strip()
        if re.search(r"(?:필요한|위한|대한|통해|있으며|하며|하고|그리고|또는|및)\s*$", plain):
            out = out[:last.start()].rstrip()
    return out


def _sharpen_editor_dod(rendered: str, context: str, prompt: str) -> str:
    """Apply the same observable-evidence DoD contract used by ticket drafts."""
    title = re.search(r'^\[[A-Z][A-Z0-9]*-\d+\]\s+"([^"]+)"',
                      str(context or ""), re.M)
    summary = title.group(1).strip() if title else "요청한 작업"
    item = {"summary": summary, "type": "Task", "description": str(rendered or "")}
    try:
        from app.agent.workflow.agents.work_architect import _sharpen_dod
        _sharpen_dod({"request_text": str(prompt or ""), "messages": []}, [item])
    except Exception:
        return str(rendered or "")
    return str(item.get("description") or rendered or "")


def _drop_unverified_editor_dates(rendered: str, source: str) -> str:
    """source에 없는 상대·절대 기한을 editor 초안에서 제거한다."""
    out = str(rendered or "")
    trusted = str(source or "")
    for match in list(re.finditer(
            r"(?:오늘|내일|모레|이번\s*주|다음\s*주|금주|차주)(?:\s*[월화수목금토일]요일)?\s*까지|"
            r"\b\d{4}-\d{2}-\d{2}\b", out, re.I)):
        phrase = match.group(0)
        if re.sub(r"\s+", "", phrase).lower() in re.sub(r"\s+", "", trusted).lower():
            continue
        out = out.replace(phrase, "")
    return re.sub(r"\s{2,}", " ", out).replace(" ,", ",").strip()


def _status_conflicts(rendered: str, context: str) -> list[str]:
    """명시적 미완료 항목을 완료로 단정한 생성문을 찾는다."""
    marker = re.search(r"명시적 미완료\(완료로 쓰지 말 것\):\s*([^\n]+)", context or "")
    if not marker:
        return []
    # DoD의 "성능 측정 완료"는 **현재 상태 주장**이 아니라 미래 완료 기준이다. 미체크
    # task item과 완료 조건 절을 검사하면 올바른 부모 본문까지 거절한다(CMP8 실측).
    claims = re.sub(
        r"<li\b[^>]*data-checked=[\"']?false[\"']?[^>]*>.*?</li>", " ",
        rendered or "", flags=re.S | re.I)
    claims = re.sub(
        r"<h([1-6])\b[^>]*>\s*(?:완료\s*조건|DoD).*?</h\1>.*?(?=<h[1-6]\b|$)",
        " ", claims, flags=re.S | re.I)
    text = re.sub(r"\s+", " ", _plain_text(claims)).strip()
    blocks = [re.sub(r"\s+", " ", _plain_text(x)).strip()
              for x in re.split(r"</(?:li|p|h[1-6])\s*>", claims, flags=re.I)]
    blocks = [x for x in blocks if x]
    done = (r"(?:완료(?:되었|됐|했|함|됨|된|하였|되었습니다|됐습니다|했습니다|하였습니다)|"
            r"완료(?=\s*(?:[.!?]|$|[-—–:·(]))|"
            r"끝났(?:습니다)?|마쳤(?:습니다)?)")
    bad = []
    for raw in marker.group(1).split("|"):
        topic = raw.strip()
        if not topic:
            continue
        pattern = (re.escape(topic)
                   + r"(?:\s*(?:작업|기능|항목|건|상태))?"
                     r"(?:은|는|이|가|을|를)?\s*(?:이미\s*)?" + done)
        sentence_conflict = any(
            _topic_matches(topic, sentence) and re.search(done, sentence)
            for block in blocks
            for sentence in re.split(r"[.!?]\s*|\n+", block)
            if sentence.strip())
        if re.search(pattern, text) or sentence_conflict:
            bad.append(topic)
    return bad


def _qualify_non_done_ticket_claims(rendered: str, context: str) -> str:
    """Replace a completion claim tied to an exact non-done child badge.

    Title-based topic matching cannot connect `ABC-123 연동 완료` to a child whose title is
    `다운스트림 조회 연동`.  The deterministic child row carries the exact key and Jira
    status, so only the short completion clause immediately following that canonical badge
    is qualified; unrelated completed siblings and the rest of the sentence are preserved.
    """
    children = re.findall(
        r'\b([A-Z][A-Z0-9]{1,9}-\d+)\s+"[^"]+"\(미완료:\s*([^)]+)\)',
        str(context or ""), re.I)
    out = str(rendered or "")
    for raw_key, raw_status in children:
        key = raw_key.upper()
        status = _html.escape(raw_status.strip() or "상태 미상")
        anchor = (rf'(<a\b[^>]*data-key=["\']{re.escape(key)}["\'][^>]*>'
                  rf'.*?</a>)')
        claim = re.compile(
            anchor
            + r'(?P<subject>[^<\n.!?]{0,80}?)'
              r'(?:완료(?:되었습니다|됐습니다|했습니다|하였습니다|되었|됐|했|함|됨|된|하였)?)'
              r'(?!\s*(?:조건|기준|여부|시점|계획|예정|목표|를?\s*위해))'
              r'(?P<linker>\s*에\s*따른|\s*에\s*따라|\s*로\s*인해|\s*하여|\s*해서)?',
            re.S | re.I)

        def qualify(match):
            subject = re.sub(r"(?:은|는|이|가|을|를)?\s*$", "",
                             match.group("subject") or "").strip()
            subject_text = (" " + _html.escape(subject) if subject else " 해당 작업")
            bridge = ". 상태 확인 후" if (match.group("linker") or "").strip() else ""
            return (match.group(1) + subject_text + f"은 Jira 상태 {status}로 "
                    f"최종 상태 확인 필요{bridge}")

        out = claim.sub(qualify, out)
    return out


def _ticket_status_ledger(context: str) -> dict[str, str]:
    """Read only exact key-bound status fields from deterministic editor context."""
    out: dict[str, str] = {}
    marker = re.search(r"티켓별 현재 상태:\s*([^\n]+)", str(context or ""))
    if marker:
        for chunk in marker.group(1).split("|"):
            match = re.fullmatch(
                r"\s*([A-Z][A-Z0-9]*-\d+)\s*=\s*(.+?)\s*", chunk, re.I,
            )
            if match:
                out[match.group(1).upper()] = match.group(2).strip()
    # Backward-compatible deterministic context used by older callers and focused tests.
    for match in re.finditer(
            r'\b([A-Z][A-Z0-9]*-\d+)\s+"[^"]*"\('
            r'(?:완료(?::\s*([^)]+))?|미완료:\s*([^)]+))\)',
            str(context or ""), re.I):
        key = match.group(1).upper()
        out.setdefault(key, (match.group(2) or match.group(3) or "완료").strip())
    return out


def _bind_ticket_status_claims(rendered: str, context: str) -> str:
    """Give every ticket in a multi-ticket status sentence its own exact field value."""
    ledger = _ticket_status_ledger(context)
    if not ledger:
        return str(rendered or "")
    out = str(rendered or "")
    block_re = re.compile(r"<(p|li)\b[^>]*>.*?</\1>", re.S | re.I)
    anchor_re = re.compile(
        r'(<a\b[^>]*data-key=["\']([A-Z][A-Z0-9]*-\d+)["\'][^>]*>.*?</a>)',
        re.S | re.I,
    )

    def bind_block(match: re.Match) -> str:
        block = match.group(0)
        anchors = [item for item in anchor_re.finditer(block)
                   if item.group(2).upper() in ledger]
        keys = list(dict.fromkeys(item.group(2).upper() for item in anchors))
        if len(keys) < 2 or not re.search(
                r"(?:Jira\s*)?(?:상태|status)\b", _plain_text(block), re.I):
            return block
        values = {ledger[key].casefold() for key in keys}
        if len(values) == 1 and next(iter(values)) in _plain_text(block).casefold():
            return block
        # Remove only the bounded shared status assertion; keep mentions, rationale, and
        # following sentences in the same HTML block. Exact status is then attached to each
        # ticket anchor independently.
        block = re.sub(
            r"(?:은|는|이|가)?\s*(?:Jira\s*)?(?:상태|status)\s+[^<.!?]{1,100}"
            r"(?=[.!?]|<)", "", block, count=1, flags=re.I,
        )

        def annotate(anchor: re.Match) -> str:
            key = anchor.group(2).upper()
            return anchor.group(1) + f" · Jira 상태 {_html.escape(ledger[key])}"

        return anchor_re.sub(annotate, block)

    return block_re.sub(bind_block, out)


def _topic_matches(topic: str, sentence: str) -> bool:
    """`다운스트림 조회 연동`과 `다운스트림 2홉 조회` 같은 안전한 축약을 맞춘다."""
    topic_plain = re.sub(r"^\s*\[[^\]]+\]\s*", "", str(topic or ""))
    topic_plain = re.sub(r"\bh[1-6]\b", " ", topic_plain, flags=re.I)
    tokens = [x.lower() for x in re.findall(r"[A-Za-z0-9가-힣_.-]{2,}", topic_plain)
              if x.lower() not in {"작업", "기능", "항목", "상태", "연동", "구현", "작성"}]
    target = str(sentence or "").lower()
    return bool(tokens) and all(token in target for token in tokens)


def _ground_acceptance_metrics(rendered: str, source: str) -> str:
    """자료에 없는 정량 목표를 만들지 않고, 누락된 기준을 구체적인 확인 과제로 남긴다."""
    out = str(rendered or "")
    source_plain = _plain_text(source)
    for metric in set(re.findall(r"\b\d+(?:\.\d+)?\s*%", _plain_text(out))):
        if metric.replace(" ", "") in source_plain.replace(" ", ""):
            continue
        out = re.sub(re.escape(metric) + r"\s*이상\s*개선",
                     "담당팀과 합의한 목표값을 충족", out)
        out = re.sub(re.escape(metric) + r"\s*(?:이하|이상)",
                     "담당팀과 합의한 목표값", out)
    out = re.sub(
        r"성능\s*측정\s*결과가\s*(?:요구사항|기준|기대치)을?\s*충족(?:함|됨|한다)?",
        "성능 측정 지표와 목표값은 담당팀 확인 필요 — 확정 후 측정값과 판정 결과를 기록한다",
        out)
    return out


def _qualify_status_conflicts(rendered: str, topics: list[str]) -> str:
    """완료 보고와 Jira 상태가 충돌하면 완료 단정을 상태 확인 문장으로 바꾼다."""
    out = str(rendered or "")
    for topic in topics:
        safe = _html.escape(topic)
        replacement = (f"{safe} 항목은 자료상 아직 남음 · Jira 상태 In Progress · "
                       "현재 진행 상황 확인 필요")
        for tag in ("li", "p"):
            pattern = rf"<{tag}\b[^>]*>.*?</{tag}>"

            def qualify(match):
                plain = _plain_text(match.group(0))
                if (not _topic_matches(topic, plain)
                        or not re.search(r"완료|마쳤|끝났", plain)):
                    return match.group(0)
                return f"<{tag}>{replacement}</{tag}>"

            out = re.sub(pattern, qualify, out, flags=re.S | re.I)
            # 같은 완료 오판이 목록과 이어지는 설명 paragraph에 동시에 나올 수 있다.
            # 첫 tag 종류에서 멈추면 하나를 고치고 다른 하나가 남아 compose 전체가 실패한다.
            # 두 종류를 모두 훑되, 무관한 tag는 qualify()가 그대로 보존한다.
        # Compatible models sometimes return Markdown/plain text despite the HTML contract.
        # The safety pass used to inspect only <p>/<li>, so the detector correctly rejected
        # CMP1/CMP5 but could never repair them. Rewrite only a line that both names this
        # explicit remaining topic and claims completion; preserve every unrelated line.
        lines = []
        for line in out.splitlines():
            plain = _plain_text(line)
            if _topic_matches(topic, plain) and re.search(r"완료|마쳤|끝났", plain):
                lines.append(f"<p>{replacement}</p>")
            else:
                lines.append(line)
        out = "\n".join(lines)
    return out


def _source_ticket_aliases(rendered: str, source: str) -> dict[str, str]:
    """Resolve a one-character project-prefix copy error only with exact title evidence.

    A numeric issue suffix is not globally unique and is never enough.  The verified source
    must contain exactly one key with that suffix, the copied prefix must be exactly one
    character shorter, and the quoted title beside the generated token must equal the
    canonical quoted source title.
    """
    row = re.compile(
        r"\b([A-Z][A-Z0-9]{0,9})-(\d+)\b[^\n\"“]{0,100}[\"“]([^\"”\n]{2,180})[\"”]",
        re.I)
    source_by_number: dict[str, dict[str, tuple[str, str, str]]] = {}
    for match in row.finditer(_plain_text(source)):
        prefix, number, title = match.group(1).upper(), match.group(2), match.group(3)
        key = f"{prefix}-{number}"
        source_by_number.setdefault(number, {})[key] = (
            key, prefix, re.sub(r"\s+", " ", title).strip().casefold())

    aliases: dict[str, str] = {}
    for match in row.finditer(_plain_text(rendered)):
        prefix, number, title = match.group(1).upper(), match.group(2), match.group(3)
        raw_key = f"{prefix}-{number}"
        candidates = list((source_by_number.get(number) or {}).values())
        if len(candidates) != 1 or candidates[0][0] == raw_key:
            continue
        key, canonical_prefix, canonical_title = candidates[0]
        copied_title = re.sub(r"\s+", " ", title).strip().casefold()
        prefix_supported = (len(canonical_prefix) == len(prefix) + 1
                            and (canonical_prefix.startswith(prefix)
                                 or canonical_prefix.endswith(prefix)))
        if prefix_supported and canonical_title and copied_title == canonical_title:
            aliases[raw_key] = key
    return aliases


def _badgeify(html: str, ticket_aliases: dict[str, str] | None = None) -> str:
    """평문 언급 → 에디터 뱃지 마크업.

    · 티켓 키(태그 밖 텍스트의 `DL-123`) → `<a href=".../browse/DL-123">DL-123</a>`
      — CommentEditor 의 linkBadge 가 `a[href]` 를 파싱해 타입·제목·상태 뱃지로 그린다.
    · `[~사번]` → `<span data-type="mention" data-id="사번">@사번</span>`
      — Mention 확장이 같은 모양으로 저장·렌더한다(프로필 칩).
    이미 앵커 안에 있는 키는 건드리지 않는다(뱃지 안에 뱃지 방지).
    """
    import html as _html
    from html.parser import HTMLParser
    from app.agent.tools._ctx import jira_key_allowed

    aliases = {str(key).upper(): str(value).upper()
               for key, value in (ticket_aliases or {}).items()}

    class BadgeParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.out, self.stack = [], []

        def handle_starttag(self, tag, attrs):
            self.stack.append(tag.lower())
            if tag.lower() == "a":
                pairs = [(str(k), str(v or "")) for k, v in attrs]
                href = next((v for k, v in pairs if k.lower() == "href"), "")
                hit = (re.fullmatch(r"([A-Z][A-Z0-9]{0,9}-\d+)", href.strip(), re.I)
                       or re.search(r"/browse/([A-Z][A-Z0-9]{0,9}-\d+)(?:$|[?#])",
                                    href.strip(), re.I))
                raw_key = hit.group(1).upper() if hit else ""
                key = aliases.get(raw_key, raw_key)
                if key and jira_key_allowed(key):
                    # 모델이 ``href="DL-1"``이나 절대 Jira URL을 내도 editor가 이해하는
                    # canonical ticket badge anchor 하나로 정규화한다.
                    attrs = [("class", "jira-badge tkt"), ("data-key", key),
                             ("href", f"/browse/{key}")]
            rendered = "".join(
                f' {_html.escape(str(k), quote=True)}="{_html.escape(str(v or ""), quote=True)}"'
                for k, v in attrs)
            self.out.append(f"<{tag}{rendered}>")

        def handle_startendtag(self, tag, attrs):
            rendered = "".join(
                f' {_html.escape(str(k), quote=True)}="{_html.escape(str(v or ""), quote=True)}"'
                for k, v in attrs)
            self.out.append(f"<{tag}{rendered}/>")

        def handle_endtag(self, tag):
            self.out.append(f"</{tag}>")
            if tag.lower() in self.stack:
                idx = len(self.stack) - 1 - self.stack[::-1].index(tag.lower())
                del self.stack[idx:]

        def handle_data(self, data):
            if any(x in self.stack for x in ("a", "code", "pre")):
                self.out.append(data); return
            pattern = re.compile(
                r"(?P<url>https?://[^\s<>\"']+)|"
                r"\[~(?P<bracket_uid>[A-Za-z0-9._-]+)\]|"
                r"(?<![\w@])@(?P<at_uid>[A-Za-z0-9][A-Za-z0-9._-]{1,63})\b|"
                r"\b(?P<key>[A-Z][A-Z0-9]{0,9}-\d+)\b",
                re.I)
            pos = 0
            for match in pattern.finditer(data):
                self.out.append(data[pos:match.start()])
                url = match.group("url") or ""
                uid = match.group("bracket_uid") or match.group("at_uid") or ""
                raw_key = (match.group("key") or "").upper()
                key = aliases.get(raw_key, raw_key)
                if uid:
                    self.out.append(f'<span data-type="mention" data-id="{_html.escape(uid)}">'
                                    f'@{_html.escape(uid)}</span>')
                elif url:
                    clean = url.rstrip(".,;:!?)]}")
                    suffix = url[len(clean):]
                    safe = _html.escape(clean, quote=True)
                    self.out.append(f'<a href="{safe}">{_html.escape(clean)}</a>{suffix}')
                elif jira_key_allowed(key):
                    safe = _html.escape(key, quote=True)
                    self.out.append(f'<a class="jira-badge tkt" data-key="{safe}" '
                                    f'href="/browse/{safe}">{_html.escape(key)}</a>')
                else:
                    self.out.append(raw_key)
                pos = match.end()
            self.out.append(data[pos:])

        def handle_entityref(self, name): self.out.append(f"&{name};")
        def handle_charref(self, name): self.out.append(f"&#{name};")
        def handle_comment(self, data): self.out.append(f"<!--{data}-->")

    parser = BadgeParser()
    try:
        parser.feed(str(html or "")); parser.close()
        return "".join(parser.out)
    except Exception:
        return str(html or "")


def _legacy_reference_tokens(value: str) -> str:
    """새 reference placeholder를 legacy Composer 표기로 안전하게 내린다.

    Composer의 현재 출력 schema는 HTML string 하나라 별도 ``references[]``를 받을 수 없다.
    ticket key와 uid처럼 자체 식별 가능한 값만 변환하고, 알 수 없는 id는 wrapper를 벗긴
    평문으로 남겨 malformed HTML을 만들지 않는다. 실제 resolve 결과는 compose()가 아래에서
    canonical reference bundle로 다시 제공한다.
    """
    text = str(value or "")
    text = re.sub(r"\{\{\s*ref\s*:\s*([A-Z][A-Z0-9]{1,9}-\d+)\s*\}\}",
                  lambda m: m.group(1).upper(), text, flags=re.I)
    text = re.sub(r"\{\{\s*mention\s*:\s*([A-Za-z0-9._-]+)\s*\}\}",
                  lambda m: f"[~{m.group(1)}]", text, flags=re.I)
    text = re.sub(r"\{\{\s*(?:ref|mention)\s*:\s*([^{}<>]{1,120})\s*\}\}",
                  lambda m: m.group(1).strip(), text, flags=re.I)
    # 일부 모델은 예시 placeholder를 한 번 더 escape해 ``{{{{ref:DL-1}}}}``로 낸다.
    # 안쪽을 내린 뒤 남는 ``{{DL-1}}``도 plain key로 정규화한다.
    text = re.sub(r"(?:\{\{\s*)+([A-Z][A-Z0-9]{1,9}-\d+)(?:\s*\}\})+",
                  lambda m: m.group(1).upper(), text, flags=re.I)
    # Markdown link가 아닌 단순 key 장식 ``[DL-1]``은 badge 바깥에 대괄호가 남지 않게 한다.
    return re.sub(r"\[([A-Z][A-Z0-9]{1,9}-\d+)\]", lambda m: m.group(1).upper(), text)


def _reference_candidates(rendered: str) -> list[dict]:
    """생성 HTML에서 typed reference 후보를 추출한다. label/url 검증은 resolver가 한다."""
    from app.agent.retrieval.harvest import _conf_id

    refs, seen = [], set()
    for key in re.findall(r"\b([A-Z][A-Z0-9]{1,9}-\d+)\b", rendered or ""):
        rid = "ticket:" + key
        if rid not in seen:
            seen.add(rid); refs.append({"id": rid, "kind": "ticket", "key": key})
    for uid in re.findall(r'data-(?:id|uid)=["\']([A-Za-z0-9._-]+)["\']',
                          rendered or "", re.I):
        rid = "person:" + uid
        if rid not in seen:
            seen.add(rid); refs.append({"id": rid, "kind": "person", "user_id": uid})
    for url in re.findall(r'href=["\'](https?://[^"\']+)["\']', rendered or "", re.I):
        url = _html.unescape(url)
        rid = "url:" + str(len(refs))
        if url not in seen:
            seen.add(url)
            page_id = _conf_id(url)
            # A verified editor source can carry the public/corporate Confluence host while
            # the local provider uses an in-process base URL.  Page identity and allowed
            # space are validated by `_document`; host equality is not identity evidence.
            if page_id:
                refs.append({"id": rid, "kind": "document", "page_id": page_id, "url": url})
            else:
                refs.append({"id": rid, "kind": "external", "url": url})
    return refs


def _unfence(text: str) -> str:
    """```html … ``` 로 감싸 오는 모델이 있다 — 그대로 꽂으면 에디터에 백틱이 남는다."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()

"""agent/compose.py — 에디터 안에서 본문·코멘트를 써 준다.

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

MAX_SEED = 6000        # 사용자가 쓰던 글. 이보다 길면 앞부분만 — 시드는 의도 파악용이다
MAX_CONTEXT = 5000     # 티켓 맥락. 프롬프트가 커지면 응답이 느려지고 커서는 계속 멈춰 있다


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
        parts.append(f'[{r["key"]}] "{r.get("title", "")}" — {r.get("status")}'
                     f' · 담당 {r.get("assignee") or "없음"}'
                     f' · 마감 {r.get("due") or "없음"}')
        if r.get("children"):
            parts.append(f'하위 {r.get("children_done")} 완료: ' + ", ".join(
                f'{c["key"]} "{c.get("title", "")}"{"(완료)" if c.get("done") else ""}'
                for c in r["children"][:6]))
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
    except Exception:
        pass
    return "\n\n".join(parts)[:MAX_CONTEXT]


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


def compose(ticket_key: str = "", kind: str = "comment", prompt: str = "",
            seed_html: str = "", user_id: str = "") -> dict:
    """에디터에 꽂을 HTML 을 만든다. 돌려주는 것: {ok, html, note}."""
    from app.agent import config as C
    from app.agent.prompts.roles import SYSTEM_COMPOSER
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

    ctx = _ticket_context(ticket_key, kind)
    rules = _house_rules(kind, prompt)
    what = {"description": "티켓 **본문**", "comment": "**코멘트**",
            "transition": "상태 전이와 함께 남길 **말**"}.get(kind, "**코멘트**")

    task = f"""\
# 명령서
{what}을 작성하라. 결과는 사용자의 에디터에 그대로 삽입된다 — HTML 본문만 내고,
인사말·설명·따옴표·코드펜스를 붙이지 마라.

## 사용자의 요청
{prompt or "(따로 말한 것 없음 — 아래 작성 중인 글을 완성하라)"}
{wrap_data(
    data_block("작성 중인 글 (사용자의 초안 — 말투와 의도를 살려 이어 쓴다)", seed),
    data_block("이 에디터가 붙어 있는 티켓의 맥락 (여기 없는 사실은 쓰지 마라)", ctx),
    data_block("사내 작성 규율", rules))}"""

    try:
        state = {"user_id": user_id or "", "user_identity": ""}
        msg = C.get_llm(temperature=0.3).invoke(
            [("system", persona(state, SYSTEM_COMPOSER)), ("user", task)])
        html = str(getattr(msg, "content", msg) or "").strip()
    except Exception as e:
        from app.agent.workflow.session import _friendly_error
        return {"ok": False, "error": _friendly_error(e)}

    html = _unfence(html)
    if not html:
        return {"ok": False, "error": "생성된 내용이 비어 있습니다. 요청을 조금 더 구체적으로 적어 주세요."}
    # 언급은 전부 **뱃지**여야 한다(사용자 지시: plain text 금지). 모델이 평문으로 남긴
    # 티켓 키·[~사번] 을 에디터가 뱃지로 파싱하는 마크업으로 바꾼다 — 보장은 코드가 한다.
    html = _badgeify(html)

    # 접지 — 챗과 **같은 검사**를 태운다. 에디터에 꽂히는 글이라고 날조를 봐줄 이유가 없다.
    note = ""
    try:
        from app.agent.workflow import grounding
        bad = grounding.check(html)
        if not bad.get("ok"):
            items = ((bad.get("fake_keys") or []) + list((bad.get("wrong_titles") or {}))
                     + (bad.get("fake_people") or []))
            if items:
                note = ("확인되지 않은 항목이 있습니다 — 삽입 전에 확인하세요: "
                        + ", ".join(str(x) for x in items[:5]))
    except Exception:
        pass
    return {"ok": True, "html": html, "note": note}


def _badgeify(html: str) -> str:
    """평문 언급 → 에디터 뱃지 마크업.

    · 티켓 키(태그 밖 텍스트의 `DL-123`) → `<a href=".../browse/DL-123">DL-123</a>`
      — CommentEditor 의 linkBadge 가 `a[href]` 를 파싱해 타입·제목·상태 뱃지로 그린다.
    · `[~사번]` → `<span data-type="mention" data-id="사번">@사번</span>`
      — Mention 확장이 같은 모양으로 저장·렌더한다(프로필 칩).
    이미 앵커 안에 있는 키는 건드리지 않는다(뱃지 안에 뱃지 방지).
    """
    import re

    # 표(테이블) 안은 제목·키 나열 자리라 그대로 둔다 — 유일한 예외(사용자 지시).
    parts = re.split(r"(<table\b.*?</table>)", html, flags=re.S | re.I)

    def _keys(seg: str) -> str:
        # 태그 밖 텍스트 노드만 — `>`/`<` 사이 조각 단위로 치환한다.
        out = []
        for tok in re.split(r"(<[^>]+>)", seg):
            if tok.startswith("<"):
                out.append(tok)
                continue
            tok = re.sub(r"\[~([A-Za-z0-9._-]+)\]",
                         r'<span data-type="mention" data-id="\1">@\1</span>', tok)
            tok = re.sub(r"\b([A-Z][A-Z0-9]{1,9}-\d+)\b",
                         r'<a href="/browse/\1">\1</a>', tok)
            out.append(tok)
        joined = "".join(out)
        # <a href="...">DL-123</a> 처럼 이미 링크였던 것 안에 또 앵커가 생기는 것을 방지 —
        # 위 분할은 앵커 **텍스트**도 텍스트 노드로 보므로, 중첩 앵커만 사후에 푼다.
        return re.sub(r'(<a\b[^>]*>)((?:(?!</a>).)*?)<a\b[^>]*>([^<]*)</a>',
                      r"\1\2\3", joined, flags=re.S)

    return "".join(seg if i % 2 else _keys(seg) for i, seg in enumerate(parts))


def _unfence(text: str) -> str:
    """```html … ``` 로 감싸 오는 모델이 있다 — 그대로 꽂으면 에디터에 백틱이 남는다."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()

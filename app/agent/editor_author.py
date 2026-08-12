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
        parts.append(f'[{r["key"]}] "{r.get("title", "")}" — {r.get("status")}'
                     f' · 담당 {r.get("assignee") or "없음"}'
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
                          "대상을 한 줄만 적어 주세요 (예: 'CDC 파이프라인 개선 작업 본문')")}

    ctx = _ticket_context(ticket_key, kind)
    rules = _house_rules(kind, prompt)
    what = {"description": "티켓 **본문**", "comment": "**코멘트**",
            "transition": "상태 전이와 함께 남길 **말**"}.get(kind, "**코멘트**")

    task = f"""\
# 명령서
{what}을 작성하라. 결과는 사용자의 에디터에 그대로 삽입된다 — HTML 본문만 내고,
인사말·설명·따옴표·코드펜스를 붙이지 마라.

★ **쓸 수 없으면 쓰지 마라 — 단, 쓸 수 있으면 반드시 써라.** 판정은 글의 종류에 따라 다르다:
- **코멘트는 대화다** — 티켓 맥락(최근 코멘트·상태)이나 시드가 없으면 이어 말할 문맥이
  없어 쓸 수 없다. 그때와, 요청이 이 티켓과 무관한 주제일 때만 **첫 줄에 `NEED_INFO:`**
  와 무엇을 알려 주면 되는지 1~2문장(예: `NEED_INFO: 어떤 작업에 대한 코멘트인지 — 목적을
  한 줄만 적어 주세요`).
- **본문은 문서다** — 제목·상위 Epic·자식 Sub-Task·관련 티켓만으로도 무엇을/어떻게 쓸지
  대부분 정해진다. 티켓 맥락이 있으면 프롬프트가 짧아도 NEED_INFO 없이 쓴다.
- 티켓 맥락이 있으면 세부 수치·결과가 없어도 쓴다 — 검토 요청·확인 요청·진행 질문
  코멘트는 결과를 몰라도 쓸 수 있는 글이다. 모르는 세부는 비워 두거나 일반적 표현으로.
- 사용자가 담당자를 멘션해 검토를 요청하면, 자료 첫 줄의 `담당 사번`을 `[~사번]`으로 쓰고
  확인할 대상과 요청을 적는다. 검토 결과가 아직 없다는 이유로 NEED_INFO를 내지 않는다.
- **사실의 주어와 상태를 바꾸지 마라.** 연결 티켓·관련 문서의 수치가 현재 티켓의 결과라는
  뜻은 아니다. 자료의 `명시적 미완료` 항목과 `남은/예정/진행 중`인 일은 완료로 쓰지 않는다.
- 자료끼리 상태가 다르면 하나를 골라 단정하지 말고, `구현 완료 보고가 있으나 Jira 상태는
  In Progress이므로 최종 상태 확인 필요`처럼 **충돌과 확인할 항목을 함께 쓴다**.
- 사용자 요청·작성 중인 글·티켓 제목·현재 본문에 없는 기능, UI 변경, 성능 목표값을 작업
  범위나 DoD에 추가하지 않는다. 기준이 없으면 구체적인 `담당팀 확인 필요`로 남긴다.
- **자식 Sub-Task 가 있거나(자료의 '하위' 목록) 시드·프롬프트에 분할 계획이 보이면**,
  본문은 '무엇을 왜'(전체 범위·전체 DoD)를 맡는다 — 실행 세부는 자식의 몫이니 자식
  제목을 본문에 반복하지 말고, 범위 항목이 자식들과 정합하게 쓴다(knowledge/07 역할표).

## 사용자의 요청
{prompt or "(따로 말한 것 없음 — 아래 작성 중인 글을 완성하라)"}
{wrap_data(
    data_block("작성 중인 글 (사용자의 초안 — 말투와 의도를 살려 이어 쓴다)", seed),
    data_block("이 에디터가 붙어 있는 티켓의 맥락 (여기 없는 사실은 쓰지 마라)", ctx),
    data_block("사내 작성 규율", rules))}"""

    llm_usage = {}
    try:
        from app.agent.usage import Meter, callback
        meter = Meter()
        handler = callback(meter)
        state = {"user_id": user_id or "", "user_identity": ""}
        invoke_config = {"callbacks": [handler]} if handler else {}
        llm = C.get_llm(temperature=0.3)
        messages = [("system", persona(state, SYSTEM_EDITOR_AUTHOR)), ("user", task)]
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
        return {"ok": False, "error": _friendly_error(str(e))}

    html = _unfence(html)
    # ── 피드백 루프: 모호해서 못 쓴다는 신호 — 일반론을 지어내는 것보다 낫다(사용자 요청).
    #    UI 는 팝업을 유지한 채 이 문구를 보여 주고 프롬프트·시드 보완을 유도한다.
    ask = _need_info(html)
    if ask:
        return {"ok": False, "needsInfo": True,
                "error": "이대로는 정확한 글을 쓸 수 없습니다 — " + ask,
                "usage": llm_usage}
    if not html:
        return {"ok": False, "error": "생성된 내용이 비어 있습니다. 요청을 조금 더 구체적으로 적어 주세요."}
    # 언급은 전부 **뱃지**여야 한다(사용자 지시: plain text 금지). 모델이 평문으로 남긴
    # 티켓 키·[~사번] 을 에디터가 뱃지로 파싱하는 마크업으로 바꾼다 — 보장은 코드가 한다.
    # Composer는 아직 plain HTML을 반환하는 legacy adapter다. 새 role contract의
    # ``{{ref:id}}``/``{{mention:id}}``를 모델이 흉내 내더라도 그대로 badgeify하면
    # ``{{ref:<a ...>DL-1</a>}}``처럼 placeholder 안에 anchor가 생긴다. 이 경로에서는
    # 확인 가능한 key/uid를 legacy 표기로 먼저 내린 뒤 기존 parser가 뱃지화한다.
    html = _legacy_reference_tokens(html)
    html = _badgeify(html)
    html = _ground_acceptance_metrics(html, "\n".join((prompt, seed, ctx)))

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
    # UI가 ticket/person/document를 다시 regex 추측하지 않도록 canonical reference bundle을
    # 함께 보낸다. 해결 실패는 malformed anchor를 만들지 않고 note로 노출한다.
    references = []
    try:
        from app.agent.references import resolve_references
        resolved = resolve_references(_reference_candidates(html))
        references = resolved.get("references") or []
        if resolved.get("unresolved"):
            message = ", ".join(str(x.get("id") or "") for x in resolved["unresolved"][:5])
            note = (note + " " if note else "") + f"확인되지 않은 참조: {message}"
    except Exception:
        pass
    return {"ok": True, "html": html, "note": note, "references": references,
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
    done = (r"(?:완료(?:되었|됐|했|함|됨|된|하였|되었습니다|됐습니다|했습니다|하였습니다)|"
            r"완료(?=\s*(?:[.!?]|$))|"
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
            topic in sentence and re.search(done, sentence)
            for sentence in re.split(r"[.!?]\s*|\n+", text)
            if sentence.strip())
        if re.search(pattern, text) or sentence_conflict:
            bad.append(topic)
    return bad


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
        replaced = False
        for tag in ("li", "p"):
            pattern = rf"<{tag}\b[^>]*>.*?</{tag}>"

            def qualify(match):
                nonlocal replaced
                plain = _plain_text(match.group(0))
                if (topic not in plain
                        or not re.search(r"완료|마쳤|끝났", plain)):
                    return match.group(0)
                replaced = True
                return (f"<{tag}>{safe} 항목은 구현 완료 보고가 있으나 Jira 상태가 "
                        f"In Progress이므로 최종 완료 여부는 확인 필요합니다.</{tag}>")

            out = re.sub(pattern, qualify, out, flags=re.S | re.I)
            if replaced:
                break
    return out


def _badgeify(html: str) -> str:
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

    class BadgeParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.out, self.stack = [], []

        def handle_starttag(self, tag, attrs):
            self.stack.append(tag.lower())
            if tag.lower() == "a":
                pairs = [(str(k), str(v or "")) for k, v in attrs]
                href = next((v for k, v in pairs if k.lower() == "href"), "")
                hit = (re.fullmatch(r"([A-Z][A-Z0-9]{1,9}-\d+)", href.strip(), re.I)
                       or re.search(r"/browse/([A-Z][A-Z0-9]{1,9}-\d+)(?:$|[?#])",
                                    href.strip(), re.I))
                key = hit.group(1).upper() if hit else ""
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
            if any(x in self.stack for x in ("a", "table", "code", "pre")):
                self.out.append(data); return
            pattern = re.compile(r"\[~([A-Za-z0-9._-]+)\]|\b([A-Z][A-Z0-9]{1,9}-\d+)\b")
            pos = 0
            for match in pattern.finditer(data):
                self.out.append(data[pos:match.start()])
                uid, key = match.group(1), match.group(2)
                if uid:
                    self.out.append(f'<span data-type="mention" data-id="{_html.escape(uid)}">'
                                    f'@{_html.escape(uid)}</span>')
                elif jira_key_allowed(key):
                    self.out.append(f'<a class="jira-badge tkt" data-key="{key}" '
                                    f'href="/browse/{key}">{key}</a>')
                else:
                    self.out.append(key)
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
    from urllib.parse import urlsplit
    from app.agent.retrieval.harvest import _conf_id
    from app.agent.tools._ctx import settings

    refs, seen = [], set()
    for key in re.findall(r"\b([A-Z][A-Z0-9]{1,9}-\d+)\b", rendered or ""):
        rid = "ticket:" + key
        if rid not in seen:
            seen.add(rid); refs.append({"id": rid, "kind": "ticket", "key": key})
    for uid in re.findall(r'data-id=["\']([A-Za-z0-9._-]+)["\']', rendered or ""):
        rid = "person:" + uid
        if rid not in seen:
            seen.add(rid); refs.append({"id": rid, "kind": "person", "user_id": uid})
    for url in re.findall(r'href=["\'](https?://[^"\']+)["\']', rendered or ""):
        rid = "url:" + str(len(refs))
        if url not in seen:
            seen.add(url)
            conf_base = str(getattr(settings(), "confluence_base", "") or "")
            same_conf = bool(conf_base and urlsplit(conf_base).netloc.lower()
                             == urlsplit(url).netloc.lower())
            page_id = _conf_id(url) if same_conf else ""
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

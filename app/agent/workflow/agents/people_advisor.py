"""People Advisor — 담당자를 **근거와 함께** 제안한다. 이름만 던지면 PM 이 검증할 수 없다.

담당자 추천이 쓸모없어지는 방식은 정해져 있다: "적합해 보입니다"로 끝나는 것. 리더는 그걸
검증할 수 없으니 결국 자기가 다시 판단한다 — 그러면 에이전트가 한 일이 없다.

그래서 네 신호를 **각각 확인하고 숫자와 티켓 키로** 말하게 한다.
  ① 지금 얼마나 물려 있나   `get_team_workload`
  ② 비슷한 일을 해 봤나     ResearchAnalyst 의 근거 티켓 + `get_ticket_participants`
  ③ 그 논의에 실제로 꼈나   `get_ticket_participants` — 담당자 필드엔 없지만 코멘트엔 있는 사람
  ④ 그 모듈 사람인가        `get_module_people` / `get_person_profile`

**순위를 코드에 박지 않는다.** "진행중 건수가 가장 적은 사람"으로 정하면 난이도를 모르는
추천이 되고, "P1 이 밀려 있으면 예외" 같은 현실의 결을 담을 수 없다. 도구는 신호만 모으고
판단과 문장은 모델이 한다. 대신 **근거 없는 추천을 스키마가 막는다**(reasons 가 필수다).
"""

from __future__ import annotations

from app.agent.workflow.agents.base import StructuredAgent
from app.agent.workflow.agents.work_architect import draft_text
from app.agent.prompts.roles import SYSTEM_PEOPLE_ADVISOR
from app.agent.workflow.contracts import role_output_schema, validate_role_output
from app.agent.workflow.prompts import data_block, persona, wrap_data
from app.agent.workflow.state import AgentState, Node, note

SCHEMA = role_output_schema(Node.PEOPLE_ADVISOR)


def _similar_history(state) -> str:
    """유사 업무의 **담당 이력 표**를 코드가 만든다.

    실측: 모델에게 맡기면 워크로드·모듈 소속만 확인하고 "유사 업무를 해 봤는가"는
    건너뛴다(도구 걸음을 워크로드에 다 쓴다). 검색과 집계는 판단이 아니다 — 코드가
    돌리고, 모델은 그 표를 근거로 판단만 한다.
    """
    kws = [str(k) for k in (state.get("keywords") or []) if str(k).strip()][:4]
    if not kws:
        return ""
    from app.agent.tools.search_tools import search_work_history
    r = search_work_history.invoke({"query": " ".join(kws), "limit": 12})
    from app.agent.workflow.relevance import matches_focus
    by_user: dict = {}
    for it in (r or {}).get("jira") or []:
        # 검색기는 recall을 위해 module 하나만 겹쳐도 결과를 준다. 담당 경험은 현재 요청의
        # 고유 keyword가 제목/요약에 실제로 겹칠 때만 '유사'라고 부른다.
        if not matches_focus(" ".join(str(it.get(k) or "")
                                      for k in ("title", "summary", "snippet")), kws):
            continue
        u = (it.get("assignee") or "").strip()
        if u:
            by_user.setdefault(u, []).append(it)
    rows = []
    for u, tickets in sorted(by_user.items(), key=lambda kv: -len(kv[1]))[:6]:
        refs = " · ".join(f"{t.get('key')} \"{t.get('title','')}\"({t.get('status','')})"
                          for t in tickets[:3])
        rows.append(f"- {u} — 유사 {len(tickets)}건: {refs}")
    return "\n".join(rows)


def _roster_load(state) -> str:
    """후보 로스터와 **현재 부하**를 코드가 조회한다.

    예전엔 도구(get_team_workload·get_module_people)로 두고 모델이 부르게 했다. 그런데
    부르는 대상이 늘 같았고(초안의 모듈), 도구 호출 한 번이 곧 LLM 왕복 한 번이라
    배정 하나에 4~5회를 태웠다(실측). 누구를 조회할지는 판단이 아니라 초안이 정한다.
    """
    mods = []
    for it in ((state.get("draft") or {}).get("items") or []):
        for c in (it.get("components") or []):
            if str(c).strip() and str(c) not in mods:
                mods.append(str(c))
    if not mods and state.get("module"):
        mods = [str(state["module"])]
    if not mods:
        return ""
    from app.agent.tools.people_tools import get_team_workload
    rows = []
    for m in mods[:3]:
        res = get_team_workload.invoke({"module": m}) or {}
        ppl = res.get("people") or []
        if not ppl:
            continue
        # 이름표는 **도구가 판정한 것**을 쓴다. 컴포넌트가 로스터 키와 안 맞으면 도구가
        # 전원으로 넓혀 오는데, 그걸 "[<컴포넌트> 로스터·부하]" 라고 적으면 PeopleAdvisor 가
        # 남의 모듈 사람을 그 모듈 소속으로 읽고 근거 문장에 그렇게 쓴다(실측 갭).
        rows.append(f"[{res.get('module') or m} 로스터·부하]")
        rows += [f"- {p.get('id')} {p.get('name', '')} — 진행중 {p.get('inProgress', 0)}건 · "
                 f"열림 {p.get('open', 0)}건 · 최근 완료 {p.get('done28d', 0)}건" for p in ppl]
    return "\n".join(rows)


def _all_assignees_user_specified(draft: dict) -> bool:
    """Whether every item and child has a user-decided assignee or explicit unassignment."""
    items = [item for item in (draft.get("items") or []) if isinstance(item, dict)]
    if not items:
        return False
    for item in items:
        if item.get("assignee_source") not in ("user", "user_unassigned"):
            return False
        if item.get("assignee_source") == "user" and not str(item.get("assignee") or "").strip():
            return False
        for child in (item.get("children") or []):
            if not isinstance(child, dict):
                continue
            if child.get("assignee_source") not in ("user", "user_unassigned"):
                return False
            if child.get("assignee_source") == "user" \
                    and not str(child.get("assignee") or "").strip():
                return False
    return True


def _user_fixed_assignments(draft: dict) -> list[dict]:
    """Build advisor-shaped rows without re-evaluating user decisions."""
    rows = []
    for index, item in enumerate(draft.get("items") or []):
        if not isinstance(item, dict):
            continue
        children = []
        for child_index, child in enumerate(item.get("children") or []):
            if not isinstance(child, dict):
                continue
            source = str(child.get("assignee_source") or "")
            if source not in ("user", "user_unassigned"):
                continue
            explicitly_unassigned_child = source == "user_unassigned"
            children.append({
                "index": child_index,
                "user": "" if explicitly_unassigned_child else
                        str(child.get("assignee") or "").strip(),
                "why": ("사용자 지정 미할당" if explicitly_unassigned_child
                        else "사용자 지정 담당자"),
            })
        explicitly_unassigned = item.get("assignee_source") == "user_unassigned"
        rows.append({
            "index": index,
            "user": str(item.get("assignee") or "").strip(),
            "reasons": ["사용자 지정 미할당" if explicitly_unassigned else "사용자 지정 담당자"],
            "children": children,
            "alternates": [],
        })
    return rows


def _workload_only_assignments(draft: dict, roster_load: str) -> list[dict]:
    """Choose the measured least-load roster member when no experience signal exists.

    With only workload numbers available, asking a model to narrate the same ordering added
    latency and sometimes inverted its meaning (for example, calling 16 open tickets "high
    capacity"). This helper does no semantic ranking: it preserves the configured module
    roster and sorts the already measured in-progress/open counts.
    """
    rows = []
    for index, item in enumerate((draft or {}).get("items") or []):
        if not isinstance(item, dict):
            continue
        module = next((str(value).strip() for value in (item.get("components") or [])
                       if str(value).strip()), "")
        candidates = _module_roster(roster_load, module)
        if not candidates:
            return []
        chosen = candidates[0]
        reason = (f"{module} 로스터 · 진행중 {chosen['in_progress']}건 · "
                  f"열림 {chosen['open']}건 · 관련 이력 없음")
        children = []
        for child_index, _child in enumerate(item.get("children") or []):
            person = candidates[child_index % len(candidates)]
            children.append({
                "index": child_index, "user": person["user"],
                "why": (f"{module} 로스터 · 진행중 {person['in_progress']}건 · "
                        f"열림 {person['open']}건"),
            })
        rows.append({
            "index": index, "user": chosen["user"], "reasons": [reason],
            "children": children,
            "alternates": [{
                "user": person["user"],
                "why": (f"{module} 로스터 · 진행중 {person['in_progress']}건 · "
                        f"열림 {person['open']}건"),
            } for person in candidates[1:3]],
        })
    return rows


class PeopleAdvisor(StructuredAgent):
    """★ 도구를 쓰지 않는다 — 후보 재료(유사 이력·로스터·부하)를 코드가 미리 조회한다.

    예전엔 ToolAgent 였는데, 부르는 대상이 늘 같았다(초안이 정한 모듈의 사람들).
    누구를 조회할지가 판단이 아니면 순회할 이유가 없다 — 도구 호출 한 번이 곧 LLM
    왕복 한 번이라 배정 하나에 4~5회를 태웠다(실측 기준선).
    """

    name = Node.PEOPLE_ADVISOR

    def node(self):
        base = super().node()

        def run(state):
            # Every assignee is already a user decision.  Re-running roster history and
            # workload search cannot improve that decision and used ~99 seconds in MTG2.
            # Keep the fan-out/join shape, but return deterministic aligned rows without an
            # LLM or tool call so the approval prose still reflects the exact payload.
            if _all_assignees_user_specified(state.get("draft") or {}):
                rows = _user_fixed_assignments(state.get("draft") or {})
                return {
                    "assignments": rows,
                    "trace": note(state, self.name, f"{len(rows)}건 사용자 지정 담당 유지"),
                }
            # 두 조회는 독립 — 병렬로. prod 는 호출당 수백 ms~수 초다.
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=2) as ex:
                f_hist = ex.submit(_similar_history, state)
                f_load = ex.submit(_roster_load, state)
            try:
                hist = f_hist.result()
            except Exception:
                hist = ""            # 검색 실패가 배정 자체를 막으면 안 된다
            try:
                load = f_load.result()
            except Exception:
                load = ""
            if hist:
                state = {**state, "similar_history": hist}
            if load:
                state = {**state, "roster_load": load}
            if ((state.get("draft") or {}).get("construction") == "literal_delegated"
                    and not hist):
                rows = _workload_only_assignments(state.get("draft") or {}, load)
                if rows:
                    result = self.apply(state, {"assignments": rows, "caution": ""})
                    result["trace"] = note(
                        state, self.name,
                        f"{len(rows)}건 부하 기준 결정적 추천(관련 이력 없음)",
                    )
                    return result
            return base(state)

        return run

    def system(self, state):
        return persona(state, SYSTEM_PEOPLE_ADVISOR, role_id=self.name)

    def task(self, state):
        from app.agent.workflow.relevance import evidence_is_relevant
        ev = "\n".join(f"- {e.get('key','')} {e.get('title','')} — {e.get('why','')}"
                       for e in (state.get("evidence") or []) if evidence_is_relevant(e))
        data = wrap_data(
            data_block("Current Situation", state.get("situation")),
            data_block("Relevant Tickets and Participants", ev),
            data_block("Verified Similar-Work Assignment History",
                       state.get("similar_history")),
            data_block("Verified Candidate Roster and Current Workload",
                       state.get("roster_load")))
        return f"""\
# Task

Recommend an evidence-backed assignee for every item in the ticket draft.

## Constraints

- Preserve each zero-based draft `index`.
- Use only a verified Jira user ID in `skcc.x1042` form; never print or guess a name.
- Every reason must come from the supplied data. Inspect similar-work history and ticket counts as well as workload. State in Korean when no similar history exists.
- Include at least one `alternates` candidate with the reason and limitation; the user chooses in the UI.
- Distribute work deliberately instead of assigning every item to the same person without evidence.
- Assign each child Sub-Task in `children`. Existing child assignees in the draft may be temporary round-robin values, so use workload and history to correct them.
- When the request or draft says work must be distributed and at least two verified roster candidates exist, assign sibling children to distinct users. Returning the same user for every child is invalid unless only one eligible candidate exists in the supplied roster.
- Never assign a person whom the same analysis rejects as overloaded or unsuitable.
- Write `reasons`, alternate `why`, child `why`, and `caution` in Korean.

## Ticket Draft Data

{draft_text(state.get('draft')) or '(no draft)'}

Inferred module: {state.get('module') or 'unknown'}{data}"""

    def schema(self):
        return SCHEMA

    def pre_validate_structured_output(self, state, out, *, output_contract: str, execution_stage: str) -> dict:
        return validate_role_output(self.name, out)

    def apply(self, state, out):
        # 초안에 없는 항목 번호는 버린다 — 실 모델이 1건짜리 초안에 [0]~[5]를 낸 적이 있다.
        # 화면이 그 유령 배정을 그리면 사용자는 "무슨 티켓의 담당자지?"가 된다.
        n_items = len((state.get("draft") or {}).get("items") or [])
        rows = []
        for a in (out.get("assignments") or []):
            if not isinstance(a, dict):
                continue
            idx = int(a.get("index") or 0)
            if not (0 <= idx < n_items):
                continue
            from app.agent.workflow.relevance import evidence_is_relevant
            blocked = {str(e.get("key") or "") for e in (state.get("evidence") or [])
                       if e.get("key") and not evidence_is_relevant(e)}
            has_similar = bool(str(state.get("similar_history") or "").strip())
            reasons = [str(r).strip() for r in (a.get("reasons") or []) if str(r).strip()]
            reasons = [r for r in reasons if not any(k in r for k in blocked)
                       and (has_similar or "유사" not in r)]
            # "유사 티켓 X 담당"은 담당 이력 표에서 **이 추천 사용자 행**에 X가 있을 때만
            # 사실이다. BUG2 실측에서 x1210을 추천하면서 실제 x1402 담당 DL-9090을
            # x1210의 경험처럼 썼다. workload 근거는 남기되 거짓 이력 근거는 제거한다.
            user = str(a.get("user") or "").strip()
            own_hist = next((line for line in str(state.get("similar_history") or "").splitlines()
                             if line.lstrip().startswith(f"- {user} ")), "")
            clean_reasons = []
            for reason in reasons:
                if "유사" in reason:
                    refs = _ticket_keys(reason)
                    if not own_hist or (refs and not any(k in own_hist for k in refs)):
                        continue
                cleaned = _ground_assignment_reason(reason, own_hist, state.get("roster_load"))
                if cleaned:
                    clean_reasons.append(cleaned)
            reasons = clean_reasons
            if not any(any(ch.isdigit() for ch in r) for r in reasons):
                measured = _workload_reason(state.get("roster_load"), user)
                if any(ch.isdigit() for ch in measured):
                    reasons = [measured]
            kids = []
            for c in (a.get("children") or []):
                if not isinstance(c, dict) or not str(c.get("user") or "").strip():
                    continue
                child_user = str(c.get("user") or "").strip()
                child_hist = next((line for line in
                                   str(state.get("similar_history") or "").splitlines()
                                   if line.lstrip().startswith(f"- {child_user} ")), "")
                why = _ground_assignment_reason(
                    str(c.get("why") or "").strip(), child_hist, state.get("roster_load"))
                if not why:
                    why = _workload_reason(state.get("roster_load"), child_user)
                    if not any(ch.isdigit() for ch in why):
                        continue
                elif not any(ch.isdigit() for ch in why):
                    measured = _workload_reason(state.get("roster_load"), child_user)
                    if any(ch.isdigit() for ch in measured):
                        why = measured
                kids.append({"index": int(c.get("index") or 0),
                             "user": child_user, "why": why})
            alternates = []
            for x in (a.get("alternates") or []):
                if not isinstance(x, dict) or not x.get("user"):
                    continue
                alt_user = str(x.get("user") or "").strip()
                alt_hist = next((line for line in
                                 str(state.get("similar_history") or "").splitlines()
                                 if line.lstrip().startswith(f"- {alt_user} ")), "")
                why = _ground_assignment_reason(
                    str(x.get("why") or "").strip(), alt_hist, state.get("roster_load"))
                if not why:
                    why = _workload_reason(state.get("roster_load"), alt_user)
                    if not any(ch.isdigit() for ch in why):
                        continue
                elif not any(ch.isdigit() for ch in why):
                    measured = _workload_reason(state.get("roster_load"), alt_user)
                    if any(ch.isdigit() for ch in measured):
                        why = measured
                alternates.append({"user": alt_user, "why": why})
            row = {"index": idx, "user": user,
                   "reasons": reasons, "children": kids,
                   "alternates": alternates[:2]}
            item = ((state.get("draft") or {}).get("items") or [])[idx]
            rows.append(_enforce_item_roster(
                _normalize_workload_choice(row), item, state.get("roster_load")))

        # Structured output이 유효해도 모델이 초안 항목 하나를 통째로 빠뜨릴 수 있다.
        # 컴포넌트 로스터가 검증된 경우에는 다시 LLM을 호출하지 않고 최소 부하 후보로
        # 안전하게 복원한다. 이 경로는 추천 누락 때문에 전체 생성 턴을 재시도하던 비용도 없앤다.
        present = {r.get("index") for r in rows}
        for idx, item in enumerate((state.get("draft") or {}).get("items") or []):
            if idx in present:
                continue
            fallback = _enforce_item_roster(
                {"index": idx, "user": "", "reasons": [],
                 "children": [], "alternates": []},
                item, state.get("roster_load"))
            if fallback.get("user"):
                rows.append(fallback)
        rows.sort(key=lambda row: int(row.get("index") or 0))
        named = sum(1 for r in rows if r["user"])
        return {"assignments": rows,
                "trace": note(state, self.name,
                              f"{named}/{len(rows)}건 담당자 제안" + (
                                  f" · {out['caution'][:60]}" if out.get("caution") else ""))}


def _ticket_keys(text: str) -> list[str]:
    import re
    return re.findall(r"\b[A-Z][A-Z0-9]*-\d+\b", str(text or ""))


def _roster_display_names(roster_load) -> set[str]:
    """Extract verified display names from the human-readable workload material."""
    import re
    names = set()
    for line in str(roster_load or "").splitlines():
        match = re.match(r"\s*-\s*[A-Za-z][A-Za-z0-9.]+\s+(.+?)\s+[—–-]\s+", line)
        if match:
            name = match.group(1).strip()
            if name:
                names.add(name)
    return names


def _ground_assignment_reason(reason: str, own_history: str, roster_load="") -> str:
    """담당 이력 표에 없는 티켓·경험 주장을 지우고 측정된 workload 절은 보존한다."""
    import re
    text = str(reason or "").strip()
    # Reasons are rendered next to the typed assignee identity.  A model-written plain
    # display name is both redundant and unsafe: one measured run attributed another
    # person's ticket to the selected user.  Fall back to measured workload instead.
    if any(name in text for name in _roster_display_names(roster_load)):
        return ""
    refs = _ticket_keys(text)
    if refs and (not own_history or any(key not in own_history for key in refs)):
        return ""
    if not own_history and re.search(r"경험|유사\s*(?:업무|티켓)|코멘트\s*\d+건|"
                                     r"(?:티켓|작업)\s*담당|"
                                     r"업무에\s*적합|적합(?:함|하다|합니다)?", text):
        # "진행중 8건이며 ETL 경험"처럼 한 문장에 섞였으면 검증 가능한 부하만 살린다.
        m = re.search(r"진행\s*중(?:인)?\s*(?:티켓|작업)?\s*(\d+)\s*건|진행중\s*(\d+)\s*건",
                      text)
        if m:
            return f"진행중 {next(x for x in m.groups() if x is not None)}건"
        return ""
    return text


def _workload_reason(roster_load, user: str) -> str:
    import re
    for line in str(roster_load or "").splitlines():
        if line.lstrip().startswith(f"- {user} "):
            m = re.search(r"진행중\s*(\d+)\s*건", line)
            if m:
                return f"진행중 {m.group(1)}건"
    return "승인 화면에서 현재 부하 확인 필요"


def _module_roster(roster_load, module: str) -> list[dict]:
    """`_roster_load`의 사람 친화 텍스트에서 한 모듈의 검증 후보만 복원한다.

    조회가 실패해 전사 로스터로 넓어진 섹션은 요청 모듈과 이름이 다르므로 선택하지 않는다.
    즉, 알 수 없는 컴포넌트에 전사 최소부하자를 그 모듈 담당자로 가장하지 않는다.
    """
    import re

    wanted = str(module or "").strip().casefold()
    if not wanted:
        return []
    active = False
    people = []
    for raw in str(roster_load or "").splitlines():
        line = raw.strip()
        header = re.fullmatch(r"\[(.+?)\s+로스터·부하\]", line)
        if header:
            active = header.group(1).strip().casefold() == wanted
            continue
        if not active:
            continue
        match = re.match(
            r"-\s+(\S+)\s+.*?—\s+진행중\s+(\d+)건\s+·\s+열림\s+(\d+)건",
            line)
        if match:
            people.append({"user": match.group(1),
                           "in_progress": int(match.group(2)),
                           "open": int(match.group(3))})
    return sorted(people, key=lambda p: (p["in_progress"], p["open"], p["user"]))


def _has_verified_assignment_experience(reasons) -> bool:
    """Return whether a cleaned reason contains direct, user-specific history evidence."""
    import re

    text = " ".join(str(reason or "") for reason in (reasons or []))
    return bool(re.search(
        r"\b[A-Z][A-Z0-9]*-\d+\b|유사\s*(?:티켓|작업|업무)?\s*\d+\s*건|"
        r"(?:티켓|작업)\s*담당",
        text,
    ))


def _enforce_item_roster(row: dict, item: dict, roster_load) -> dict:
    """초안 컴포넌트와 다른 모듈의 추천을 검증된 후보로 교정한다.

    모델은 전체 조사 문맥에서 눈에 띈 다른 모듈 사람을 고를 수 있다. 반면 후보 집합은
    초안 컴포넌트와 people.yaml이 결정하는 기계적 제약이다. 후보가 맞는 선택은 모델의
    유사 이력 판단을 보존하고, 후보 밖 선택이나 누락만 최소 부하 순으로 교정한다.
    """
    module = next((str(c).strip() for c in (item.get("components") or [])
                   if str(c).strip()), "")
    candidates = _module_roster(roster_load, module)
    chosen = str(row.get("user") or "").strip()
    fixed_source = str(item.get("assignee_source") or "")
    if fixed_source == "user":
        # A user decision outranks both semantic experience ranking and workload order.
        chosen = str(item.get("assignee") or "").strip()
        row = dict(row, user=chosen, reasons=["사용자 지정 담당자"], alternates=[])
    elif fixed_source == "user_unassigned":
        chosen = ""
        row = dict(row, user="", reasons=["사용자 지정 미할당"], alternates=[])
    if not candidates:
        supplied = {int(child.get("index") or 0): dict(child)
                    for child in (row.get("children") or []) if isinstance(child, dict)}
        children = []
        for child_index, child in enumerate(item.get("children") or []):
            existing = supplied.get(child_index, {})
            source = str(child.get("assignee_source") or "")
            if source == "user_unassigned":
                children.append({"index": child_index, "user": "",
                                 "why": "사용자 지정 미할당"})
            elif source == "user":
                children.append({"index": child_index,
                                 "user": str(child.get("assignee") or "").strip(),
                                 "why": "사용자 지정 담당자"})
            elif existing:
                children.append(existing)
        row["children"] = children
        return row
    by_user = {p["user"]: p for p in candidates}
    if fixed_source not in ("user", "user_unassigned") and chosen not in by_user:
        chosen = candidates[0]["user"]
        picked = by_user[chosen]
        row = dict(row, user=chosen,
                   reasons=[f"{module} 로스터 · 진행중 {picked['in_progress']}건 · "
                            f"열림 {picked['open']}건"])
        row["alternates"] = [
            {"user": p["user"],
             "why": f"{module} 로스터 · 진행중 {p['in_progress']}건 · 열림 {p['open']}건"}
            for p in candidates if p["user"] != chosen
        ][:2]

    # Even a valid roster member can arrive with a stale workload number or be repeated as
    # their own alternate.  Both defects made the prose compare one person with themselves.
    if chosen in by_user:
        import re

        def with_load(reason: str, person: dict) -> str:
            value = str(reason or "").strip()
            if re.search(r"진행\s*중|진행중", value):
                value = re.sub(r"(진행\s*중(?:인)?\s*(?:티켓|작업)?\s*)\d+(\s*건)|"
                               r"(진행중\s*)\d+(\s*건)",
                               lambda m: ((m.group(1) or m.group(3))
                                          + str(person["in_progress"])
                                          + (m.group(2) or m.group(4))), value)
            return value

        row["reasons"] = [with_load(reason, by_user[chosen])
                          for reason in (row.get("reasons") or []) if str(reason).strip()]
        alternates, seen = [], {chosen}
        for alternate in (row.get("alternates") or []):
            if not isinstance(alternate, dict):
                continue
            user = str(alternate.get("user") or "").strip()
            if not user or user in seen or user not in by_user:
                continue
            seen.add(user)
            alternates.append({"user": user,
                               "why": with_load(alternate.get("why"), by_user[user])})
        row["alternates"] = alternates[:2]

    # The model sees a prose roster and may omit the true minimum from alternates or attach
    # another person's count to the selected ID. Only after counts have been rebound to the
    # complete module roster is a workload superlative safe. Verified direct history keeps
    # semantic precedence; workload-only selection is compiler-owned and deterministic.
    chosen_load = by_user.get(chosen)
    minimum_load = candidates[0]
    if (fixed_source not in ("user", "user_unassigned")
            and chosen_load
            and (chosen_load["in_progress"], chosen_load["open"])
                > (minimum_load["in_progress"], minimum_load["open"])
            and not _has_verified_assignment_experience(row.get("reasons"))):
        picked = candidates[0]
        chosen = picked["user"]
        tied = [person for person in candidates
                if (person["in_progress"], person["open"])
                == (picked["in_progress"], picked["open"])]
        rank = "공동 최저라" if len(tied) > 1 else "가장 낮아"
        row = dict(
            row,
            user=chosen,
            reasons=[
                f"검증된 관련 이력 근거 없음 · {module} 로스터 · "
                f"진행중 {picked['in_progress']}건 · 열림 {picked['open']}건 · "
                f"후보 중 현재 부하가 {rank} 임시 추천"
            ],
            alternates=[{
                "user": person["user"],
                "why": (f"{module} 로스터 · 진행중 {person['in_progress']}건 · "
                        f"열림 {person['open']}건")
            } for person in candidates if person["user"] != chosen][:2],
        )

    # 모델이 자식만 다른 모듈 사람에게 줬다면 같은 후보 집합 안에서 분산한다.
    children = []
    supplied = {int(c.get("index") or 0): dict(c)
                for c in (row.get("children") or []) if isinstance(c, dict)}
    for child_index, child in enumerate(item.get("children") or []):
        existing = supplied.get(child_index, {})
        user = str(existing.get("user") or "").strip()
        if child.get("assignee_source") == "user":
            user = str(child.get("assignee") or user).strip()
        elif child.get("assignee_source") == "user_unassigned":
            # An explicit empty assignment is a user decision, not a missing model field.
            # Keep an aligned display row so ResultIntegrator cannot narrate a roster member
            # while merge_assignments correctly leaves the payload unassigned.
            children.append({"index": child_index, "user": "",
                             "why": "사용자 지정 미할당"})
            continue
        elif user not in by_user:
            user = candidates[child_index % len(candidates)]["user"]
        if not user:
            continue
        if user in by_user:
            p = by_user[user]
            why = (f"{module} 로스터 · 진행중 {p['in_progress']}건 · "
                   f"열림 {p['open']}건")
        else:
            why = "사용자 지정 담당자"
        children.append({"index": child_index, "user": user, "why": why})
    if children:
        row["children"] = children
    return row


def _normalize_resolved_assignment_rationale(draft: dict) -> dict:
    """Remove stale unassigned prose once every payload item has an assignee.

    ``rationale`` is authored before People Advisor runs in the graph fan-out. The merged
    item fields are authoritative; keeping a sentence such as ``담당자는 미정`` after all
    fields were filled makes the pending payload contradict itself.
    """
    import re

    result = dict(draft or {})
    targets = []
    for item in result.get("items") or []:
        if not isinstance(item, dict):
            continue
        targets.append(item)
        targets.extend(child for child in (item.get("children") or [])
                       if isinstance(child, dict))
    if not targets or not all(str(item.get("assignee") or "").strip() for item in targets):
        return result
    rationale = str(result.get("rationale") or "")
    if not rationale:
        return result
    rationale = re.sub(
        r"(?im)(?:^|(?<=[.。\n]))\s*[^.。\n]{0,100}담당(?:자)?(?:는|은|을|를)?"
        r"[^.。\n]{0,50}(?:미정|미할당|정하지\s*않|비워\s*둠|비어\s*있)"
        r"[^.。\n]*(?:[.。]|$)",
        " ", rationale,
    )
    result["rationale"] = re.sub(r"[ \t]+\n", "\n", rationale).strip()
    return result


def _normalize_workload_choice(row: dict) -> dict:
    """경력 근거 없이 workload만 비교했으면 실제 최소 진행 건수 후보를 1순위로 둔다."""
    import re

    def load(parts) -> int | None:
        text = " ".join(str(x) for x in parts)
        m = re.search(r"진행\s*중(?:인)?\s*(?:티켓|작업)?\s*(\d+)\s*건|진행중\s*(\d+)\s*건", text)
        if not m:
            return None
        return int(next(x for x in m.groups() if x is not None))

    reasons = list(row.get("reasons") or [])
    # 유사 이력·직접 경험이 있는 선택은 부하만으로 뒤집지 않되, 대안의 더 낮은 부하를
    # "높다"고 거꾸로 설명하게 두지는 않는다.
    has_experience = any(re.search(r"유사|(?:티켓|작업)\s*담당|DL-\d+", str(r))
                         for r in reasons)
    primary_load = load(reasons)
    alts = list(row.get("alternates") or [])
    ranked = [(load([a.get("why")]), i, a) for i, a in enumerate(alts)]
    ranked = [x for x in ranked if x[0] is not None]
    if primary_load is None or not ranked:
        return row
    alt_load, idx, alt = min(ranked, key=lambda x: x[0])
    if alt_load >= primary_load:
        return row
    if has_experience:
        new = dict(row)
        alts[idx] = dict(alt, why=(f"진행중 {alt_load}건으로 현재 부하는 더 낮지만, "
                                   "1순위의 관련·완료 경험을 우선함"))
        new["alternates"] = alts
        return new
    old_user = str(row.get("user") or "")
    new = dict(row)
    new["user"] = str(alt.get("user") or "")
    new["reasons"] = [
        f"검증된 관련 이력 근거 없음 · 진행중 {alt_load}건으로 후보 중 "
        "현재 부하가 가장 낮아 임시 추천"
    ]
    alts[idx] = {"user": old_user, "why": f"진행중 {primary_load}건으로 1순위보다 부하가 높음"}
    new["alternates"] = alts
    return new


def merge_assignments(draft: dict, assignments: list) -> dict:
    """제안된 담당자를 초안에 실제로 꽂는다. **근거가 없는 제안은 반영하지 않는다** —
    근거 없이 배정된 담당자는 승인 화면에서 사용자가 검증할 방법이 없다.

    자식(Sub-Task) 담당도 여기서 덮는다. WorkArchitect 의 `_fill_owners` 는 모듈 명단을
    **순번으로** 돌릴 뿐 부하를 보지 않는다 — 실측: PeopleAdvisor 가 "x1450 은 진행중 15건이라
    부적합"이라 써 놓고 자식 2건이 그 사람에게 갔다. 사람을 고르는 일은 한 역할의 것이다.
    """
    items = list((draft or {}).get("items") or [])
    for a in assignments or []:
        i = a.get("index")
        if not (isinstance(i, int) and 0 <= i < len(items)):
            continue
        if a.get("user") and a.get("reasons"):
            # 사용자가 입으로 지정한 담당("성능 측정은 x1402")은 추천이 못 덮는다 —
            # 지정은 결정이고 추천은 제안이다(실측: 추천이 지정 3건을 전부 한 사람으로 뭉갬).
            if items[i].get("assignee_source") not in ("user", "user_unassigned"):
                items[i] = dict(items[i], assignee=a["user"])
        kids = [dict(c) for c in (items[i].get("children") or []) if isinstance(c, dict)]
        if not kids:
            continue
        touched = False
        for c in a.get("children") or []:
            j, who = c.get("index"), str(c.get("user") or "").strip()
            if isinstance(j, int) and 0 <= j < len(kids) and who \
                    and kids[j].get("assignee_source") not in ("user", "user_unassigned"):
                kids[j]["assignee"] = who
                touched = True
        if touched:
            items[i] = dict(items[i], children=kids)
    return _normalize_resolved_assignment_rationale(dict(draft or {}, items=items))

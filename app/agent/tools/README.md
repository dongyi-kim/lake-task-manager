# app/agent/tools — 에이전트가 LTM 을 만지는 손

## 원칙

**LTM 내부 함수를 직접 부른다** — HTTP 왕복 없음. 자기 API 를 자기가 다시 부르면 같은 일을 두 번
하면서 세션·인증만 복잡해진다. 내부 호출이면 캐시·무효화·회로차단기·AuthProvider 가 그대로
재사용되고, **prod SSO 로 바꿔도 도구는 손댈 게 없다**. 접근은 전부 `_ctx.py` 한 곳을 거친다.

**docstring 은 LLM 이 읽는 명세다.** 사람용 설명이 아니라 "언제 쓰는지 / 무엇이 나오는지 /
무엇이 **안** 나오는지"를 적는다. 안 나오는 것을 적어야 모델이 다음 도구로 넘어간다.

**출력은 다이어트한다.** 도구 결과는 그대로 컨텍스트에 실린다. 원본 Jira 이슈 하나가 4~8KB 라
20건이면 그것만으로 수만 토큰이다. `_ctx.trim()` / `_ctx.compact()` 로 판단에 필요한 것만 남긴다.

**검증 규칙을 두 벌 만들지 않는다.** `validate_ticket_plan` 은 화면의 Bulk 생성이 쓰는
`domain/bulk.validate_bulk` 를 그대로 부른다. 규칙이 갈라지면 더 관대한 쪽이 사고를 낸다.

**역할 분리는 프롬프트가 아니라 도구 목록으로 한다.** 전부 다 주면 Research Analyst 이 티켓을 만든다.

## 묶음

| 묶음 | 도구 | 성격 |
|---|---|---|
| `SEARCH_TOOLS` | `search_work_history` `get_ticket` `get_ticket_context` `get_epic_tree` `find_parent_epic` `deep_search` | 읽기 |
| `PEOPLE_TOOLS` | `get_team_workload` `get_ticket_participants` `get_person_profile` `get_module_people` | 읽기 |
| `RULE_TOOLS` | `search_rules` | 정적 RAG |
| `REVIEW_TOOLS` | `validate_ticket_plan` `list_ticket_options` `list_child_types` `list_transitions` | 부작용 없음 |
| `WRITE_TOOLS` | `create_tickets` `update_ticket` `add_ticket_comment` `transition_ticket` | **승인 토큰 필수** |

`search_work_history`(키워드)와 `deep_search`(의미)는 **경쟁하지 않는다**. 전자는 그 단어를 쓴
문서를, 후자는 그 단어를 안 썼지만 같은 이야기를 하는 문서를 찾는다 — "CDC"로 검색하면
"변경분 실시간 반영"이라 적힌 6개월 전 티켓은 절대 안 나온다. 비용이 다르므로 도구도 나눈다
(`deep_search` 는 본문을 긁고 임베딩까지 한다 → [`../retrieval/`](../retrieval/)).

탐색을 셋으로 나눈 이유 — 검색으로 실마리(①) → 티켓을 열어 읽고(②) → 링크를 타고 번진다(③).
어디까지 갈지는 모델이 정한다(ReAct). 한 도구가 다 하면 매번 최대 비용을 치른다.

담당자 근거 네 신호: **① 현재 워크로드**(`get_team_workload`) · **② 유사 티켓 담당 이력**
(Research Analyst 의 검색 결과) · **③ 코멘트·멘션 참여**(`get_ticket_participants`) ·
**④ 모듈 소속 + 최근 활동**(`get_module_people` / `get_person_profile`).
도구는 **순위를 매기지 않는다** — 신호만 모으고 판단과 문장은 모델이 한다.

## 쓰기 게이트 (HITL)

쓰기 도구는 `approval_token` 없이 아무것도 못 한다 → [`../approval.py`](../approval.py).
토큰은 **그 내용에만** 유효하다(payload 해시 대조) — A 를 승인받고 B 를 만드는 경로가 막힌다.
1회용이며 30분 뒤 만료된다. 프롬프트로만 걸지 않는 이유는, 우리 도구가 **남이 쓴 티켓 본문·
코멘트를 그대로 컨텍스트에 싣기** 때문이다(거기 섞인 문장이 지시처럼 읽힐 수 있다).

## 도메인 제약 (도구가 알고 있어야 하는 것)

- **Story Point 는 Story 타입에만** 설정 가능 → 생성 시에는 못 넣는다
- Bulk 는 `mode` 가 하나뿐 — Sub-Task 는 부모가 이미 있어야 하므로 **두 번 나눠** 부른다
- 한 번에 100건 상한(`MAX_ITEMS`)
- 상태명·필드 id 를 하드코딩하지 않는다 → `list_transitions` / `list_ticket_options` 로 확인
- `Epic → Task-tier → Sub-Task` 계층과 허용 field/action은
  `app/domain/ticket_actions.py`가 집행한다. `statusCategory=done`에서는 field update를
  거부하지만 comment와 현재 Jira가 제공한 `Reopened` 전이는 허용한다. 전이 후 변경은 새 승인이다.

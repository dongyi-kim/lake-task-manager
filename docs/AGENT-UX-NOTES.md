# Agent UX 노트 — 레퍼런스 인사이트와 우리 적용 (R1~R3)

사용자 지시 3건에 대한 조사·감사·반영 기록:
① 조기 질문(빨리 물었으면 탐색이 짧았을 지점) 감사 ② 정확성만이 아니라 **가시성·UX 검증**
③ 엔터프라이즈/오픈소스 에이전트 가이드라인 리서치와 반영.

## 1. 레퍼런스 인사이트 → 우리 적용 지도

| 패턴 (출처) | 내용 | 우리 상태 |
|---|---|---|
| Intent Preview (Smashing Magazine 2026-02, agentic UX patterns) | 행동 전에 계획·해석을 보여 주고 승인/수정 기회 | ✅ 해석 확인 턴("제가 이해한 바") · 진행 플랜 체크리스트 · 승인 카드 |
| Escalation Pathway (동일 출처 + Anthropic Building Effective Agents) | 모호하면 자신 있게 추측하지 말고 **질문·선택지·사람 개입** | ✅ 표기 후보 객관식 · 구조 확인 질문 · Epic choice. 반대 규율(확실한데 되묻기 금지 — 위임 우선)도 코드로 |
| Checkpoint before irreversible (Anthropic) | 비가역 행동 전 사람 확인 | ✅ HITL 승인 토큰(내용 지문) — 처음부터 |
| Explainable Rationale | 행동·판단의 근거를 사람 말로 | ✅ rationale·근거 병기·담당 추천 사유. 승인 카드 경고("확인 필요") |
| Confidence Signal | 확신 수준 표시 | 🔶 부분 — 경고·"확인 필요"로 표현. 수치화는 과잉으로 판단(백로그) |
| Action Audit & Undo | 행동 로그 + 되돌리기 | 🔶 결과 카드(created/failed)·Langfuse 트레이스는 있음. Undo 는 Jira 특성상 미지원(백로그: 생성 직후 삭제 제안) |
| Citations rendered clickable next to content (MS Copilot citation 가이드) | 인용은 본문 옆 클릭 가능한 참조로, 태그 보존 | ✅ 참조 인덱스([N] 마커 + **참조** 섹션) + 키·문서 뱃지 렌더 + 중복 번호 코드 병합 |
| Answer format instructions are code (Copilot Studio guidance) | 길이·톤·형식·인용 스타일을 지시로 고정 | ✅ responder.md 형식 규칙 + judge 가 검증. "지시는 코드처럼" — 우리 원칙(보장은 코드)과 동일 |

핵심 수렴점: **"모르면 묻고, 알면 묻지 말고, 보여줄 땐 사람의 눈을 위해"** — 세 출처가
공통으로 말하는 것이고, 우리 구현은 세 방향 모두 코드 가드로 보장한다.

## 2. R1 — 조기 질문 감사 결과

"혼자 오래 탐색하는 대신 한 번 물었으면 됐을 지점"을 흐름별로 점검:

| 흐름 | 판정 | 조치 |
|---|---|---|
| plan_work 막연한 첫 요청 | **개선함** — 조사 전 해석 확인 턴(범위·모듈·Epic choice) | 커밋 6eb8812 |
| 식별자 오탈자 | **개선함** — '기록 없음' 대신 유사 후보 객관식 | 커밋 0c67f1b |
| 공백형 표기 | 물을 필요 없음 — 변형 검색이 정확히 찾으면 바로 답(확인은 불확실할 때만) | 커밋 079a361 |
| modify 대상 모호("그 티켓") | 기존 규율로 충분 — 조사에서 후보를 찾고 refiner 가 확인 질문. 조사가 대개 한 번에 좁힌다 | 유지 |
| ask 일반 주제 모호 | 묻지 않는 것이 맞다 — Curator 의 gaps(모르는 것 명시)가 후속 질문을 유도. 선제 질문은 과잉(확실한데 되묻기 감점 원칙) | 유지 |
| 사람 이름 동명이인 | mock 에선 미발생. prod 신호 시 choice 로(백로그) | 백로그 |

원칙으로 정리: **질문은 "사용자만 아는 갈림"에만, 탐색 전에, 객관식으로.**
찾아보면 아는 것을 묻는 것과, 물어야 할 것을 탐색으로 때우는 것 모두 결함이다.

## 3. R2 — 가시성·UX 검증 체계

- judge 5축으로 확장(`tools/agent_scenarios.py`): visibility 기준 구체화(값=표,
  이력=타임라인, 근거 3+ = 참조 인덱스, 인용 벽=2점 이하, 없음 나열 감점) +
  **interaction 축 신설**(모호→확인 질문 냈나 / 확실한데 되물었나).
- 결정적 체커 `_ux_ok`: '확인된 기록 없음' 3회↑ 실패, [N] 마커 3개↑인데 참조 섹션
  없으면 실패 — judge(주관) 이전의 최소선.
- 시나리오 추가: DATA10(오탈자 → choice 확인이 정답, 추정 데이터 유출 금지),
  DATA11(확인 후속 → 표+참조 형식까지 검증).
- 평가 규율(전 라운드 확립): 배터리 green ≠ 품질 — 출력 전문 정성 평가·기대 대조·
  Claude 레퍼런스 비교(docs/DRAFT-COMPARISON.md)가 최종 게이트.

## 4. 백로그 (인사이트 중 미반영)

1. Confidence Signal 수치화 — 현재는 경고 문구로 갈음. 필요성 관찰 후 결정.
2. 생성 직후 되돌리기 — "방금 만든 DL-x 삭제해줘" 흐름(HITL 삭제 도구). Jira 롤백
   부재를 UX 로 보완.
3. 동명이인 choice — prod 인명 검색에서 복수 일치 시.
4. Autonomy Dial — 사용자별 "얼마나 알아서 할지" 기본값 설정(설정 패널). 현재는
   발화("알아서")로만 제어.

> 출처: [Smashing Magazine — Designing For Agentic AI](https://www.smashingmagazine.com/2026/02/designing-agentic-ai-practical-ux-patterns/) ·
> [Anthropic — Building Effective Agents](https://www.anthropic.com/research/building-effective-agents) ·
> [MS Copilot Studio — instructions guidance](https://learn.microsoft.com/en-us/microsoft-copilot-studio/guidance/generative-mode-guidance) ·
> [M365 Copilot citations](https://team400.ai/blog/2026-05-microsoft-365-copilot-plugin-citations)

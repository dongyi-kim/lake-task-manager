# Tracking implementation ledger

Plan: docs/design/tracking/plan.md; base 7e8e9efc; branch feat/tracking.

| Work pair | Shared contract | Ruling |
|---|---|---|
| Backend / UI | endpoints, camelCase run snapshot | agree wire examples before frontend binding; backend owns routes/main, UI owns static files |
| Backend / mock | Jira 8.20.8 search/comment/history semantics | mock implements upstream contracts; collector retains unsupported/partial fallback |
| Mock / performance | realistic 15 people, 10 Epic, 30 Task, 60 Sub-Task | persistent fixture source, OS-temp runtime, cold/warm separate |
| Backend itself | exact completion vs low call count | history required for completion/period assignee; other field display adds no collection |
| UI itself | dense hierarchy and complete functionality | distinct evidence region, square folding, local highlight/filter, no read-ack on mere listing |
| Tests / workspace | temp writes and branch isolation | user rules override stale AGENTS/skill worktree and temp paths |

## Status

- [x] Planning and generated preview prepared.
- [x] Backend + routes + semantic tests.
- [x] UI + integration + browser verification.
- [x] Mock parity + performance fixture and measurements.
- [x] Independent review + targeted regression validation.

## Final decisions and validation

- Date-only inclusive KST period. Module uses current component/WBS/ancestor membership; explicit people use historical responsibility or own activity.
- Raw evidence cache is shared across date ranges and projections, keyed by issue revision. Every new run refreshes lightweight membership/revision; completed source pages survive interruptions.
- Exact completion and assignment require history. Displaying other fields from that history adds no REST call. Truncation or missing identity remains partial, never zero.
- Mock Jira 8.20 search comment/changelog, historical JQL, creator and comment author metadata implemented in the app-owned compatibility layer. No installed package patch or invented Cloud endpoint.
- Independent review caught and verified fixes for DC username/key aliases, missing ancestor retry, inherited module projection, and stale analyzed rows after resumed scope resolution.
- Removed repeated full-checkpoint serialization per source request. Persist completed pages/raw sources; coalesce derived UI snapshots at the first result, batches, or elapsed intervals.
- Targeted integration/static/query/domain/parity suite: **723 passed**. After final scope fixes, Tracking backend/UI/mock subset: **49 passed**. These overlap; they are not a full repository suite or an additive total.
- Browser: light 1600px / dark 1360px, no JS errors or horizontal overflow, folding/highlighting/filter/feed switching made no Tracking API requests, existing TicketDialog opened.
- [Final local benchmark](performance.md): 15 people, 100 active + 9,900 archived tickets, 50ms injected/request. Cold/warm separated; all compared core events matched the fixture oracle.
- Production Jira/SSO latency and actual LLM calls remain unmeasured. No push, merge, or release performed.

## Report UX follow-up

- Default report view replaces event-count reading with Task-level outcomes, completed child titles, recent report/comment excerpts, current assignee/status, and evidence links. Existing tree/date views retained.
- Dashboard separates period completion history, newly created tickets, comments, field changes, and evidence-backed review items. Partial counts remain provisional; no inferred progress percentage.
- AI synthesis area remains visible in an unconfigured state. No automatic AI call. Pins persist locally; copying reports includes scope/time/partial status and ticket-specific reopen/recompletion evidence.
- Real VOC flag/configured component only: virtual `사용자 VOC` WBS → `[대분류 - 소분류]` virtual Epic → stripped display title. Original summaries, saved run, events and totals preserved. Virtual Epic highlight/filter supported; ordinary prefixed tasks remain unchanged.
- Otherwise unmapped VOC may use current assignee's configured module membership. Explicit module/WBS/parent mapping takes precedence; missing assignee proof remains uncertain. Lightweight discovery includes assignee without a separate REST call.
- Independent review fixes: synthetic missing-parent briefs open all child evidence; partial review counts do not show definitive zero; copied completion lines retain reopen state; assignee supplement does not override explicit other-module mapping.
- Final related tests: **441 passed** (Tracking backend/UI + static assets). Browser: real dev data at 1600/1360px, light/dark, pin persistence, copy, evidence pane, tree/feed switching, no JS errors or horizontal overflow. Interaction loop made **0 extra Tracking requests**.
- Separate synthetic browser scenario verifies two VOC categories, prefix removal, preserved ordinary prefix, virtual Epic filtering and tree rows. No server data modified by that scenario.
- Live mock collection for ETL, 2026-10-01 through 2026-11-15: complete, 137 context/target issues including 7 VOC. November-only fixture has no updated ETL VOC, so it correctly has none. Tailscale health returned 200.
- Browser plugin unavailable; used existing Python Playwright/Chromium. ui-skills CLI unavailable (`npx` absent); applied installed frontend-design guidance and existing LTM design tokens. Desktop only, as requested.

## Ticket-name correction

- Existing snapshots already contained titles; date feed, cross-ticket evidence, comment attribution and copied source lists were rendering only keys. Shared title/label helpers now render title first and key as secondary context. Unknown titles say `제목 미확인` rather than presenting the key as a title.
- Applied to report headings, child changes, tree rows, highlighting choices, AI evidence references, missing-source rows and all copied ticket references. Original evidence and VOC display grouping preserved; no new collection.
- Related frontend/static tests: **399 passed**. Tailscale browser verified all 330 date-feed references had titles, 8 child-comment source labels and 2 cross-ticket evidence labels included names, no JS errors/overflow, and no additional Tracking request.

## 2026-11-17 Tracking 보정 검증

- 댓글: 원문 HTML·1/2회 인코딩된 HTML·렌더 결과가 원문 태그를 인용한 경우·일반 텍스트 줄바꿈을 재현. 공통 정화 경로와 코드 엔티티 보존, 3줄 이상 접기 검증. 로컬 저장 자료는 mock wiki 댓글뿐이므로 운영 원문 재현으로 간주하지 않음.
- 중복 제목 바 제거. 조회 조건과 보고 도구를 한 줄에 통합. 트리 스크롤바만 숨기고 휠·키보드 스크롤 유지, WBS의 +/−와 공통 경계 손잡이 적용.
- 완료 지표는 선택 기간에 완료 범주로 진입한 고유 업무 수. Resolved와 Closed가 모두 Done이면 동일 취급하며 Done→Done은 새 완료가 아님. 현재 Done 수·하위 완료 수와 별도 집계. 현행 집계 오류는 로컬에서 재현되지 않아 의미 회귀 검증 추가.
- 새로 들어온 멘션임을 관계 정보로 확인한 링크만 활동에 유지. 직접 추가·삭제·교체·방향 미확인 링크 제외. 원본 이력·checkpoint는 보존.
- VOC 제목의 대분류 내부 하이픈을 잘못 분리하던 경로를 공통 제목 파서로 통합. 120개 VOC + 120개 하위 작업·공유 계층 중복 fixture에서 240개 티켓·사건 보존. 가상 WBS의 직속 행 수는 분류 Epic 수이며, 전체 업무 수와 다름.
- VOC 모듈 판정에서 username 없이 key/accountId만 있고 소속을 입증할 수 없으면 조용히 제외하지 않고 불확실한 수집으로 유지. 표시 이름은 소속 근거로 사용하지 않음.
- 로컬 저장 run의 광역 VOC→ETL VOC는 5→0(11/11–17), 24→7(10/1–11/15), 9→0(11/1–16). ETL 7건의 최근 변경은 10월. 이 수치는 mock 자료이며 운영 VOC 누락 여부를 입증하지 않음.
- 최종 관련 검증 **801건 통과**(backend 318, frontend·정적 자산 483). 이전 부분 실행과 중복 합산하지 않음.
- 이름 캐시 90일 신선도·365일 표시 보존, 서버/환경 격리, 기존 수집 이름 재사용. 과거 댓글의 이름이 더 최근 이름을 덮어쓰지 않도록 관측 시각 보존.
- Task/Sub-task 자체 상태·댓글의 가로 날짜 축 추가. 날짜별 다중 전환·댓글, 빈 날짜·부분 수집, 클릭·키보드 근거 이동. 1280/1440 밝은·어두운 화면 확인, 추가 조회 없음.
- 상세 근거와 독립 리뷰 결과: [후속 검증](../../validation/2026-11-17-tracking-follow-up.md).

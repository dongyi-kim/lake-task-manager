# Tracking Time Axis Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Track completion below.

**Goal:** 승인한 작은 표식·한 줄 말풍선 시안을 실제 Tracking의 통합 시간축에 적용.

**Architecture:** 기존 buildTicketTimeline의 선택 범위·직속 하위·부분 수집 판정을 유지. 저장된 사건을 실제 시각 축으로 투영하는 모델과 충돌 없는 배치 함수를 분리. Vue 컴포넌트는 기간/일간 확대, 하위 펼치기, 기존 근거 focus 계약을 연결.

**Tech Stack:** 기존 Vue ESM, CSS, SVG, pytest/Node, Playwright.

**Spec:** docs/design/tracking/time-axis-proposal.md; app/static/design/tracking-time-axis.{html,css,js}

## Global Constraints

- 동일 checkout의 feat/tracking-time-axis에서 작업. worktree·소스 복제 없음.
- Jira/MCP/API 추가 조회 없음. 실제 저장 사건만 사용.
- 기본 통합 축 하나. 직속 하위 작업별 축은 명시적으로 펼침.
- 상태 표식 16px, 댓글 18px, 안내 24px 한 줄. 안내에 날짜·시간 없음.
- 사건 x는 실제 시각. 밀집 시 y와 안내 위치만 조정하며 실제 표식에서 연결선 시작.
- 댓글 묶음은 같은 티켓만. 정확한 시각·티켓 키·원본 사건 유지.
- 조회된 하위 Done/전체만 현재 집계. 불완전 이력에서 과거 상태선·진척률 추정 금지.
- 기존 VOC 투영 범위, 자체/하위 구분, 잡음 필터, 원본 근거 이동 유지.
- 테스트 임시 파일은 OS 임시 디렉터리에서 생성하고 finally 정리.
- 별도 배포 요청 없음. 개발 주소에서 확인 가능한 구현까지 완료.

## Review Focus

1. 동시 사건·긴 제목·좁은 패널에서 표식/안내 겹침과 잘림.
2. 74개 이상 댓글 묶음의 원본 접근, 출처 혼합 방지.
3. KST 날짜 경계·야간 사건의 확대 범위 밖 안내.
4. 부분 수집·빈 기록·관측 시점 이후의 불확실성 유지.
5. 구조 범위/상세 전환·같은 티켓 갱신에서 기존 스크롤·근거 선택 유지.

## Task 1: Actual-time model and compact timeline

Files: tracking-ticket-timeline.js, tracking-ticket-timeline.css; new trackingTimeAxisModel.js, trackingTimeAxisLayout.js and optional focused Vue axis component; tests/frontend/tracking/test_tracking_time_axis.py.

- [x] 기존 모델 위에 실제 시간 투영·같은 출처 댓글 묶음·부분 수집 검증 단위 테스트 작성.
- [x] 새 순수 모델/배치 모듈과 단일 축 컴포넌트 구현. 데이터 인터페이스는 기존 props와 focus(event.id) 유지.
- [x] 전체 기간/시간 확대/24시간/댓글 표시/하위 펼치기 구현. 빈 기록과 불명 시각 별도 안내.
- [x] 변경 시각 기반 배치, 24px 안내, leader SVG, hover/focus, ResizeObserver 정리 구현.
- [x] 관련 단위 테스트 실행 및 독립 검토.

## Task 2: Product browser regressions

Files: tests/frontend/tracking/test_tracking_ticket_timeline_browser.py, test_tracking_timeline_scope_browser.py.

- [x] 기존 날짜열 표현 검사를 승인된 시간축 표현으로 갱신. 출처/근거/스크롤/추가 조회 없음 검사는 보존.
- [x] Task/Epic 기본 통합 축·하위 확장, 실제 시각 간격, 같은 시각 사건, 24px 안내·연결선, 날짜 확대/야간, 댓글 묶음·키보드, 테마·패널 너비 검증.
- [x] 관련 browser/model/static tests 실행. 실패 원인 수정 후 영향 범위 재검증.

## Task 3: Live development verification and delivery

- [x] Tailscale 실제 Tracking에서 풍부한 저장 run과 DL-5008 / DL-10000 / DL-101 확인.
- [x] 독립 코드 검토 후 발견된 문제 수정. 검증 결과 docs/validation에 기록.
- [x] 앱 변경만 commit. dev health revision 대조 및 최종 개발 링크 제공.

## Execution ledger

- Base: 6c098d6d. 승인된 시안 적용 요청에 따라 별도 디자인 승인 대기 없이 실행.
- 사용자 작업공간 규칙이 skill의 worktree/저장소 내 임시 작업공간 지침보다 우선. 진행 기록은 이 문서에 유지.
- Task 1 구현 담당: native gpt-6.1-sol high. Root는 기존 browser regression 갱신. 별도 계약 검토에서 실제 438px 상세 패널·VOC 투영 범위·원본 focus 이동·부분 이력·KST 반열린 구간을 중점 확인.
- Task 2 선행 검증: 새 기본 통합 축 검사 1건이 기존 날짜열 구현에서 예상대로 실패(2026-11-17). 표현 검사만 갱신하고 근거 이동·추가 API 없음 계약 유지.
- 설계 판정: 저장된 기간 사건만으로 작업별 과거 상태 전 구간을 확정할 수 없으므로 확장 축은 중립 점선과 실제 사건 표식 사용. 현재 상태는 행 제목 옆 별도 표시.

- Implementation and independent review complete; 426 related checks passed. Final reduced-motion adjustment: 4 affected browser cases passed. Live Tailscale confirmed Task/Epic/Story, narrow detail, 0 additional Tracking reads and 0 browser errors. See docs/validation/2026-11-17-tracking-time-axis.md.

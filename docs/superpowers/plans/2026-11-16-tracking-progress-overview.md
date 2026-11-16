# Tracking Progress Overview Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to implement each owned unit and verify integration.

**Goal:** 기간의 주요 성과와 현재 상태·남은 확인을 첫 화면에서 읽을 수 있는 Tracking 화면 구현.

**Architecture:** 기존 저장 실행을 순수 projection으로 요약하고, 기존 report/controller 위에 성과 요약·압축 행·보조 집계 패널을 결합한다. 수집·캐시·API 경로를 추가하지 않는다.

**Tech Stack:** 기존 Vue 3 무빌드 ES modules, CSS tokens, FastAPI, pytest/Node, 로컬 Playwright.

**Spec:** 사용자가 승인한 `C:/Users/PC/research/ltm-tracking-proposal-20261116/index.html` 및 같은 폴더 README.md. 기존 기능 보존이 우선이며 가상 데이터를 제품에 넣지 않는다.

## Global Constraints

- 현재 checkout의 `feat/tracking-progress-overview`에서 작업. worktree·소스 복제 금지.
- 실제 Jira REST/MCP/AI 호출 증가 없음. 저장 결과를 화면에서만 재구성.
- 기간 내 완료 이력과 수집 시점의 현재 완료를 구분. 전체 하위 분모·모듈 완료율 추정 금지.
- 실제 payload에 없는 개발/운영 분류, Confluence 근거, AI 성과 문장을 만들어 넣지 않음.
- 날짜·프리셋·대상·최근 실행·취소/재개·필터·강조·고정·복사·계층·날짜별 보기·근거·티켓 열기 보존.
- 데스크톱 1366×768·1600×1000, 기존 테마 토큰 사용. 앱 기본 포트4457 유지.
- 테스트 임시 파일은 OS 임시 경로에만 생성하고 finally 정리.

## Review Focus

- 기간 종료 이후 재오픈된 현재 상태를 완료 이력과 혼동하지 않음.
- 부분 수집·댓글만 존재·기록 없음이 완료/중단/지연으로 과장되지 않음.
- 공유 경로와 하위 티켓을 중복 집계하지 않음.
- 검색/강조/고정으로 표시 목록이 줄어도 조회 전체 집계 범위를 명시.
- 긴 한글 제목, VOC 다수, 근거 패널, 다크 모드에서 가독성과 조작 유지.

## Task 1: 근거 기반 projection

Files: `app/static/components/trackingOverviewModel.js`, `tests/frontend/tracking/test_tracking_overview.py`.

- [x] 완료·재오픈·부분 수집·댓글·현재 상태 누락 회귀 작성 및 실패 확인.
- [x] `buildTrackingOverview(report)`, `trackingRowSummary(item)` 구현. 기존 report 모델 계약 보존.
- [x] 순수 모델 관련 test 실행.

## Task 2: 실화면 통합

Files: `tracking-view.js`, `tracking-report.js`, 새 `tracking-overview.js`, `styles/tracking-report.css`, 새 `styles/tracking-overview.css`, `index.html`.

- [x] 이름이 있는 성과 최대3개와 전체 범위 집계·확인 사항·AI 자리 구성.
- [x] 기본 행은 제목/기간 변화/현재 상태를 짧게, 상세 댓글·하위 완료는 펼치기와 근거 패널로 보존.
- [x] 기존 모든 조회/보기/필터/고정/복사/근거 동작 및 VOC 계층 보존.
- [x] 근거 패널의 영역 구분, 열기·닫기 포커스 복귀 보완.
- [x] 기존 카드 스타일 제거·새 컴포넌트/스타일 책임 분리.

## Task 3: 검증 및 전달

- [x] `tests/frontend/tracking` 관련 회귀, Vue template compilation, static asset checks.
- [x] 포트4457 listener·health·reload·revision 확인.
- [x] 기존 mock 저장 실행으로 첫 화면·필터·고정·보기·부분 수집·근거·다크 검증. 화면 조작 중 추가 수집 호출0 확인.
- [x] 1366/1600 실제 화면 캡처 및 독립 리뷰. 5개 업무 첫 화면 노출은 실데이터 긴 제목으로 검증하며 미달 시 원인 기록.
- [x] 의도한 앱 파일만 커밋하고 Tailscale 개발 주소·검증 결과 제공. 이번 요청에서 신규 Release는 발행하지 않음.


검증 결과: `docs/design/tracking/progress-overview.md`. 최초 y300 목표 대신 실제 조회 controls와 시점 라벨을 보존해 y360.5, 1366에서 5개 업무 완전 노출로 확정.

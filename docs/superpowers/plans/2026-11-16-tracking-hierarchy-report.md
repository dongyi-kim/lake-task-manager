# Tracking Hierarchy Report Implementation Plan

**Goal:** 계층을 선택해 해당 범위의 그룹별 업무 요약을 읽고, 필요할 때 기존 활동 근거 패널을 여는 화면.

**Architecture:** 기존 VOC projection 위에 순수 scope projection 추가. 저장 실행은 불변이며 계층 선택·그룹 접기·집계·검색·보기 전환은 로컬 계산만 수행.

**Spec:** 사용자의 ‘업무 요약 그룹핑, 계층 선택 시 현재 요약 표 표시’ 요청과 ‘기존 자세히 보기 유지’ 후속 요구.

- 계층 이름: 보고 범위 변경. 별도의 항상 보이는 ‘근거’ 버튼: 기존 상세 패널 열기.
- 목록 제목·근거 버튼: 같은 상세 패널 사용. 닫을 때 원래 버튼으로 포커스 복귀하고 계층·필터·스크롤 유지.
- 계층 선택을 바꾸면 이전 상세 패널을 닫아 다른 범위의 근거와 혼동하지 않음.
- 기본 업무 요약은 WBS/Epic별 그룹. 공유 경로 티켓은 중복 요약하지 않음. VOC 가상 WBS와 분류 Epic 보존.
- 선택 범위의 이름·경로를 표시하고 주요 성과·집계·날짜별 활동·복사에 같은 scope 적용.
- Task 선택은 해당 Task와 하위 근거를 묶음. Subtask 선택은 그 업무의 근거만 표시하며 형제·부모 활동이 섞이지 않음.
- 기존 계층별 수치 표는 ‘계층별 집계’로 접근. 기존 수집·재개·취소·기간·대상·고정·검색·강조·AI·티켓 열기 보존.
- 오른쪽 상시 집계 패널은 ‘집계·AI’ 펼침으로 이동. 왼쪽 계층과 가운데 업무 목록이 동시에 충분한 폭을 가짐.
- 다른 실행을 열면 범위는 전체로 초기화. 같은 실행의 수집 갱신·필터 변경은 계층 접기/선택 유지.
- 전체 하위 분모·진척률 추정, 신규 Jira/Confluence 조회, 실 AI 호출 없음.

## Files and Checks

- [x] `trackingScopeModel.js` + scope regression: WBS/Epic/Task/Subtask, 공유 경로, VOC, 누락 부모, 부분 수집, 불변성.
- [x] `tracking-report.js`: 실제 계층 group props, group folding, filtered copy preserved, scope shortcut.
- [x] `tracking-navigator.js`, `tracking-view.js`: scope/evidence 역할 분리, scoped feed/overview, focus·selection 유지.
- [x] `tracking-scope.css`: navigation와 목록 2열, 그룹 구분, 기존 근거 패널, light/dark.
- [x] 기존 Tracking 회귀 및 Vue compile, static integrity: 372 passed.
- [x] 1366/1600 실제 mock 화면에서 계층선택·근거독립동작·닫기·그룹접기·scope복사·추가API0 검증.
- [x] 검증 결과 기록: [검증 기록](../../validation/2026-11-16-tracking-hierarchy-report.md).

임시 산출물은 OS 임시 경로. 소스 전체 복제·worktree·상위 deploy 저장소 변경·신규 release 없음.

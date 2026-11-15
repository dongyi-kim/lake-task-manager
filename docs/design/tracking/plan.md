# Tracking 구현 계획

2026-11-16 · 기준 main 7e8e9efc · 구현 branch feat/tracking

## 목적과 화면

기간 중 생성·완료·댓글과 확보한 변경 근거를 사람·모듈별로 조사한다. 날짜만 입력하며 시작일·종료일 모두 포함, 종료일은 오늘 선택 가능. 기본 최근 7일, Asia/Seoul 기준. 사람 범위는 기간 중 담당 업무와 본인 활동의 합집합이다. WBS/사람 조직 배치는 현재 설정 기준임을 표시한다.

모듈 선택은 현재 티켓 컴포넌트·WBS/Epic 연결·부모의 모듈 소속 기준이다. 외부 담당자나 작성자의 모듈 내 활동도 포함한다. 사람을 명시적으로 선택했을 때만 당시 담당자 또는 행위자로 범위를 좁힌다. 부모나 소속 근거를 읽지 못하면 제외하지 않고 미확인으로 남긴다.

기존 상단 Task 다음에 Tracking 탭 추가. 기간/대상 → 집계 띠 → 계층 표 2/3 + 상세 1/3 구성. WBS Task → Epic → Task → Sub-Task, 사각 +/− 폴딩, 28~32px 행. 검색·강조·일치 항목만 필터는 로컬 처리하고 조상 경로 보존. 현재 상태와 기간 내 사건 구분. 계층/날짜별 활동 전환, 기존 TicketDialog 연결. 기존 기능·디자인 branch 보존, desktop 전용, main 토큰 재사용.

디자인 시안: ./tracking-preview.png (합성 예시 데이터, 구현 screenshot 아님).
참고: https://www.notion.com/help/views-filters-and-sorts , https://linear.app/docs/pulse

## 정확성과 부하

- 생성은 created와 creator. 완료는 non-Done → Done 상태 사건이며 resolved → closed를 중복 완료로 계산하지 않는다. 재오픈·재완료 사건 보존, 상단은 고유 티켓 수.
- 댓글은 작성일·작성자 기준. 원본에서 관측된 마지막 수정 여부를 별도로 표시하며 삭제 댓글·과거 편집본 복원을 약속하지 않는다.
- 당시 담당자와 정확한 완료 검증에 필요한 이력만 조사. 그 이력의 필드 변경은 추가 REST 없이 표시한다. 필드 변경만을 위한 추가 이력 호출은 기본 제외.
- 검색은 필요한 필드만, description 제외. 검색 comment/expand=changelog 반환과 coverage를 검증하고, 잘림·누락만 보충. 확장 지원은 서버 응답으로 판정. 현재 mock의 빠진 공식 API 동작도 구현한다.
- 후보 검색은 기간 종료 후 다시 수정된 티켓을 잃지 않도록 updated 상한을 두지 않는다. updatedBy와 역사 연산을 지원하면 후보 축소에 사용하며, 미지원 시 보수적으로 탐색 후 원본으로 판정한다.
- 사람 조건은 최대 25명씩 묶어 검색한다. 모듈만 선택한 경우 가벼운 메타데이터·부모 조회로 소속이 명확히 다른 후보를 먼저 제외한 뒤 댓글·이력을 최대 100티켓씩 받는다.
- 캐시는 티켓 원본 기준 공유. 기간/분해 방식이 달라도 재사용하며 새 조회마다 가벼운 검색으로 updated를 확인한다. 키+updated가 일치하는 원본은 cool 7일 동안 재사용하고, 변경된 티켓만 다시 수집한다. 실제 fetchedAt 보존.
- 실패 구간을 건너뛰지 않는다. 수집 실행·원본·완료 페이지·미확인·재개 위치를 SQLite에 영속 저장하고 완료분을 재호출하지 않는다. 일반 TTL cache 정리와 실행 checkpoint를 분리한다.
- 공유 Epic과 조상 rollup은 전역 집계 중복 제거. 미확인/일부 확인/활동 없음 구분. 부분 수집 수치를 최종 합계로 표시하지 않는다.
- 물리 REST 요청/응답 bytes/재시도/수집시간을 기록한다. UI 내부 수집은 MCP를 경유하지 않으며 기본 LLM 토큰 0. AI는 사용자 요청 시 확보한 근거만 전달한다.
- UI의 수집 REST 계측은 인증 요청 제외임을 표시한다. 성능 배터리는 provider에서 인증을 포함한 물리 요청을 별도로 세어 캐시 효과로 숨겨진 요청이 없도록 비교한다.

## API 및 구현 단위

1. `app/tracking/`: 수집, 근거 해석/계층, 영속 실행, 계측. `app/routes/tracking.py` 및 main wiring.
2. `app/static/components/tracking-view.js`, scoped CSS, 상단 route/tab 연결.
3. mock 검색 comment/changelog, updatedBy/역사 조건 등 필요한 공식 Jira 8.20.8 계약 보완. 앱/목업 환경별 알고리즘 분기 금지.
4. 의미 검증·중단 복구·공유 캐시·브라우저·성능 배터리.

공통 UI 계약: `GET /api/tracking/options` → people/modules/defaults. `POST /api/tracking/runs` body `{startDate,endDate,people:[id],modules:[id]}`. `GET /api/tracking/runs/{id}` → id/status/request/asOf/summary/coverage/metrics/issues/events/tree/errors. `GET /api/tracking/runs` 최근 실행. `POST .../{id}/resume`, `POST .../{id}/cancel`. JSON camelCase. 상태 queued/running/complete/partial/paused/cancelled. 정적 화면 조작은 저장된 결과 사용. 전체 키와 세부 필드는 구현자들이 먼저 합의하고 이 문서에 갱신한다.

선택 기능 `POST .../{id}/summary`: 저장된 근거만으로 AI 요약. 근거 ID와 티켓 연결을 검사하고 부분 수집 표시·실제 토큰 사용량을 보존한다. 입력이 너무 크면 조용히 누락시키지 않고 범위를 좁히도록 안내한다. 수집 도구를 모델에 제공하지 않는다.

## 검증과 새 성능 기준

- 누적 1년 10,000여 티켓, 월 약 120 VOC 시나리오와 별도로 **15명 규모 모듈, 현재 진행형 10 Epic + 30 Task + 60 Sub-Task**의 명시적 fixture에서 성능 측정.
- 일간/주간/월간 조회, cold/warm/변경 일부 상태, serial SSO 유사 조건과 병렬 가능한 provider 조건 비교. 주입 지연과 실제 운영 지연을 구분한다.
- 측정: 물리 Jira REST 호출 수(페이지/재시도 포함), MCP 호출 수, 전송 bytes, 첫 결과/전체 소요, 캐시 재사용, 토큰. mock 누락 API를 보완한 경로와 fallback 경로 모두 검증.
- 경계 날짜, creator≠reporter, 담당자 변경, 완료/재오픈/재완료, 과거 생성 최근 댓글, 공유 Epic, 잘린 원본, 연결 중단 후 재개를 검증.
- 통과 기준: 회수 가능한 사건 누락/중복 0, 미확인 오표시 0, 필터/강조 REST 0, 기수집 이력의 필드 표시 추가 REST 0, 재개 시 완료 페이지 재수집 0.

## 진행 기록

- 계획·시안 저장 후 backend/frontend/mock을 파일 소유권별 병렬 구현. 코드와 의미 테스트 통합 후 독립 리뷰 및 실제 브라우저 검증.
- Ruling: 사용자 workspace 규칙 우선. worktree/소스 복제 없음. OS 임시 폴더만 테스트 산출물에 사용, finally 정리. 전체 suite는 CI 정책을 따르고 로컬은 변경 관련 suite만 실행.
- Ruling: 현재 운영 API 측정은 수행하지 않음. 로컬 측정치를 운영 실측으로 표현하지 않음.
- 구현·검증 완료 범위와 수치는 [성능 및 검증 결과](performance.md), [진행 기록](progress.md) 참조.

# 전략적 JQL 실행: 실제 UI 비교

## 변경 목적과 범위

JQL 정규화와 issue/API 캐시는 유지하면서, 논리적인 캐시 membership과 물리적인 Jira 요청을 분리한다. 사람·상태·Epic 조건은 응답만으로 membership을 증명할 수 있는 경우에 묶고, 임의 OR 쿼리는 전체 결과를 캐싱한다. MCP도 UI와 같은 실행 계층을 사용한다.

- MyTasks: 첫 사람의 결과를 먼저 받고 이후 최대 5명씩 조회한다. 완료된 묶음부터 중복 제거·정렬·통계를 반영한다.
- 워크로드: 한 사람의 상태별 조회를 함께 계획한다. 해결일 없는 완료 티켓의 보조 조건은 별도로 유지한다.
- WBS: 아직 표시 가능한 관계 캐시가 없는 Epic을 최대 5개씩 묶는다. 빈 Epic의 Jira Agile 대체 조회와 기존 stale 표시 정책은 유지한다.
- 사용자 표시 이름·이메일처럼 계정 ID가 증명되지 않은 조건은 다른 조건과 겹쳐도 정상 빈 membership으로 추정하지 않는다.
- 모든 페이지의 완료와 mutation fence를 확인한 뒤 membership을 저장한다. 정상 응답이 없는 실패는 빈 결과로 캐싱하지 않는다.

## 비교 조건

- Baseline: `2fdb8feb8d18f3d8852ae3d1bcda54f058a332e8` (`v2026.11.04.2`).
- Candidate: `f0034d20` (첫 사람 우선 표시 조정 포함). 측정 도구와 보고서는 이후 별도 커밋한다. 초기 후보 `bdd341bd`는 첫 카드 표시 지연을 확인한 튜닝 자료이며 최종 비교에서 제외한다.
- 측정 harness: `d1605d4a`. 환경: Windows, 기존 Python Playwright, Chromium `149.0.7827.55`(설치 build 1228), `http://127.0.0.1:4457`, 1600×1000.
- Browser 플러그인이 제공되지 않아 기존 Playwright 사용(`Browser plugin not available`).
- 동일한 합성 데이터: ETL 15명, 인력이 겹치는 Workbench, 3개 Epic, Parent/SubTask와 기간 경계 양쪽의 Task. 데이터 해시를 매 실행 기록한다.
- 실제 UI 버튼·select·FieldEdit를 조작한다. Vue 상태는 읽기 전용 완료·결과 검증에만 사용한다.
- API/leaf 캐시의 논리 시각은 고정하고 타이머·네트워크 지연·성능 시계는 실제 시간으로 측정한다. 긴 baseline 실행만 TTL을 넘겨 비교가 달라지는 것을 방지한다.
- 첫 콘텐츠는 조작 완료 후 선택한 새 필터의 카드/행을 처음 관측한 시간의 상한이다. 사람 검색·WBS 펼치기처럼 여러 동작을 묶은 단계에서는 정확한 첫 paint 시각이 아니다. `actionMs`도 전체 컨트롤 조작 시간이며 순수 클릭 응답 지연이 아니다.
- Task 완료는 스트림·상태축·SubTask 동기화 완료다. Epic 이름·사진 등 부속 작업은 남을 수 있다. 워크로드는 선택 모듈의 모든 인력 통계, WBS는 요청한 Epic 트리까지 준비된 상태를 기다린다.
- Jira 검색 요청 수는 실제 페이지 요청을 센다. leaf 개수와 같다고 가정하지 않으며 메타데이터·후속 요청도 따로 집계한다.
- 캐시·시간 통제가 끝나기 전 pilot 실행은 최종 p50/p95에서 제외한다. 실 Jira·SSO·LLM은 호출하지 않는다.

| 요청당 고정 지연 | 버전별 cache-cold | 버전별 warm reload | 버전별 24조작 여정 | 역방향 여정 | 해석 |
|---|---:|---:|---:|---:|---|
| 800ms | 5 | 20 | 10 | 1 | 주 비교; 표의 p50/p95 |
| 0ms | 1 | 3 | 1 | 1 | 탐색 비교; p95 해석 안 함 |
| 250ms | 1 | 3 | 1 | 1 | 탐색 비교; p95 해석 안 함 |

cache-cold는 기존 요청을 마친 뒤 서버 캐시를 비우고 새 브라우저 context를 여는 조건이다. 프로세스·OS cold startup을 측정한 것이 아니다. warm은 Task/SubTask 완료 직후 reload하며 모든 부속 작업이 끝날 때까지 추가 대기하지 않는다. 여정 앞의 내부 이름 `task-module-etl-cold`는 이미 warm인 추가 진입이므로 cold 분포에서 제외했다.

논리 시각은 `2026-11-04T06:00:00+00:00`이다. 서버 Cache/JiraClient의 `time.time`, JQL compiler의 `now`, 브라우저 CacheMemo의 주입된 `now`만 고정했다. 실제 `Date.now`, 재시도·인증 타이머와 성능 시계는 유지했다. 데이터 612건의 SHA-256은 `0c5fd744172f1bba4a6edaafa7782083a33cb30fadb18a8e71ce8cb64bb6b5d6`이다. 두 버전의 fixture·harness·시각 설정 일치를 자동 확인했다.

p95는 정렬 표본의 `(N-1)×0.95` 위치를 선형 보간한다. 특히 cold N=5는 기술 통계이며 운영 tail latency의 신뢰구간이 아니다. 고정 mock 지연은 왕복 오버헤드를 모사하며 실제 Jira의 인덱스·쿼리 처리 CPU나 SSO 성능을 재현하지 않는다. provider 경과 시간에는 직렬화 대기와 서비스 시간이 함께 포함되므로 이를 합쳐 UI 시간이나 순수 Jira 지연으로 해석하지 않았다. queue-only 시간은 별도로 측정하지 않았다.

## 실제 UI 여정

1. ETL Task cold 진입, 완료 1주→1달, 할당 모두→2주, 진행 모두→1달.
2. 담당자/보고자 전환과 다른 사람 선택, ETL→Workbench→ETL, 기간 복원.
3. Task→워크로드, 할당 기간과 완료 기간 변경, WBS 진입.
4. Epic·Task·SubTask 펼치기/접기, Task로 복귀.
5. 역방향 워크로드 cold→Task→WBS→워크로드.
6. 로딩 중 필터 변경과 수정 후 신규 조회는 별도의 정합성 시나리오로 확인한다.

## 결과

이 612건·ETL 15명 fixture의 800ms 지연 조건에서 Task cold 전체 완료 중앙값은 **120.037→40.017초(66.7% 감소)**, 검색은 매회 **107→21페이지(80.4% 감소)**였다. 첫 카드는 **4.533→5.156초로 0.623초 늦어졌다**. 전체 완료와 첫 표시의 차이를 함께 판단해야 한다.

처음부터 5명씩 묶었던 초기 후보 `bdd341bd`는 별도의 cold 5회에서 첫 카드 중앙값이 10.896초로 늦어졌다. 이를 확인해 첫 사람을 먼저 표시하도록 조정한 최종 후보 `f0034d20`을 다시 측정했다. 초기 후보의 5 cold·18 warm 기록은 최종 표와 CSV에 섞지 않았다.

800ms 전체 프로토콜의 실제 검색 요청은 **859→246**, 모든 upstream 요청은 **1,145→547**이었다. CSV의 단계별 요청 합계는 1,119→523이다. 단계 계측 사이에 시작한 부속 요청 26/24건이 있어 전체 수치는 서버의 전체 요청 기록으로 별도 집계했다. 요청 수는 물리적인 page 호출이며 캐시 membership 개수와 다르다. 검색 요청 수에 설정 지연을 곱한 명목 지연량은 687.2→196.8초지만, 이 산식은 측정한 UI 시간이나 queue-only 시간이 아니다.

첫 24조작의 완료시간 합계는 213.761→108.528초였다. 반복 여정 2~10의 합계 중앙값은 35.283→31.786초, p95는 38.816→36.141초였다. 조작 사이 계측 시간을 제외한 합계이며, 반복 여정에도 새 사람과 처음 방문하는 기간 조합이 포함된다. 전체 완료 기준으로는 첫 cold 조회부터 80.020초를 절감해 손익분기를 넘었다. 첫 카드 기준 이득은 확인하지 못했다.

warm reload 20회의 첫 두 번은 사진·사용자 보강 작업이 남아 tail이 길었다. baseline의 12/8/2건, candidate의 12/7/1건 추가 요청은 avatar와 사용자 정보였고 JQL 검색은 모두 0건이었다. 이 표본을 제거하지 않았다. 이미 검색이 warm인 Task 재필터에서도 약 3초대의 로컬/API/모델·렌더 비용이 남으며, 원인을 특정하려면 별도 프로파일링이 필요하다.

Task→워크로드의 15개 검색과 워크로드 완료 4주의 30개 검색은 그대로 남았다. 다른 화면이 조회하지 않은 해결일 없는 Done 등의 조건은 여전히 확인해야 한다. 기존 캐시 공유는 유지되며, 새로 필요한 조건의 묶음 조회가 추가 비용을 줄인다. WBS Epic·SubTask 펼치기에는 양쪽 모두 추가 검색이 없었다.

아래는 원래 내부 조작 이름으로 기록했다. 10회 조작 분포에는 첫 방문과 반복 방문이 함께 포함되므로 첫 순회 수치도 별도 열로 제공한다. 각 단계의 filter, 결과/집계 digest, 동일 여부, 시간, 요청, long task, 단계 끝의 pending 요청은 [336쌍 원본 집계 CSV](jql-strategic-cache-ui-20261104.csv)에서 확인할 수 있다.

### Task 진입 — 800ms, 초 단위 p50 / p95

| 조건 | N | 첫 카드 baseline | 첫 카드 candidate | 완료 baseline | 완료 candidate | 검색 페이지 합계 baseline → candidate |
|---|---:|---:|---:|---:|---:|---:|
| entry-cold | 5 | 4.533 / 4.884 | 5.156 / 5.487 | 120.037 / 120.129 | 40.017 / 40.471 | 535 → 105 |
| entry-warm | 20 | 0.509 / 6.094 | 0.552 / 6.039 | 4.072 / 9.579 | 3.393 / 8.908 | 0 → 0 |

### 24개 조작별 전체 완료 — 800ms, 각 N=10

| 조작 | baseline p50 / p95(초) | candidate p50 / p95(초) | 첫 순회 baseline → candidate(초) | 첫 순회 검색 baseline → candidate |
|---|---:|---:|---:|---:|
| done-month | 4.181 / 22.483 | 3.658 / 7.607 | 37.358 → 10.129 | 32 → 6 |
| allocated-2w | 3.841 / 21.610 | 3.343 / 6.558 | 36.098 → 8.986 | 32 → 6 |
| progress-month | 3.898 / 21.511 | 3.389 / 6.595 | 35.817 → 8.992 | 32 → 6 |
| assignee-scope | 0.175 / 0.196 | 0.189 / 0.205 | 0.176 → 0.169 | 0 → 0 |
| assignee-other | 1.538 / 2.391 | 1.555 / 2.476 | 3.073 → 3.178 | 0 → 0 |
| reporter-scope | 0.172 / 0.183 | 0.167 / 0.192 | 0.181 → 0.149 | 0 → 0 |
| reporter-other | 1.519 / 1.528 | 1.564 / 1.628 | 1.517 → 1.558 | 0 → 0 |
| module-workbench | 1.524 / 12.948 | 1.447 / 4.249 | 22.265 → 6.408 | 22 → 3 |
| module-etl | 3.778 / 3.846 | 3.375 / 3.457 | 3.713 → 3.236 | 0 → 0 |
| allocated-all | 4.048 / 4.157 | 3.585 / 3.789 | 3.931 → 3.366 | 0 → 0 |
| progress-all | 4.137 / 4.341 | 3.581 / 3.684 | 4.007 → 3.474 | 0 → 0 |
| done-week | 3.770 / 3.917 | 3.284 / 3.363 | 3.689 → 3.133 | 0 → 0 |
| workload-after-task | 0.106 / 7.035 | 0.099 / 6.993 | 12.702 → 12.624 | 15 → 15 |
| workload-module-workbench | 0.070 / 9.035 | 0.076 / 6.406 | 12.299 → 7.466 | 12 → 6 |
| workload-module-etl | 0.123 / 0.158 | 0.126 / 0.135 | 0.182 → 0.137 | 0 → 0 |
| workload-month | 0.156 / 0.434 | 0.163 / 0.523 | 0.425 → 0.506 | 0 → 0 |
| workload-all | 0.172 / 0.244 | 0.171 / 0.185 | 0.139 → 0.123 | 0 → 0 |
| workload-done-week | 0.173 / 0.202 | 0.170 / 0.185 | 0.049 → 0.046 | 0 → 0 |
| workload-done-month | 0.169 / 13.696 | 0.169 / 13.708 | 24.757 → 24.771 | 30 → 30 |
| wbs-after-workload | 0.100 / 4.944 | 0.099 / 4.137 | 8.904 → 7.432 | 8 → 6 |
| wbs-expand | 0.973 / 1.356 | 0.984 / 1.459 | 1.644 → 1.818 | 0 → 0 |
| wbs-subtasks | 0.328 / 0.473 | 0.333 / 0.495 | 0.557 → 0.512 | 0 → 0 |
| wbs-collapse | 0.154 / 0.165 | 0.162 / 0.180 | 0.164 → 0.183 | 0 → 0 |
| task-return | 0.107 / 0.123 | 0.109 / 0.129 | 0.113 → 0.132 | 0 → 0 |

### 첫 카드/행 관측 — 800ms, 각 N=10

| 조작 | baseline p50 / p95(초) | candidate p50 / p95(초) |
|---|---:|---:|
| done-month | 0.140 / 0.634 | 0.136 / 0.195 |
| allocated-2w | 0.117 / 0.207 | 0.121 / 0.213 |
| progress-month | 0.108 / 0.308 | 0.108 / 0.213 |
| assignee-scope | 0.096 / 0.142 | 0.091 / 0.142 |
| assignee-other | 1.532 / 2.391 | 1.538 / 2.456 |
| reporter-scope | 0.073 / 0.138 | 0.072 / 0.127 |
| reporter-other | 1.519 / 1.528 | 1.564 / 1.628 |
| module-workbench | 0.106 / 0.157 | 0.110 / 0.178 |
| module-etl | 0.105 / 0.124 | 0.103 / 0.126 |
| allocated-all | 0.116 / 0.234 | 0.115 / 0.236 |
| progress-all | 0.111 / 0.307 | 0.117 / 0.328 |
| done-week | 0.105 / 0.208 | 0.103 / 0.203 |
| workload-after-task | 0.106 / 0.786 | 0.099 / 0.724 |
| workload-module-workbench | 0.068 / 0.076 | 0.073 / 0.081 |
| workload-module-etl | 0.123 / 0.158 | 0.126 / 0.135 |
| workload-month | 0.153 / 0.179 | 0.159 / 0.178 |
| workload-all | 0.172 / 0.244 | 0.171 / 0.185 |
| workload-done-week | 0.173 / 0.202 | 0.170 / 0.185 |
| workload-done-month | 0.169 / 2.002 | 0.169 / 2.052 |
| wbs-after-workload | 0.100 / 4.944 | 0.099 / 4.137 |
| wbs-expand | 0.964 / 1.000 | 0.968 / 1.013 |
| wbs-subtasks | 0.328 / 0.473 | 0.333 / 0.495 |
| wbs-collapse | 0.154 / 0.165 | 0.162 / 0.180 |
| task-return | 0.107 / 0.123 | 0.109 / 0.129 |

### 역방향 — 800ms, 각 N=1

| 조작 | 첫 콘텐츠 baseline → candidate(초) | 완료 baseline → candidate(초) | 검색 baseline → candidate |
|---|---:|---:|---:|
| reverse-workload-cold | 24.350 → 10.365 | 102.388 → 69.562 | 75 → 39 |
| reverse-task-after-workload | 0.399 → 0.756 | 57.663 → 22.647 | 52 → 12 |
| reverse-wbs-after-task | 8.616 → 7.085 | 8.616 → 7.085 | 8 → 6 |
| reverse-workload-return | 0.088 → 0.095 | 0.088 → 0.095 | 0 → 0 |

### 0 / 250ms 탐색 비교 — cold N=1, warm N=3, 여정 N=1

| 지연 | cold 첫 카드 baseline → candidate(초) | cold 완료 baseline → candidate(초) | cold 검색 baseline → candidate | warm 완료 중앙값 baseline → candidate(초) | 24조작 합계 baseline → candidate(초) |
|---:|---:|---:|---:|---:|---:|
| 0ms | 1.060 → 1.663 | 20.226 → 10.009 | 107 → 21 | 3.943 → 3.839 | 67.244 → 44.165 |
| 250ms | 2.435 → 2.787 | 49.575 → 16.915 | 107 → 21 | 5.669 → 3.502 | 111.576 → 59.903 |

0ms의 단일 순회에서는 Task→워크로드가 3.109→3.759초로 늘었다. 지연 없는 모든 동작의 개선을 확인한 결과는 아니며, 개별 수치는 CSV에 그대로 남겼다.

### 내부 캐시 접근 카운터 — 800ms 전체 실행

| 종류 | baseline hit / miss | candidate hit / miss |
|---|---:|---:|
| jqlleaf | 17,769 / 1,736 | 27,120 / 1,834 |
| jqlrow | 285,105 / 0 | 401,615 / 0 |
| issueL | 18,014 / 83 | 17,798 / 60 |
| issue | 1 / 6,224 | 0 / 5,978 |
| user | 398 / 198 | 387 / 209 |
| childkeys | 731,917 / 6,194 | 229,332 / 6,194 |
| epicmeta | 12,329 / 204 | 5,105 / 173 |

내부 readiness 검사와 반복 조회도 세므로 사용자/API 요청의 hit ratio가 아니다. 사용자 체감과 Jira 호출 절감은 위 UI 시간·실제 upstream 요청 수로 판단한다.

### 정합성과 남은 비용

- 800ms 270쌍, 0ms 33쌍, 250ms 33쌍의 **336쌍 전부**에서 filter key, issue key 집합과 순서, 통계, 인력별 집계, WBS 트리 목록이 같았다. 예외적인 비교 누락은 없다.
- 모든 측정에서 uncaught JavaScript 예외(`pageerror`), WBS partial, 인력 오류, 실제 fixture 티켓·검색 실패는 없었다. 기본 harness의 `consoleErrors` 필드명은 `pageerror` 수집을 뜻하며 전체 console 로그 수집을 뜻하지 않는다.
- 800ms 원시 provider 응답에는 합성 사진이 없는 avatar 404가 baseline 61건/candidate 65건 있었다. 양쪽의 `/issue/__filter__` 404 각 1건은 사용자 필터의 context sentinel을 티켓처럼 조회하는 기존 경로다. 정상 fixture 티켓 실패와 분리했으며 sentinel의 불필요한 조회는 후속 개선 대상이다.
- 최종 후보의 별도 800ms 취소 시나리오에서 새 filter token과 정확한 Workbench 목록으로 전환했고, ETL로 돌아왔을 때 전환 전에 완료된 검색은 다시 호출하지 않았다. 일부 진행 중인 작업은 큐에 남아 새 카드까지 10.166초가 걸렸다. 즉시 필터를 바꾸는 것과 네트워크 결과가 즉시 나오는 것은 구분한다.
- 지연 없는 실제 UI에서 summary·본문·상태 변경 후 새 값과 카드 잔존을 확인했다. 임의 OR JQL의 조건 순서를 바꿔도 같은 결과를 반환하고 추가 검색은 0건이었다. 이후 부속 요청을 마친 Epic 표시는 `DL-101 성능 Epic`, `DL-102 성능 Epic`으로 유지됐다.
- 위 두 별도 시나리오에서는 전체 console error도 수집해 0건을 확인했다. 브라우저 request failure는 취소 시나리오 6건, 수정·검색 시나리오 7건으로 모두 의도적인 `net::ERR_ABORTED`였다. 사진의 provider 404와 이 브라우저 취소는 별도 계층의 사건이다.
- 800ms 전체의 50ms 이상 long task는 **189→199건**, 최댓값은 **205→241ms**였다. main-thread 비용 감소를 확인한 결과는 아니다. API/JQL 호출 감소와 별개로 추가 프로파일링할 부분이다.
- 상대 시각·캐시 수명을 고정한 비교이므로 장시간 실제 TTL 만료, 실 Jira 검색 CPU, 인증 유실, SSO 복구의 운영 성능을 검증했다는 뜻은 아니다. 해결일 없는 Done 조건의 기능적 회귀는 단위 테스트에서 확인했으며 이 UI fixture에 해결일이 비어 있는 Done 행을 따로 추가한 것은 아니다.

## 정합성·리뷰

- 정규화 동치 쿼리, warm-first 진행 표시, 그룹 실패·pagination 실패, 중복 row·total 변경, narrow/full-light projection, mutation 경합과 frozen cursor를 관련 회귀로 검증했다.
- 실제 MCP SDK를 통한 검색·UI 캐시 재사용·수정 후 재조회와 이전 snapshot의 불변성을 확인했다.
- 리뷰에서 확인한 표시 이름의 잘못된 빈 membership, WBS stale fallback 우회, 누락 필드 응답의 canonical 캐시 덮어쓰기에 별도 회귀를 추가했다.
- 테스트 수와 남은 제약은 아래 리뷰·회귀 검증 부록에 기록했다.

## 재현

`tools/jql_ui_benchmark.py`와 `tools/jql_ui_benchmark_fixture.py`는 mock 환경에만 계측을 주입한다. 실행별 설정·캐시·로그는 `.cache/ui-jql/` 아래에 격리하고 포트 4457을 사용한다. 운영 설정과 실행 중인 운영 Jira 세션은 수정하지 않는다.

측정 코드 `d1605d4a`, baseline `2fdb8feb8d18f3d8852ae3d1bcda54f058a332e8`, candidate `f0034d204042403710637f8a1ae085d82b1847f5`를 각각 고정한다. 한 번에 하나의 서버만 4457에서 실행한다.

```powershell
python tools/jql_ui_benchmark.py --repo <비교할-worktree> --run .cache/ui-jql/<고유-실행명> --latency 800 --cold 5 --warm 20 --journeys 10 --reverse --keep-server
```

0/250ms는 `--cold 1 --warm 3 --journeys 1 --reverse`를 사용했다. 추가 정합성은 `tools/jql_ui_edge_scenarios.py`의 `--rapid`(800ms), `--mutation --generic`(0ms)로 실행했다. 전체 console·취소 event와 Epic 이름 검사는 임시 관측 wrapper에서 추가했다. 측정 버전의 dev quit은 서버를 종료하지 않아 PID·명령행으로 소유권을 확인한 watcher/worker만 중지했다. 원시 로그·실행 디렉터리와 baseline worktree는 보고서와 CSV 검증 후 정리했으며, 운영 설정은 변경하지 않는다.

측정 harness 파일 SHA-256:

- `jql_ui_benchmark.py`: `a343fa854ebd11de65b8c6386d6787961457ce086dede5f35979f948ddd4399b`
- `jql_ui_benchmark_fixture.py`: `71b846e1ff187211ef20a874594bcc3736971497c3af09cf6524ccbf62bed634`
- `jql_ui_edge_scenarios.py`: `8f6433a8b1276cf9c52f20bc34f847e14882a53edd0689c93f09304c4bcc0a47`

## 리뷰·회귀 검증 부록

### 발견 사항과 조치

| 검토 항목 | 발견한 문제 | 조치와 확인 근거 |
|---|---|---|
| 1. 독립적인 membership 기준 | 그룹 조회와 개별 조회가 같은 client/cache를 사용하면 잘못된 분할 결과를 양쪽에서 읽어도 비교를 통과할 수 있었다. | 별도 JiraClient와 독립 메모리 캐시에서 `_legacy_search`로 직접 조회한다. 세 사람 × 담당/보고 × 세 상태의 **18개 keyset**을 각각 그룹 membership과 비교하고, 그룹 client에서 재조회할 때 실제 Jira search가 **0회**인지 확인한다. 전체 모델도 이 독립 경로와 대조한다. [테스트](../../../tests/jira/workload/test_mytasks_query_batches.py#L141) |
| 2. 필터 전환·취소 | 첫 배치가 존재하지 않는 사용자들의 빈 결과여서, 완료된 실제 티켓이 새 필터에서 재사용되는지 증명하지 못했다. | 교육 fixture의 첫 담당자 한 명을 먼저 조회한다. `DL-9103`을 포함한 응답을 받은 뒤 중단하고 해당 담당자 필터를 연다. 독립 기준과 티켓·통계가 같고 membership 재조회가 없음을 확인한다. 추가 검색은 **이전 응답에 없었던 필요한 부모 키**의 `key IN (...)` 보강만 허용하며, 이미 반환된 티켓 재조회는 실패 처리한다. [테스트](../../../tests/jira/workload/test_mytasks_query_batches.py#L212) |
| 3. 누락 필드 응답 | 분할만 막아도, 실제 HTTP 응답의 reporter/Epic 선택 필드가 누락된 row가 기존 정상 issue 또는 공유 projection을 덮어쓸 수 있었다. fetch helper를 대체한 테스트만으로는 이 경로를 검증할 수 없었다. | 실제 provider의 search 응답에서 필드를 제거한다. 기존 정상 row 유지, 초기 캐시가 없는 경우의 오염 방지, 다른 쿼리의 ready 판정, 동일 쿼리·snapshot의 불완전 row 재표시를 검증한다. 불완전 결과는 해당 쿼리 범위에 보관하고, 명시적인 `null`과 누락을 구분한다. [provider 경계 테스트](../../../tests/jira/query/test_jql_partition_identity.py#L35), [projection 검사](../../../app/jira/jql_execution.py#L32) |
| 4. 사용자 별칭 | `assignee = "John Smith" OR reporter = bob`처럼 표시 이름과 계정 ID가 섞이면, reporter에 걸린 row가 있다고 해서 표시 이름 조건을 빈 결과로 확정할 수 없다. | 실제 응답의 `name` 또는 확인된 사용자 캐시로 증명된 ID만 개별 membership을 유도한다. 확인되지 않은 별칭은 `None`으로 남겨 빈 membership을 저장하지 않으며, 전체 그룹 결과는 유지한다. [분할 정책](../../../app/jira/jql_execution.py#L136), [회귀](../../../tests/jira/query/test_jql_partition_identity.py#L13) |
| 5. 부모 역할 표시 | 독립 비교에서 `DL-9012`의 역할이 그룹 조회에서는 담당자로, 개별 조회에서는 `None`으로 나타났다. SubTask가 먼저 도착하면 역할이 없는 context 부모로 그룹을 만들고 그대로 유지하던 기존 순서 의존 버그였다. | 그룹 역할을 임시 node의 `role` 값 대신 실제 담당자·보고자 ID로 계산한다. 순서와 무관하게 전체 모델 비교가 일치한다. [수정](../../../app/domain/mytasks.py#L868), [독립 모델 비교](../../../tests/jira/workload/test_mytasks_query_batches.py#L141) |
| 6. 느린 그룹과 상대 날짜 | 긴 조회 중 TTL 버킷을 넘으면 뒤쪽 그룹의 상대 날짜가 앞쪽과 달라질 수 있었다. | 계획 시각을 한 번 정하고 모든 그룹을 그 시각에 compile한 canonical JQL로 실행한다. fake clock으로 그룹 사이에 한 시간씩 경과시켜도 실행 조건이 바뀌지 않는지 확인한다. [계획 시각](../../../app/domain/mytasks.py#L525), [회귀](../../../tests/jira/workload/test_mytasks_query_batches.py#L110) |
| 7. WBS의 stale 표시 | 사전 배치 조회가 살아 있는 stale 관계 캐시보다 먼저 실행되면, 기존 오프라인 표시 경로를 우회할 수 있었다. | 표시 가능한 관계 캐시가 있는 Epic은 강제 사전 조회 대상에서 제외한다. 네트워크 실패 시 이전 롤업 유지와 빈 Epic의 Agile 대체 조회를 각각 확인한다. [stale 회귀](../../../tests/jira/workload/test_wbs_query_batches.py#L103), [빈 Epic 회귀](../../../tests/jira/workload/test_wbs_query_batches.py#L68) |


8. **별칭 테스트의 unknown/empty 구분:** 별칭의 membership을 `None`으로 남기는 수정 후 `len(None)`을 호출하던 과거 테스트도 고쳤다. unknown은 결과 없음과 다르다는 점을 직접 검증한다. [테스트](../../../tests/jira/query/test_jql_execution_plan.py#L40).

9. **변경 크기:** 실행 계층과 순수 계획이 섞인 초기 변경은 800행을 넘었다. 계획 파일을 분리하고 실행·MyTasks·대시보드를 의존 순서 PR로 나눴다. [계획 계층](../../../app/jira/jql_execution.py#L1).

10. **중간 버전의 스트림 호환성:** 새 메시지의 생산자만 먼저 반영하면 기존 MyTasks 소비자가 진행률을 잘못 계산할 수 있었다. 두 쪽을 같은 커밋 `429adbaa`에 적용했다. [생산자](../../../app/jira/jira_client.py#L1182), [소비자](../../../app/domain/mytasks.py#L568).

11. **계측 기록의 동시성:** 요청 완료 정보가 metrics 복사 중 바뀌면 계측 자체가 실패할 여지가 있었다. 실측 중에는 발생하지 않았으며, 원본 `d1605d4a`를 보존한 뒤 `f1428fd0`에서 요청 완료 정보를 lock 안에서 한 번에 기록하도록 보강했다. [수정](../../../tools/jql_ui_benchmark_fixture.py#L152).

12. **측정 서버 종료:** dev 모드의 일반 quit API는 watcher를 종료하지 않았다. 실측에서는 검증한 소유 PID만 정리했고, 이후 도구는 named watcher를 직접 소유해 그 프로세스 트리만 종료하도록 바꿨다. 0ms 실제 실행·종료와 시작/metadata 실패·attach 비소유 경로를 검증했다. [수정](../../../tools/jql_ui_benchmark.py#L73). 새 종료 경로로 기존 성능 표본을 재측정했다고 주장하지 않으며, 실측 원본은 위 revision으로 재현할 수 있다.

### 함께 확인하는 캐시 계약

- 정상 빈 결과와 실패를 구분한다. 그룹 인증·전송 오류가 발생해도 개별 leaf 수만큼 재시도를 확대하지 않는다. [그룹 실패](../../../tests/jira/query/test_jql_execution.py#L80)
- 뒤쪽 페이지 실패, total 변경, 중복 key는 완전한 membership이나 canonical issue 캐시로 발행하지 않는다. [pagination 실패](../../../tests/jira/query/test_jql_execution.py#L138)
- mutation 전 시작한 그룹의 늦은 결과가 변경 후 캐시를 덮어쓰지 못하며, 캐시된 그룹에서 membership을 유도해도 최초 수집 시각을 유지한다. [mutation fence](../../../tests/jira/query/test_jql_execution.py#L124), [수집 시각](../../../tests/jira/query/test_jql_execution.py#L159)
- MCP 임의 OR 검색은 한 번의 전체 조회를 캐싱하고 UI와 재사용한다. 수정 후 새 조회는 최신 membership을 보지만 기존 pagination snapshot은 그대로 유지한다. [실제 MCP SDK 회귀](../../../tests/mcp/test_query_strategy.py#L7)
- 계획 단계의 일시적인 인증 실패가 실행 전에 복구되면 실제 leaf 수로 진행률 총계를 보정한다. [회귀](../../../tests/jira/workload/test_mytasks_query_batches.py#L88)

### 검증 결과의 범위

GitHub Actions의 외부 API 없는 전체 회귀도 네 단계 모두 통과했다.

| PR | 내용 | 통과 | 건너뜀 | CI |
|---|---|---:|---:|---|
| [#117](https://github.com/dongyi-kim/lake-task-manager/pull/117) | 순수 요청 계획 | 4,141 | 74 | [결과](https://github.com/dongyi-kim/lake-task-manager/actions/runs/41789287271) |
| [#118](https://github.com/dongyi-kim/lake-task-manager/pull/118) | 실행·캐시·MCP | 4,156 | 74 | [결과](https://github.com/dongyi-kim/lake-task-manager/actions/runs/41789291852) |
| [#119](https://github.com/dongyi-kim/lake-task-manager/pull/119) | MyTasks 스트리밍·첫 표시 조정 | 4,174 | 74 | [결과](https://github.com/dongyi-kim/lake-task-manager/actions/runs/41793689262) |
| [#120](https://github.com/dongyi-kim/lake-task-manager/pull/120) | workload·WBS 통합 최종 후보 | 4,182 | 74 | [결과](https://github.com/dongyi-kim/lake-task-manager/actions/runs/41793688476) |

초기 통합 후보 **`bdd341bd`의 관련 통합 회귀는 237개 통과(60.00초)**였다. 첫 표시를 조정한 최종 `f0034d20`은 MyTasks·프런트 계약 77개와 위 전체 CI 4,182개가 통과했다. 중간 실행에서의 **235개 통과·1개 실패**는 취소 후 모든 search 요청을 금지한 테스트가 새로 필요한 부모의 metadata 보강까지 금지한 데서 발생했다. 위 취소 계약으로 조건을 정확히 수정한 뒤 MyTasks 관련 두 파일 **63개 통과**를 확인하고, 이후 최종 관련 통합 대상을 다시 실행한 결과가 237개 통과다. 공통 실행 계층까지만 적용한 중간 단계의 관련 검증은 **143개 통과**였다. 이 수치는 저장소 전체 CI 결과나 UI 성능 측정 횟수가 아니다.

일반 회귀는 고의 지연 없이 실행했고, 시간 경계는 fake clock으로 검증했다. 실제 UI 성능 수치는 본문의 통제된 benchmark 결과와 구분한다. 이번 부록은 운영 Jira·SSO의 장시간 실사용 검증을 의미하지 않는다.

### 변경 크기와 병합 단위

리뷰에서 큰 혼합 변경을 피하도록 제품 변경을 네 개의 의존 순서로 나눴다. 각 단위는 추가·삭제를 합쳐 **800행 미만**이며, 새 스트림 메시지 형식의 producer와 consumer를 별도 단계에 나누지 않았다. 측정 도구·보고서는 제품 변경과 별도로 다룬다.

| 커밋 | 책임 | 추가·삭제 합계 |
|---|---|---:|
| `cdc108a2` | 재사용 membership 판별과 제한된 요청 그룹 계획 | 242행 |
| `f682c638` | 공통 검색·MCP 실행, 캐시 보호와 강제 background 분해 제거 | 605행 |
| `429adbaa` + `0dbf1c86` | MyTasks 그룹 스트림 producer·consumer, 첫 표시 조정과 관련 회귀 | 614행 |
| `bdd341bd` | 워크로드·WBS의 공통 그룹 캐시 활용 | 196행 |

### 남은 해석상 주의점

- 개인·기간 범위의 캐시만으로 전체 Epic 자식 membership을 증명할 수는 없다. 반대로 특정 Epic 캐시가 모든 개인의 업무 목록을 완성하지도 않는다. 다른 화면의 모든 Jira 호출이 없어지는 것을 성공 조건으로 삼지 않는다.
- 캐시된 티켓의 목록 재사용과 아직 받지 못한 부모·Epic metadata 보강을 분리해서 센다. 후자는 필터를 바꾸면서 필요한 정상 요청일 수 있다.
- 식별자를 증명할 수 없는 사용자 별칭과 누락 필드 응답은 개별 membership 재사용을 줄이는 대신 잘못된 빈 결과나 다른 조회의 row 오염을 방지한다.
- 실제 Jira가 동일 검색의 페이지 사이에서 데이터를 바꾸는 경우 이번 검증은 불완전 결과 발행을 막는 계약을 확인한다. 외부 수정 중 모든 조회가 즉시 성공한다는 보장은 하지 않는다.

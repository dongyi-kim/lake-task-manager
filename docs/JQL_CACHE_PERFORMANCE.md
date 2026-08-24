# JQL 분해 캐시 성능·정합성 검증

## 조건

- baseline: `e5c4d07` (`origin/main`)
- candidate: `feature/jql-query-cache`
- 동일 mock world, `LAKE_MOCK_LATENCY_MS=0/250/800`
- API: 조건별 cold 5회, warm 20회
- UI: 실제 브라우저에서 Task → 인력 워크로드 → WBS → Task → 통합검색과 Task 퀵필터 조작
- 결과 비교: key, 순서, total, mutation 직후 summary

임시 DB와 원시 결과는 worktree의 `.cache`에서만 만들었고 수치 정리 후 제거한다.

## API 결과

단위는 ms다. `cold`는 평균, `warm`은 p50/p95다.

| 지연 | 시나리오 | baseline cold | candidate cold | baseline warm | candidate warm |
|---:|---|---:|---:|---:|---:|
| 250 | 동일 JQL 반복 | 1110.73 | 411.76 | 1081.35 / 1653.00 | 2.34 / 2.63 |
| 250 | AND 순서 변경 | 1032.91 | 403.89 | 1049.23 / 1390.13 | 2.36 / 2.70 |
| 250 | OR 순서 변경 | 573.70 | 713.86 | 578.04 / 787.56 | 1.28 / 1.48 |
| 250 | OR 뒤 단일 leaf | 589.27 | 717.94 | 563.89 / 725.64 | 1.34 / 54.24 |
| 800 | 동일 JQL 반복 | 1537.42 | 974.46 | 1385.20 / 2116.15 | 2.58 / 7.57 |
| 800 | AND 순서 변경 | 1525.91 | 947.12 | 1455.43 / 1961.53 | 2.33 / 3.26 |
| 800 | OR 순서 변경 | 1106.32 | 1782.82 | 1073.06 / 1294.42 | 1.30 / 3.90 |
| 800 | OR 뒤 단일 leaf | 1086.40 | 1780.69 | 1054.85 / 1227.05 | 1.60 / 40.31 |

- candidate의 20개 정상 warm 조회는 모든 시나리오에서 upstream 0회였다. baseline JQL은 20회였다.
- 기존 단건 issue 캐시는 그대로다. 250ms에서 warm p50은 baseline 0.11ms, candidate 0.12ms다.
- OR cold는 모든 leaf를 실제 실행하므로 250ms에서 약 140ms, 800ms에서 약 676ms가 추가된다.
  첫 재조회에서 약 577ms/1072ms를 절약하므로 두 지연 조건 모두 한 번 재사용되기 전에 손익분기점을 넘는다.
- AND/동일 쿼리는 batch write와 row dedup 덕분에 cold도 baseline보다 빨랐다.

## Mutation 직후 비용

| 지연 | 구분 | write p50 | 다음 신규 조회 p50/p95 | 최신값 |
|---:|---|---:|---:|---:|
| 250 | baseline | 271.99 | 263.61 / 281.16 | 5/5 |
| 250 | candidate | 300.35 | 328.56 / 357.54 | 5/5 |
| 800 | baseline | 820.94 | 816.37 / 834.43 | 5/5 |
| 800 | candidate | 841.97 | 876.24 / 901.44 | 5/5 |

candidate는 성공한 write 뒤 generation을 바꾸고 다음 조회를 의도적으로 한 번 miss시킨다. 250ms 기준
추가 비용은 약 65ms이며 이후 동치 조회는 다시 2~3ms다. 실패한 write는 generation과 정상 캐시를
유지하고, write 이전 SWR producer는 generation fence 때문에 낡은 값을 되살리지 못한다.

## 실제 UI 여정

250ms 지연에서 서버 캐시를 명시적으로 비운 뒤 측정했다.

| 화면 완료 지점 | baseline | candidate |
|---|---:|---:|
| Task 첫 `DL-9100` 카드 | 9187 | 9247 |
| Task → 워크로드 첫 티켓 | 5843 | 5371 |
| 워크로드 → WBS 간트 | 10565 | 10711 |

대규모 DNF를 요청 스레드에서 직렬 실행한 초기 구현은 Task cold가 21.5초였지만, 직렬 provider의
첫 snapshot bootstrap과 background leaf warming으로 9.25초까지 낮춰 baseline 9.19초와 같아졌다.
background 요청은 사용자 조회·쓰기에 양보한다.

Task 퀵필터는 다음 순서로 실제 버튼을 조작했다.

1. 할당됨 `모두 → 2주 내 갱신 → 모두`
2. 진행 중 `모두 → 1달 내 갱신 → 모두`
3. 최근 완료 `1주 → 1달 → 1주`

| 변경 | baseline | candidate |
|---|---:|---:|
| 할당됨 2주 | 457 | 428 |
| 할당됨 모두 | 448 | 465 |
| 진행 중 1달 | 421 | 491 |
| 진행 중 모두 | 463 | 419 |
| 최근 완료 1달 | 454 | 422 |
| 최근 완료 1주 | 459 | 420 |

첫 여정 p50은 baseline 455.5ms, candidate 425.0ms다. 브라우저 조작 오버헤드가 큰 구간이라 차이는
작지만 회귀는 없었다. 세 필터를 대기 없이 연속 변경한 candidate 시나리오는 1094ms에 끝났고,
최종 `2주/1달/1달` 세 버튼이 모두 active였다. 지나간 응답이 최신 선택을 덮지 않았다.

추가로 `Workbench → TEST → Workbench`, 정렬 `마감 → 우선순위 → 마감`, DL-9100의 14개
SubTask 접기/펼치기와 다이어로그 열기, `/` 통합검색을 수행했다. 다이어로그는 399ms에 골격을
보였고, 상세·SubTask가 로드된 뒤 타임라인 지연과 무관하게 필드 컨트롤이 활성 상태였다.

## 정확성 gate

- baseline/candidate 검색 비교 480건: key·순서 일치, 불일치 0
- mutation 30회: 다음 신규 조회 최신값 30/30
- 정상 warm JQL: upstream 0회
- snapshot cursor: mutation 전 snapshot은 같은 generation으로 중복·누락 없이 계속 읽힘
- full issue → light 재사용 허용, light → full 재사용 금지
- focused regression: 896 passed, 2 skipped. 전체 실행에서는 2494개가 통과한 뒤 테스트가 `.cache`를
  지우면서 pytest 자체 basetemp도 지우는 기존 충돌이 발생해, 관련군과 local parity를 분리 재검증했다.

# JQL 분해 캐시 성능·정합성 검증

## 조건

- baseline: `e5c4d07` (`origin/main`)
- candidate: `feature/jql-query-cache`
- 동일 mock world, `LAKE_MOCK_LATENCY_MS=0/250/800`
- API: 조건별 cold 5회, warm 20회
- UI: 실제 브라우저에서 Task → 인력 워크로드 → WBS → 통합검색 → Task를 왕복하고,
  담당자·보고자·모듈·기간·그룹화·SubTask 보기·정렬 퀵필터를 반복 조작
- 결과 비교: key, 순서, total, mutation 직후 summary
- UI upstream 수치는 임시 launcher가 in-process Jira provider의 실제 호출을 센 값이다. launcher는
  `.test/ui-cache-matrix`, worktree와 측정 DB는 `.cache` 아래에서만 사용하고 결과 정리 후 제거한다.

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

## 실제 UI 여정 — 대량 퀵필터

250ms 지연에서 같은 브라우저와 mock world를 사용했다. 각 버전에서 서버 캐시를 비우고 문서까지
새로 로드한 뒤 Task에서 다음 47개 조작을 수행했다.

- 담당자와 보고자 FieldEdit에서 `test.ui01 ↔ test.ui02`를 실제 추천 목록으로 선택
- `담당자 → 보고자 → 모듈`, `Workbench → TEST → ETL → Workbench → TEST → Workbench`
- 할당 `모두 ↔ 2주`, 진행 `모두 ↔ 1달`, 완료 `1주 ↔ 1달`을 3회 교차 반복
- 그룹화 `없음 ↔ Sub Task`, SubTask 보기 `접기 → 전체 → 내 티켓`, 정렬
  `마감 → 우선순위 → Epic → 마감`을 2회 반복

단위는 ms다.

| Task 구간 | baseline | candidate | 변화 |
|---|---:|---:|---:|
| cold 첫 카드 | 5134.6 | 6334.1 | +23.4% |
| 담당자·보고자·모듈 p50 / p95 | 720.8 / 5082.1 | 676.1 / 4992.3 | -6.2% / -1.8% |
| 세 기간 필터 18회 p50 / p95 | 341.5 / 359.7 | 324.8 / 377.1 | -4.9% / +4.8% |
| 그룹화·보기·정렬 16회 p50 / p95 | 136.1 / 185.3 | 132.8 / 168.8 | -2.4% / -8.9% |

candidate cold는 모든 OR leaf를 빠짐없이 채우는 비용 때문에 느리다. 첫 대량 필터 여정의 실제
upstream도 baseline `273(검색 53, issue 211)`에서 candidate `469(검색 112, issue 348)`로 늘었다.
이 단계는 캐시 구축 비용이며 숨기지 않는다.

반대로 서버 캐시는 유지하고 브라우저 문서만 새로 열어 같은 scope·module·period 14개 조작을
재실행하면 다음과 같다. 이 측정은 프론트 메모리 캐시가 아닌 API/JQL 캐시 효과를 분리한다.

| warm API 구간 | baseline | candidate | 변화 |
|---|---:|---:|---:|
| 새 브라우저 Task 완료 | 2676.8 | 446.8 | -83.3% |
| 필터 폭주 p50 / p95 | 309.0 / 2087.5 | 309.4 / 346.5 | +0.1% / -83.4% |
| 필터 폭주 평균 | 493.9 | 252.3 | -48.9% |
| 실제 upstream | 83 (검색 4, issue 77) | 52 (검색 0, issue 52) | -37.3% |

candidate의 기간별 최종 카드 수는 `97 → 76 → 110 → 143 → 156 → 125`로 선택 조합에 맞게
바뀌었고, 마지막 `모두/모두/1주`와 Workbench 상태로 복귀했다. 응답 순서가 최신 선택을 덮지 않았다.

## 실제 UI 여정 — 탭 간 캐시 재사용

Task에서 Workbench를 prime한 뒤 워크로드의 모듈 `Workbench/TEST/ETL`, 완료 기간 `1/2/4주`,
정렬 `이름/할당/완료`, VoC를 조작했다. 이어 WBS의 전체 Epic과 Epic 3개, SubTask가 있는 Task
2개를 펼치고, `/` 통합검색에서 `DL-5003`과 `Workbench`를 각각 두 번 검색한 후 Task로 돌아왔다.

| 교차 화면 구간 | baseline | candidate | 비고 |
|---|---:|---:|---|
| Task prime → Workbench 워크로드 | 4253.3 | 2494.4 | -41.4% |
| 위 구간 검색 upstream | 10 | 4 | 겹치는 leaf 재사용 -60.0% |
| 워크로드 2주 | 2063.2 | 1570.9 | -23.9% |
| 워크로드 4주 | 2066.5 | 1467.7 | -29.0% |
| 워크로드 ETL | 6026.1 | 3569.4 | -40.8% |
| Workbench 재방문 | 40.8 | 41.4 | 양쪽 모두 화면 캐시 hit |
| Workload → WBS | 7012.7 | 6903.5 | 사실상 동일 |
| WBS 추가 upstream | 검색 20 | 검색 20 | projection/JQL이 달라 안전한 공유 없음 |
| key 검색 첫 실행 | 1202.9 | 1127.0 | -6.3% |
| 제목 검색 첫 실행 | 912.3 | 1541.1 | +68.9% |
| 제목 검색 재실행 | 34.3 | 35.7 | 양쪽 모두 hit |
| 검색 → Task 복귀 | 3169.0 | 3134.3 | 사실상 동일 |

전체 혼합 왕복의 upstream은 baseline `322(검색 94, issue 223)`에서 candidate
`220(검색 64, issue 151)`로 31.7% 줄었다. candidate 쪽은 보수적으로 Workload의 TEST 전환을
한 번 더 포함했는데도 감소했다. Task leaf가 Workload 검색에는 재사용됐지만 WBS는 필요한 projection과
JQL이 달라 추가 검색 20회가 양쪽에서 같았다. projection이 다른 결과를 억지로 공유하지 않는 정확성
정책이 작동한 결과다.

candidate는 Task → Workload에서 검색 호출을 줄이는 대신 상세 issue 보강을 baseline 60회에서
75회 사용한 구간도 있었다. 전체 체감은 빨라졌지만 이 보강 호출은 후속 최적화 여지다. 반면 충분히
데운 뒤 새 브라우저에서 Task 필터를 재실행한 구간은 검색 upstream 0회였고 기존 issue 캐시까지
포함해 전체 upstream이 37.3% 감소했다.

## 정확성 gate

- baseline/candidate 검색 비교 480건: key·순서 일치, 불일치 0
- 실제 `/api/mytasks` 6개 조합(담당자·보고자·Workbench/TEST·세 기간 조합): group 수, total,
  done과 `key/status/kids/title` 해시가 모두 일치
- mutation 30회: 다음 신규 조회 최신값 30/30
- 정상 warm JQL: upstream 0회
- snapshot cursor: mutation 전 snapshot은 같은 generation으로 중복·누락 없이 계속 읽힘
- full issue → light 재사용 허용, light → full 재사용 금지
- focused regression: 896 passed, 2 skipped. GitHub의 전체 offline suite도 통과했다.

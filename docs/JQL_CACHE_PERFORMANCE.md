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
| 250 | 동일 JQL 반복 | 1110.73 | 646.94 | 1081.35 / 1653.00 | 2.39 / 2.74 |
| 250 | AND 순서 변경 | 1032.91 | 643.62 | 1049.23 / 1390.13 | 2.39 / 4.47 |
| 250 | OR 순서 변경 | 573.70 | 931.02 | 578.04 / 787.56 | 1.75 / 3.06 |
| 250 | OR 뒤 단일 leaf | 589.27 | 919.38 | 563.89 / 725.64 | 1.31 / 27.91 |
| 800 | 동일 JQL 반복 | 1537.42 | 1755.87 | 1385.20 / 2116.15 | 2.32 / 2.70 |
| 800 | AND 순서 변경 | 1525.91 | 1746.82 | 1455.43 / 1961.53 | 2.40 / 3.25 |
| 800 | OR 순서 변경 | 1106.32 | 2548.18 | 1073.06 / 1294.42 | 1.37 / 2.36 |
| 800 | OR 뒤 단일 leaf | 1086.40 | 2573.87 | 1054.85 / 1227.05 | 1.57 / 35.38 |

- candidate의 20개 정상 warm 조회는 모든 시나리오에서 upstream 0회였다. baseline JQL은 20회였다.
- 기존 단건 issue 캐시는 그대로다. 250ms에서 warm p50은 baseline 0.11ms, candidate 0.12ms다.
- isolated cold client는 사용자/권한 context 분리를 위해 `/myself`를 한 번 확인한다. 실제 앱은 부팅 때
  이미 이 값을 warm하므로 UI 여정에는 추가되지 않지만 위 API cold 수치에는 250/800ms 지연이 포함된다.
- OR cold는 사용자 context 확인과 모든 leaf 실행 비용이 추가된다. 대신 정상 warm은 1~4ms이고
  upstream 0회라 고의 지연이 클수록 반복 퀵필터/탭 왕복에서 손익분기점을 빠르게 넘는다.

## Mutation 직후 비용

| 지연 | 구분 | write p50 | 다음 신규 조회 p50/p95 | 최신값 |
|---:|---|---:|---:|---:|
| 250 | baseline | 271.99 | 263.61 / 281.16 | 5/5 |
| 250 | candidate | 278.80 | 286.33 / 309.49 | 5/5 |
| 800 | baseline | 820.94 | 816.37 / 834.43 | 5/5 |
| 800 | candidate | 835.87 | 839.16 / 850.79 | 5/5 |

candidate는 성공한 write 뒤 immutable snapshot generation만 바꾸며, leaf membership은 변경 필드에
의존하는 것만 만료한다. 위 `key = DL-9001` 시나리오는 key leaf를 그대로 재사용하고 무효화된 issue row
한 건만 배치 보강해 다음 조회 비용도 baseline에 근접했다. 이후 동치 조회는 다시 2~3ms다. 실패한
write는 generation과 정상 캐시를 유지하고, write 이전 SWR producer는 generation fence 때문에 낡은
값을 되살리지 못한다.

## 실제 UI 여정 — 여러 Task 연속 수정

250ms 지연에서 Task 탭으로 TEST 모듈의 disposable Task 3개를 실제 생성했다. Task/상세,
Workload, WBS, 통합검색을 먼저 순회해 같은 38개 leaf와 issue/detail 캐시를 prime한 뒤 다음 10개
성공 write를 연속 수행했다.

- Task A: 제목 변경, Open → In Progress → Resolved 전이
- Task B: 본문 변경, 댓글 추가, 댓글 수정
- Task C: 기한·우선순위·담당자 변경
- 매 단계에서 다이어로그 표시를 확인하고, 닫은 뒤 상태 컬럼 이동과 담당자 퀵필터 이탈/진입을 확인

비교 기준은 선택 만료 도입 직전 commit `cad75c2`이며, 두 실행 모두 cold prime 비용은 거의 같았다
(`158 calls / 84.0s upstream` 대 `157 calls / 83.2s upstream`). 아래는 prime 후 계측만 초기화해
mutation과 자동 재조회에 사용된 실제 in-process Jira 호출을 센 값이다.

| 여러 Task mutation 구간 | 전체 generation leaf | 선택적 issue/field 역인덱스 | 변화 |
|---|---:|---:|---:|
| 성공 write | 10 | 10 | 동일 |
| 전체 upstream | 256 | 226 | -11.7% |
| read | 246 | 216 | -12.2% |
| Jira search | 24 | 10 | -58.3% |
| issue/detail read | 221 | 205 | -7.2% |
| 누적 upstream 대기 | 111.29s | 94.98s | -14.7% |
| mutation UI p50 / p95 | 2082 / 2672ms | 2082 / 2672ms | 직접 상세 재조회가 지배해 동일 |

snapshot generation은 `10`번 모두 증가했다. 이것은 기존 cursor가 같은 row generation을 계속 읽게
하는 정합성 장치이며 leaf 폐기를 뜻하지 않는다. leaf payload는 issue key 배열만 저장하고,
`issue→leaf` 1495개와 `predicate field→leaf` 62개의 TTL edge를 만들었다. 설명/댓글처럼 assignee,
component leaf의 membership과 무관한 변경은 해당 leaf를 유지한다. 상태·담당자처럼 membership을
바꿀 수 있는 쓰기는 그 필드를 참조하는 활성 leaf만 지운다. 삭제는 issue 역인덱스로 실제 포함 중인
leaf만 지운다.

후속 실제 UI 검증 결과는 다음과 같다.

- Resolved Task는 이전 컬럼에서 사라지고 최근 완료에 새 제목으로 표시
- 담당자 `test.ui01 → test.ui02` 변경 후 UI01 퀵필터에서 즉시 사라지고 UI02에서 표시
- 세 Task를 닫고 다시 열어 제목·본문·댓글·상태·기한·우선순위·담당자 최신값 확인
- Workload → WBS → Task 복귀 후 Task 결과 유지, 통합검색도 세 티켓의 최신 row 반환
- keep-alive 통합검색이 같은 검색어의 이전 결과를 보존하던 별도 버그를 발견해, 팝업 재활성화 시
  소스 typeahead 캐시를 비우고 검색어는 유지한 채 재조회하도록 수정. 추가 제목 변경 후 이전 제목이
  사라지고 새 제목이 표시되는 것을 브라우저에서 재현 검증

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

## 실제 UI 여정 — Task Parent 우선·SubTask 비동기 동기화

Task API를 base와 Parent별 보강의 2단계로 나눴다. base는 JQL에 이미 포함된 Task/SubTask와 필요한
Parent만 `issueL` projection으로 조립하고, 동료 SubTask와 Epic label은 base 응답 직후 최대 2개씩
독립 요청한다. 후속 요청은 background upstream priority라 퀵필터의 새 base JQL이 먼저 큐를 잡는다.

800ms 고의 지연, 빈 프로세스 캐시의 TEST 담당자 모델을 직접 비교한 결과다.

| 구간 | 기존 전체 모델 | base-first | 변화 |
|---|---:|---:|---:|
| 첫 모델 응답 | 5447.6ms | 4102.9ms | -24.7% |
| 모든 SubTask·Epic 보강 완료 | 5447.6ms | 5744.2ms | +5.4% |

완전 완료 시간은 Parent별 요청 경계 때문에 소폭 늘지만 UI는 4.1초에 풀리고, 이후 그룹마다 카드와
진척률이 독립적으로 채워진다. 같은 지연의 실제 브라우저 TEST 화면에서는 base 확인 시 카드 26개와
동기화 중 그룹 2개가 먼저 보였고, 한 그룹이 완료돼도 다른 그룹은 계속 `동기화 중` 상태를 유지한 뒤
각각 완료됐다. 느린 그룹 하나가 전체 Task 화면의 loading/클릭을 붙잡지 않았다.

같은 브라우저에서 SubTask 동기화 도중 퀵필터를 `Workbench → DataOps → Workbench`로 바꿨다.

| 조작 | 관찰 |
|---|---|
| Workbench 선택 | 45ms에 선택 반영·새 loading 전환 |
| Workbench base | 카드 114개가 먼저 표시, 14개 Parent 그룹은 독립 보강 |
| 보강 중 DataOps 선택 | 47ms에 Workbench 카드를 치우고 DataOps loading으로 전환 |
| DataOps base | 1551ms, 카드 87개·13개 Parent 보강 시작 |
| 이전 응답 경합 후 | 선택은 DataOps 유지, Workbench key가 화면을 덮지 않음; 카드 104개·pending 0 |
| Workbench 재방문 | 173ms, loading 없이 캐시된 카드 113개 즉시 표시 |

필터별 성공 base는 도착 순서와 무관하게 SWR 캐시에 저장한다. 이미 시작된 옛 필터의 그룹 보강도
완료되면 그 필터 캐시에 합치되 현재 화면은 request sequence가 같은 경우에만 갱신한다. 아직 시작하지
않은 옛 보강은 중단해 새 필터에 양보한다. 따라서 새 필터 전환과 이전 필터 결과 보존을 동시에 만족한다.
브라우저 console error/warning은 0건이었다.

## 정확성 gate

- baseline/candidate 검색 비교 480건: key·순서 일치, 불일치 0
- 실제 `/api/mytasks` 6개 조합(담당자·보고자·Workbench/TEST·세 기간 조합): group 수, total,
  done과 `key/status/kids/title` 해시가 모두 일치
- mutation 30회: 다음 신규 조회 최신값 30/30
- 정상 warm JQL: upstream 0회
- snapshot cursor: mutation 전 snapshot은 같은 generation으로 중복·누락 없이 계속 읽힘
- full issue → light 재사용 허용, light → full 재사용 금지
- 선택 만료·검색·Task 비동기 UI focused regression 563 passed. 직전 PR head의 전체 offline suite는 3284 passed, 2 skipped였으며, 현재 변경의 전체 suite는 GitHub Actions에서 판정한다.

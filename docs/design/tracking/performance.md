# Tracking 성능 및 검증 결과

2026-11-16 · `feat/tracking` · 로컬 측정

아래 3회 반복 표는 초기 구현 `24c7b33c` 기준이다. 보고 UX/VOC 보완 후에는 같은 15명·100개 활성·9,900개 과거 티켓, 요청당 50ms 조건으로 월간을 1회씩 재검증했다. cold **4회·2.192초**, warm **1회·1.239초**, 다건 확장 미지원 cold **204회·17.768초**. 보완된 모듈 판정은 같은 가벼운 검색에 담당자 필드를 추가하며 별도 요청은 늘리지 않는다. 비교 사건 누락·추가 0. 최신 원시 결과는 `benchmark-report-ux.json`에 보존했다.

보고 화면 조작(고정·분류·강조·보고 복사·저장 근거 열기·보기 전환)은 실제 브라우저 검증에서 추가 Tracking API 요청 0회였다. 표시용 VOC 분류는 원본 이벤트·집계를 변경하지 않는다.

## 조건

- 15명, 활성 10 Epic + 30 Task + 60 Sub-Task, 과거 티켓 9,900개. 총 10,000개.
- 지연 착수·부분 보고·드문 댓글·장기 작업·완료 후 Closed·재오픈/재완료·담당자 인계 사례 포함.
- 실제 Tracking 수집기 → 실제 JiraClient/provider → jira820 호환 REST 앱. 외부 Jira 접속 없음.
- 요청마다 50ms 지연 주입. serial은 동시 요청 제한을 모사하며 실제 SSO 인증 비용·회사망 지연은 포함하지 않는다.
- cold는 새 클라이언트/캐시/실행 DB. warm은 같은 캐시로 **새 조회 실행**. 저장 결과 다시 열기와 구분.
- 일간 11월 14일, 주간 11월 9~15일, 월간 11월 1~15일. KST 날짜 양끝 포함.
- 일반 조건 각 3회. 기간 전환·티켓 변경·다건 확장 미지원 조건은 각 1회. 다른 테스트와 동시 실행하지 않은 최종 v3 결과.

## 결과

시간은 전체 조회 중앙값. bytes는 압축 전 UTF-8 JSON 응답 본문 합계이며 HTTP 헤더는 제외.

| 조회 | 캐시 | 물리 Jira REST | 중앙값 | 최소–최대 | 응답 bytes |
|---|---|---:|---:|---:|---:|
| 일간 | cold | 4 | 2.250초 | 2.102–2.290초 | 939,230 |
| 일간 | warm | 1 | 1.262초 | 1.249–1.270초 | 86,810 |
| 주간 | cold | 4 | 2.249초 | 2.183–2.290초 | 939,230 |
| 주간 | warm | 1 | 1.279초 | 1.256–1.378초 | 86,810 |
| 월간 | cold | 4 | 2.079초 | 2.071–2.082초 | 939,230 |
| 월간 | warm | 1 | 1.300초 | 1.260–1.368초 | 86,810 |
| 월간 후 일간 전환 | 원본 재사용 | 1 | 1.186초 | 1회 측정 | 86,810 |
| 티켓 1개 변경 후 월간 | 변경분 재수집 | 2 | 1.411초 | 1회 측정 | 92,881 |
| 월간, 다건 댓글/이력 미지원 | cold | 204 | 18.603초 | 1회 측정 | 1,623,681 |
| 월간, 다건 댓글/이력 미지원 | warm | 1 | 1.538초 | 1회 측정 | 86,810 |

월간 cold 4회는 `/myself` 1회 + 상태 분류 1회 + 가벼운 후보 검색 1회 + 댓글/이력 다건 검색 1회다. warm은 후보와 `updated`를 새로 확인하는 검색 1회만 발생했다. 인증·상태 캐시까지 초기화한 cold를 별도로 측정했으며, 인증 캐시 만료나 추가 페이지·누락된 조상·잘린 댓글이 있으면 호출이 늘어난다. 4회를 모든 운영 상황의 상한으로 해석하지 않는다.

모든 측정에서 MCP 호출 0, 기본 LLM 토큰 0. 저장 결과 다시 열기는 해당 인증 캐시가 유효한 상태에서 REST 0회임을 검사했다. 필터·강조·트리 접기·날짜별 활동 전환도 추가 수집 없음. 필드 변경 표시를 위한 별도 REST 없음.

병렬 가능한 provider의 월간 cold/warm 중앙값은 2.075/1.277초, 호출은 동일한 4/1회였다. 후보 검색→원본 조회는 의존 단계라 이 사례는 병렬화 효과를 입증하지 않는다. 누락 원본의 독립 보충에는 기존 bounded scheduler를 사용하며 Tracking의 동시 요청은 최대 2개, serial provider는 1개로 제한한다.

월간 첫 cold 실행의 수집 시작부터 첫 결과까지는 1.250초, warm은 0.797초였다. 이는 UI polling·화면 그리기·최초 인증을 포함한 체감 지연과 다르다. 모든 활성 티켓이 최근 수정된 fixture이므로 일간도 첫 조회 원본량은 월간과 같다. 이는 오래된 티켓의 최근 댓글과 기간 종료 후 변경된 티켓을 놓치지 않기 위한 비용이다.

## 정확성·복구 검증

- 생성·완료·재오픈·댓글을 fixture 독립 정답과 대조: 모든 성능 실행에서 누락 0, 추가 0. 월간 핵심 사건 347개, 확인된 필드 변경 116개. 완료 32사건은 고유 16티켓으로 집계.
- 댓글 1건 추가 후 새 조회: 해당 티켓만 원본 재수집, 핵심 사건 348개 일치.
- 과거 담당자, DC username/user key 별칭, creator와 reporter 구분, KST 날짜 경계, 재완료 및 Done→Done 전환 검증.
- 25명 단위 조회 중 두 번째 그룹 연결 중단: 첫 그룹을 다시 받지 않고 실패 그룹부터 재개.
- 접근 불가 조상 복구, 부모 모듈 상속, 재개 후 범위 밖으로 판정된 후보 제거 검증. 누락된 근거는 partial 유지.
- 부분 댓글 페이지 보충, 잘린 changelog의 미완전 표시, 계정·연결 변경 시 저장 결과 접근 차단 검증.
- 관련 통합·정적·Jira query·domain·mock/local parity 테스트 **723개 통과**. 최종 scope 수정 후 Tracking/UI/mock 테스트 **49개 통과**. 중복 포함이며 전체 저장소 suite는 실행하지 않음.
- 실제 개발 UI: light 1600px / dark 1360px, JS 오류 0, 가로 넘침 없음, 트리 접기·강조·검색·일치 필터·날짜별 보기·TicketDialog 확인.

## 구현한 API와 한계

LTM에 Tracking options/runs/get/resume/cancel/summary API를 추가했다. jira820 연동 계층에는 공식 Jira 8.20 계약의 검색 `fields=comment`, `expand=changelog`, 완전성 메타데이터, `updatedBy` 및 필요한 역사 JQL, creator/updateAuthor 정보 지원을 보완했다. 설치된 외부 패키지 파일을 수정하지 않았으며 기존 mock/local 공통 경로를 사용한다.

운영에서 검색 확장이 빠지면 개별 이력·댓글로 보충한다. Jira 8.20에 없는 Cloud changelog API를 가정하지 않는다. 확장 이력조차 잘린 경우는 완료로 표시하지 않는다. 삭제 댓글과 이전 편집본은 복구하지 못하며 마지막 관측 편집만 표시한다. 모듈/WBS 소속은 현재 설정 기준이다.

선택 AI 요약은 저장된 근거만 전달하고 인용·미완전 표시·토큰 계측을 검사했다. 실제 모델 호출과 운영 Jira/SSO 지연은 검증하지 않았다. 이번 측정은 일반 크기 fixture이며 `--heavy`의 대형 댓글/이력 모드는 최종 성능 표에 포함하지 않았다.

## 재현

```powershell
..\.venv\Scripts\python.exe -B tools/tracking_benchmark.py --repeats 3 --latency-ms 50 --archived 9900 --output C:\Users\PC\research\ltm-tracking-20261116\benchmark-v3.json
```

러너는 runtime/cache/checkpoint를 OS 임시 디렉터리에 만들고 종료 시 정리한다. 원시 측정값·브라우저 캡처는 저장소 밖 `C:/Users/PC/research/ltm-tracking-20261116/`에 보존했다. UI 캡처: `tracking-final-light.png`, `tracking-final-dark.png`; 조작 결과: `browser-check.json`.

최종 구현 지문(SHA-256, Tracking Python + fixture + runner): `f5a96322b740c4f593a01a57d61eea23123af59dd711bbccc8706e698dac5056`.

계약 근거: [Jira REST 8.20.8](https://docs.atlassian.com/software/jira/docs/api/REST/8.20.8/), [JQL 함수](https://confluence.atlassian.com/jiracoreserver0820/advanced-searching-functions-reference-1095773235.html), [JQL 연산자](https://confluence.atlassian.com/jiracoreserver0820/advanced-searching-operators-reference-1095773234.html).

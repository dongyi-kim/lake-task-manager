# Agent 구조·품질·토큰 V3 최종 비교 보고서

> 비교 대상: 기존 V2(`ko-role-contract-v2`) / 구조·품질 개선 V3(`ko-role-contract-v3`)
> 실행일: 2026-08-12
> production routing: main/complex=`gpt-4o`, simple=`gpt-4o-mini`
> 동일 mock Jira·Confluence·comment·people 데이터, 실제 쓰기 전 승인 draft까지만 평가

## 1. 결론

V3는 V2의 가장 큰 문제였던 **자동 통과와 인간 품질의 괴리**를 크게 줄였다. Create는 V2에서
자동 `22/23`이었지만 사람이 읽으면 3.2/5 수준이었고, reply·payload 모순, 잘못된 parent,
placeholder, 과잉 분해가 반복됐다. V3는 전체 실행과 실패 케이스 closure run을 합쳐 `23/23`을
통과했고, 인간 평가는 Create 4.39/5, 38개 전체 4.46/5로 평가했다.

| 후보 | Conversation 6개 | Compose 9개 | Create 23개 | 38개 종합 |
|---|---:|---:|---:|---:|
| BASE(이전 보고서) | 4.3 | 4.1 | 3.9 | 4.0 |
| KO-R(이전 보고서) | 3.4 | 3.5 | 3.4 | 3.4 |
| V2 | 4.3 | 4.2 | 3.2 | 3.6 |
| **V3** | **4.77** | **4.42** | **4.39** | **4.46** |

사람 관점으로는 V3가 처음으로 BASE를 앞섰다. 다만 단일 live run과 일부 closure rerun을 합친
결과이므로 0.1점 차이를 통계적 우위로 해석하면 안 된다. production 기본 prompt 후보로는 충분하지만,
release 전에 seed를 섞은 5회 반복으로 p50/p95와 의미 실패율을 확인하는 것이 안전하다.

## 2. 평가 원칙과 증거 선택

점수는 5=그대로 사용 가능, 4=가벼운 수정, 3=재작업 필요, 2=중요 오판, 1=사용 불가로 매겼다.
자동 schema 통과와 별개로 다음을 직접 읽었다.

1. 답변이 실제 `pending.items`/`children`과 일치하는가.
2. ticket 상태·유형·parent·assignee·workload·DoD가 근거와 맞는가.
3. 사용자가 주지 않은 수치·원인·배포를 확정하지 않는가.
4. 모호한 사실은 질문하거나, 무엇이 미정인지 특정한 `확인 필요`로 남기는가.
5. placeholder, 내부 지시문, 깨진 link/mention, stale Reviewer 의견이 없는가.

모호성은 두 부류로 구분했다.

- 허용: `성능 측정 지표와 목표값은 담당팀 확인 필요 — 확정 후 측정값과 판정 결과를 기록한다.`
- 실패: `사용자에게 물어보세요`, `포함: 사용자에게 확인 필요`, `필요한 이유를 설명해 주세요`.

정량 비교는 중복 실행을 합산하지 않았다. 전체 run에서 실패한 케이스는 고친 뒤의 focused run으로
교체했다. 즉 Create는 전체 run의 22개 + `ATTR2` closure 1개, Compose는 전체 final run의 7개 +
`CMP1/CMP5` closure 2개, Conversation은 전체 run의 6개 중 `S2`만 closure 결과로 교체했다.
그 뒤 추가로 수행한 6/6·8/8 regression run은 품질 확인 증거이며 비교 비용에는 중복 산입하지 않았다.

## 3. 정량 결과

### 3.1 전체

| Battery | V2 | V3 final composite | 변화 |
|---|---:|---:|---:|
| Conversation 시간 | 251.7초 | **189.8초** | **-24.6%** |
| Conversation calls | 37 | **35** | -5.4% |
| Conversation tokens | 228,186 | **189,716** | **-16.9%** |
| Compose 자동 통과 | 9/9 | **9/9** | 유지 |
| Compose 시간 | 34.2초 | **22.6초** | **-33.9%** |
| Compose calls | 8 | 8 | 유지 |
| Compose tokens | **31,708** | 36,150 | +14.0% |
| Compose cost | **$0.091022** | $0.103589 | +13.8% |
| Create 자동 통과 | 22/23 | **23/23** | +1건 |
| Create 시간 | 1,201.7초 | **717.5초** | **-40.3%** |
| Create calls | 246 | **160** | **-35.0%** |
| Create tokens | 2,186,142 | **828,258** | **-62.1%** |
| Create harness cost | $4.879958 | **$2.077419** | **-57.4%** |

Create의 비용은 harness가 보고한 case별 마지막-turn cost 기준이다. multi-turn 전체 비용을 완전하게
재구성하지 않으므로 절대 청구액이 아니라 V2와 동일 방식의 비교값으로 봐야 한다.

### 3.2 과도한 토큰의 원인과 개선

| Role(runtime) | V2 calls / tokens | V2 비중 | V3 calls / tokens | V3 비중 |
|---|---:|---:|---:|---:|
| Historian / Research Analyst | 91 / 1,441,840 | 66.0% | **25 / 118,763** | 14.5% |
| Refiner / Draft Author | 39 / 356,033 | 16.3% | 35 / 358,590 | 43.7% |
| Planner / Intent Director | 28 / 119,624 | 5.5% | 28 / 119,346 | 14.5% |
| Responder / Result Integrator | 31 / 97,263 | 4.4% | 28 / 93,548 | 11.4% |
| Assigner / People Advisor | 19 / 65,609 | 3.0% | 16 / 58,873 | 7.2% |
| Query Specialist | 23 / 41,642 | 1.9% | 23 / 43,048 | 5.2% |
| Reviewer / Policy Auditor | 15 / 64,131 | 2.9% | **6 / 28,378** | 3.5% |

V2의 원인은 Historian이 같은 조사와 repair를 반복한 것이었다. V3는 조사 범위를 먼저 좁히고,
semantic relevance를 코드로 거르며, LLM Reviewer 의견만으로 재작성 루프를 돌지 않는다. 그 결과
Historian token이 91.8%, 전체 Create token이 62.1% 줄었다. Refiner 절대 token은 거의 같지만 전체
비중이 커진 것은 다른 role의 낭비가 줄었기 때문이다.

## 4. V3 역할·계약 구조

| Canonical Role | runtime alias | 책임과 출력 계약 |
|---|---|---|
| Intent Director | Planner | 요청 종류·복합도·조사/작성/실행 경로 결정 |
| Request Architect | — | 복합 요청 분해, 확실하지 않은 조건 인터뷰·open fact 지정 |
| Query Specialist | Query Specialist | 자연어 조건을 search-config 범위의 JQL/document/comment/person query로 변환 |
| Query Runner | Query Runner | cursor/native pagination으로 전량 조회, scope 증거 반환 |
| Research Analyst | Historian | Jira·Confluence·comment·외부 근거 종합, 사실/충돌/공백 분리 |
| Knowledge Curator | Curator | 전문 지식 brief와 provenance 정규화 |
| Work Architect · Draft Author | Refiner | Epic/Task/Sub-Task 구조, 본문, 질문, 실제 create payload 작성 |
| People Advisor | Assigner | 실재 user와 workload 근거가 있는 담당/대안 제안 |
| Policy Auditor | Reviewer | hierarchy, Done, 중복, DoD, source, output contract 검증 |
| Action Operator | Operator | 승인된 token의 action만 실행 |
| Result Integrator | Responder | 최종 state를 한국어 답변으로 통합하고 payload와 교차 정합 |
| Composer | Composer | 기존 ticket context로 description/comment HTML 생성 |

`Historian`과 `Refiner` 이름이 일부 runtime에 남아 있지만 역할 원점은 각각 Research Analyst와
Work Architect · Draft Author다. Historian/Refiner를 별도 옛 역할로 병존시킨 구조가 아니다.

## 5. Conversation battery — 실제 차이와 인간 평가

정량값은 V2 → V3의 `시간 / total tokens`이며, 실제 답변은 차이가 드러나는 부분만 중략했다.

| ID | 정량 V2 → V3 | 실제 출력 차이 | V2 → V3 점수·의견 |
|---|---:|---|---:|
| S1 생성 | 116.7초/92,609 → **90.0초/72,463** | V2: “단계별”이라면서 child 2개, `DL-5515/JIRA820-17` 참고. V3: `Iceberg Puffin NDV 통계 Batch Job 설계/구현/검증` 3개, 담당 `i2011/x1042/x1103`, 무관 parent 없음. | 3.2 → **4.7**. 전문용어·단계·payload가 일치한다. |
| S2 Bug | 63.9/55,625 → **37.5/36,160** | V2: Workbench이지만 cross-project parent. V3: `Bug [Catalog] 리니지 뷰어에서 2홉 이상 펼치면 화면이 빈다`, 재현/기대/실제 분리, `x1210 10건`, 대안 `i2044 13건`. | 3.7 → **4.8**. parent 날조와 workload 역전 제거. |
| S3 이력 | 34.2/32,422 → **30.5/27,090** | V3 실제: `DAG=dag_fdc_trace_summary_ic`, `30분 1회`, `8개 컬럼`, 2026-02-13~07-28 연표와 `DL-9041~9062` 근거 표. | 4.6 → **4.8**. V2 장점을 유지하며 더 짧다. |
| S4 사람 | 18.9/23,849 → **12.9/25,487** | V2: 총 21건, 10건 상세. V3: `DL-5019/5514/5005/.../5525` 10건과 Reopened 상태를 바로 제시. | 4.7 → 4.5. 빠르지만 전체 21건 중 10건이라는 절단 안내가 없어 소폭 감점. |
| S5 시작 업무 | 7.9/11,248 → **7.4/12,266** | V3: “`DL-9028`은 P1-Critical이고 마감 초과이므로 즉시 처리.” | 4.8 → **4.9**. 한 문장으로 우선순위·기한·행동을 결합했다. |
| S6 진척 | 10.1/12,433 → 11.5/16,250 | V3: “3개 중 2개 완료, 남은 `DL-9095`”, 코멘트 날짜·담당, `DL-9092` 해소, 설계 문서, `2026-08-19`까지 남은 측정/가이드. | 4.8 → **4.9**. 상태명이 아니라 완료/잔여/근거/리스크를 답했다. |

## 6. Compose battery — 실제 차이와 인간 평가

`CMP4`(빈 새 ticket)와 `CMP7`(김치찌개)은 V2/V3 모두 안전한 `needsInfo`로 본질적 차이가 없어
전문 비교를 생략하되 점수에는 포함했다.

| ID | V3 실제 출력의 차이 | V2 → V3 점수·의견 |
|---|---|---:|
| CMP1 진행 공유 | V2는 날짜에 틀린 요일을 붙였다. V3 closure: “다운스트림 조회 연동은 현재 진행 중, API 응답 문제는 해결”, “남은 성능 측정·문서 정리”. | 3.4 → **4.8**. Jira 상태와 코멘트의 완료 보고가 다르면 한쪽을 확정하지 않는다. |
| CMP2 본문 보강 | V3: `<h3>배경/작업 범위/완료 조건</h3>` 유지. 출처 없는 `20%`는 `담당팀과 합의한 목표값`으로 교정. 구조상 상위 `DL-9040`은 실제 fixture 관계라 남지만 제목도 함께 싣는다. | 3.6 → **3.7**. fixture의 의미상 어색한 parent와 일반론 동기는 여전히 가벼운 수정 필요. |
| CMP3 이어쓰기 | V2: 다운스트림 API를 원인으로 단정. V3: “p95가 기대보다 낮/높음” seed를 이어 최적화 필요성을 쓰되, 일부 run에서 전체 ticket 마감을 최적화 마감으로 확대했다. | 3.0 → **3.4**. 원인 단정은 줄었지만 자유 이어쓰기의 범위 확대가 남음. |
| CMP4 모호 | `이대로는 정확한 글을 쓸 수 없습니다 — 무엇에 대한 글인지 목적과 대상을 한 줄만…` | 5.0 → **5.0**. 동일. |
| CMP5 상태 공유 | V3 closure 실제: 렌더/업스트림 `완료`, 다운스트림 `진행 중`, 성능 측정·문서/가이드 `미완료`. | 4.8 → **4.9**. 상충 source를 안전하게 정리. |
| CMP6 멘션 | V3: `<span data-type="mention" data-id="skcc.x1402">@skcc.x1402</span>`와 설계 문서 link. | 5.0 → **5.0**. deterministic mention/reference 유지. |
| CMP7 무관 | `어떤 작업에 대한 코멘트인지 — 목적을 한 줄만 적어 주세요.` | 5.0 → **5.0**. 동일. |
| CMP8 parent 본문 | V3: parent는 전체 배경/범위/DoD를 맡고 자식 title 복제를 줄임. 성능 기준 미정은 확인 과제로 변환. | 4.2 → **4.2**. 실제 구조는 좋지만 fixture parent 의미가 어색하다. |
| CMP9 짧은 본문 | V3 prompt는 제목·현재 본문에 없는 UI/성능 scope 추가를 금지하고, 미정은 확인 필요로 남긴다. | 3.7 → **3.8**. live 출력의 일반적인 “사용성 향상” 동기는 아직 다듬을 여지가 있다. |

## 7. Create battery — 모든 차이 사례의 실제 출력과 평가

아래 V3 인용은 full run보다 늦은 closure run이 있으면 그것을 우선했다. 마지막 deterministic
중복 제거 후 live API를 다시 부르지는 않았으므로, `STR3/PAR1`의 closure 전문에는 같은 open-fact
DoD가 두 번 나온 증거가 남아 있다. 최종 코드에서는 동일 행 제거 unit test가 통과했지만 이 보고서가
실제 출력을 더 깨끗한 것처럼 바꾸지는 않는다.

| ID | V3 실제 답변/payload의 차이 | V2 → V3 인간 점수·의견 |
|---|---|---:|
| ONE1 | V2는 작은 요청을 5단계로 과분해. V3: `Task [Workbench] 쿼리 편집기 단축키 도움말 팝업 추가` 1건, UI 구현 포함/단축키 자체 변경 제외, `x1402`. | 1.5 → **4.3**. 단건 위임을 지킨다. DoD 문구는 한 번 더 구체화 가능. |
| ONE2 | V2는 임의 parent. V3: `[Catalog] '내 모듈만' 필터 추가` 단건, 체크박스 동작/UI test DoD, `x1210`. closure 답의 “DoD 누락”은 실제 표와 모순해 후처리에서 제거하도록 고정. | 3.3 → **4.3**. 구조·본문은 usable. |
| STR1 | V2는 raw ref와 잘못된 module. V3: parent Task + `1/2(15개)`, `2/2(15개)` child, 합계 30·중복/누락 대조, 서로 다른 담당. | 3.8 → **4.6**. 실제 child payload와 분량이 맞는다. |
| STR2 | V2는 인덱스 Task를 여러 module에 중복해 5건. V3: `Catalog 성능 측정`, `Runtime 인덱스 조정`, `Catalog 가이드` 정확히 3건. | 1.5 → **4.4**. 가장 위험한 과잉 생성이 제거됐다. 116.7초로 여전히 최장 case다. |
| STR3 | V2: `# 명령서`, 출처 없는 20%. V3: 새 Epic 대신 `DL-102` 아래 ETL Task, 수치가 없으므로 “성능 측정 지표와 목표값은 담당팀 확인 필요”. | 3.3 → **4.0**. 의미는 옳고 안전하다. closure의 동일 DoD 2회는 최종 dedupe test로 보강. |
| PAR1 | V2의 강점은 유지. V3: `DL-9090` 아래 리니지 뷰어 성능/가이드/회귀 Sub-Task, 사용자가 정한 `x1402/x1450/x1042`, 실제 담당 표 보장. | 4.8 → **4.2**. parent 주제가 제목에 들어가고 payload 일치. closure에서 중복 DoD·Reviewer false가 남아 감점. |
| PAR2 | V2는 사용자가 지정한 `DL-101`보다 다른 ticket을 앞세움. V3: `[ETL] CDC 재처리 배치 개선`, 상위 `DL-101`, label `needs-review`, 담당 `x1103`. | 2.8 → **4.4**. explicit parent 우선순위 회복. |
| SUB1 | V2는 “불가”라면서 payload 3개. V3: `DL-9095`가 이미 Sub-Task라 부모가 될 수 없음을 설명하고 item 0, 질문 form. | 2.0 → **4.6**. reply와 실행 후보가 일치한다. |
| SUB2 | V2의 2개 생성은 유지. V3: `DL-9090` 아래 `데이터 리니지 뷰어 성능 측정/사용 가이드`, 성능 기준은 담당팀 확인, 가이드 link·review는 parent 기록. | 4.2 → **4.1**. payload는 좋지만 closure 답의 실제 DoD 표가 성능 행만 보인 결함을 최종 전체표 보장으로 수정. |
| SUB3 | V2는 불가 답변 뒤 payload 잔존. V3: 두 대상이 모두 Sub-Task라 생성 보류, item 0, 대안 질문. | 1.0 → **4.7**. Done/계층 안전성이 회복됐다. |
| PASTE1 | V3: VoC를 `Bug [Catalog] 데이터 조회 시 컬럼 설명이 안 보이는 문제`, 재현/기대/실제 분리. | 3.5 → **4.3**. 기능 gap일 가능성은 있으나 보이는 UI 결함이라는 해석은 합리적. |
| PASTE2 | V2는 “매일 반복”으로 강화. V3: “어제 같은 시간대에도 발생, 재실행하면 해결되지만 반복”, ETL Bug, label regression. | 1.8 → **4.6**. 원문의 관측과 우려를 구분했다. |
| ASK1 | V3: `DL-5170/5354` 맥락을 짧게 말하고 목표·범위·완료 조건을 3개 form으로 질문, item 0. | 3.5 → **4.3**. 최대 질문 수 계약 준수. |
| ASK2 | V2의 실행성을 유지하면서 instruction leakage 제거. V3: `[Workbench] 널 비율 체크 구현`, 마감 `2026-08-14`, 단건 payload. | 4.2 → **4.3**. module은 대화 맥락상 Workbench로 일관됨. |
| DUP1 | V3: “이미 `DL-9072`에서 진행 중, 이준서 담당, 추가 Task 보류”, item 0. | 4.2 → **4.7**. 기존 ticket 계속 사용이 기본 결론으로 선명해졌다. |
| ATTR1 | V3: `[DataOps] 적재 지연 알림 임계값 조정`, P1, `2026-08-14`, `hotfix`, 담당 `i2130`. | 3.5 → **4.1**. 속성은 정확하다. 사용자가 “알아서”라고 했지만 module 추론은 명시하면 더 좋다. |
| ATTR2 | V2는 “항목을 나열합니다” placeholder. V3: Catalog Task, 신규 label `quality-gate`, “품질 룰/통과 기준은 담당팀 확인 필요 — 확정 기준별 결과 기록”. | 3.0 → **4.1**. 모호성을 정직하게 남김. closure 배경의 “이유를 사용자에게 확인” 지시문은 최종 placeholder guard에 추가. |
| STARR1 | V3: `[ETL] StarRocks Puffin NDV 통계정보 파이프라인 개발`, `2026-09-30`, 설계/구현/검증 child 3개와 담당 분산. | 4.5 → **4.5**. 고유어·구조·기한을 보존하며 관련 없는 `DL-102` 연결 제거. |
| BUG1 | V3: `DL-9090/9092` 맥락 뒤 재현 경로·기대·실제를 최대 3개로 질문, item 0. | 4.2 → **4.5**. 재현 정보 없이 만들지 않는다. |
| BUG2 | V3: Catalog Bug, 크롬/2홉 재현, 기대 그래프/실제 빈 화면, `x1210`, 무관 cross-project parent 없음. | 3.8 → **4.6**. 바로 승인 가능한 Bug body다. |
| BUG3 | battery 설명을 fixture와 맞게 “재현 정보가 없는 동일 증상 요청은 중복·재현 확인 없이 만들지 않는다”로 정정. V3는 item 0, 질문 3개. | 3.5 → **4.4**. 이전 설명의 “이미 중복 존재”는 mock과 모순이었다. |
| RULE1 | V3: “Sub-Task는 반드시 부모 Task가 있어야 하므로 처리할 수 없음”, 부모 선택 form, item 0. | 4.5 → **4.8**. 계층 규칙을 정확히 적용. |
| RULE2 | V2는 미완성 body. V3: Story 1건, Story Point는 payload에서 제외하고 생성 후 화면 입력을 한 번만 안내. DoD는 구현/test 기록 또는 3홉 경로 조회 결과로 구체화. | 2.0 → **4.2**. schema뿐 아니라 본문도 사용할 수 있다. “관련 문서 업데이트” 같은 일반 문구는 추가 보강 대상. |

## 8. 구조적 개선 내용

### 8.1 ticket tier와 action 유효성

- 계층은 `Epic → Task(issue type: Task/Improvement/Feature/Bug/Story 등) → Sub-Task`다.
- Sub-Task는 parent Task 없이 생성하지 않으며 Sub-Task를 다시 parent로 쓰지 않는다.
- Done ticket은 속성을 바로 변경하지 않는다. `Reopened` transition을 별도 승인으로 수행한 뒤 수정한다.
- Done ticket에도 comment는 작성할 수 있다.
- 사용자 지정 parent/assignee/type/priority/due/label이 추론된 관계보다 우선한다.
- Story Point는 현재 create payload 미지원이므로 생성 후 화면 입력을 안내하되, payload에는 넣지 않는다.

### 8.2 search와 tool 계약

- Jira 읽기는 `search.jira.projects`의 모든 project만 묵시적으로 적용한다. write destination의
  `project_key`를 search fallback으로 사용하지 않는다.
- Confluence는 `search.confluence.spaces`의 모든 지정 space만 조회한다.
- `run_jql_v2`는 50건 고정 절단 대신 native pagination/cursor를 제공한다.
- ticket/document/person/comment reference를 canonical object로 resolve한 뒤 badge·mention·link로 렌더한다.
- structured output은 `json_schema → json_object → prompt JSON → repair` fallback을 사용하고,
  native tool calling이 없는 endpoint에서도 등록 tool plan으로 후퇴한다.

### 8.3 인간 품질을 지킨 deterministic guard

- 답변의 type/parent/assignee/workload/children/DoD를 실제 payload와 다시 맞춘다.
- 존재하는 key라는 이유만으로 관련성을 인정하지 않고 semantic relevance를 분리한다.
- 출처 없는 `20%`, `1분`, 운영 배포 약속을 제거하고 구체적인 open fact로 남긴다.
- 동일한 DoD가 정규화 과정에서 중복되면 한 번만 남긴다.
- `# 명령서`, raw placeholder, 깨진 Markdown URL, false person/title warning을 제거한다.
- Composer에서 comment/document와 Jira status가 충돌하면 `최종 완료 여부 확인 필요`로 표시한다.

이 구조는 [AGENT.md](../../../app/agent/AGENT.md)에 prompt 작성법, role input/output, 우선순위, 실패 처리 방식으로
문서화했다. 설계 원칙은 OpenAI의 [Prompting best practices](https://developers.openai.com/api/docs/guides/latest-model#prompting-best-practices),
Anthropic의 [Building effective agents](https://www.anthropic.com/research/building-effective-agents) 및
[Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents),
Atlassian의 Jira search API 자료를 참고했다.

## 9. 남은 위험과 최종 판단

남은 위험은 세 가지다.

1. mock fixture에서 `DL-9090/9095`의 구조상 Epic이 `DL-9040 데이터셋 카탈로그 지식 픽스처`로
   연결되어 있어 의미상 어색하다. Agent가 관계를 날조한 것은 아니지만 fixture 정합성을 별도로 고쳐야 한다.
2. Refiner와 Composer는 model 문구 변동이 크다. deterministic guard 뒤에도 자연스러운 한국어와
   scope 확장의 품질은 seed 반복으로 봐야 한다.
3. Reviewer advisory가 false여도 기계 오류가 없으면 사람 승인으로 보낸다. 이는 무한 repair를 막지만,
   UI에서 advisory와 실제 수정 후 payload를 명확히 구분해야 한다.

그럼에도 V3는 V2와 BASE 대비 채택할 가치가 있다. 특히 Create token 62.1% 감소와 human Create
3.2→4.39 개선이 동시에 나타났다. 추천 결정은 **V3를 production candidate로 채택하되, 5회 반복
release battery에서 인간 중대 오류 0건과 latency p95를 확인한 뒤 기본값으로 전환**하는 것이다.

## 10. 검증·원시 증거

- 최종 unit suite: **1364 passed, 1 skipped**
- Create full: [`create-ko-role-contract-v3-mixed-final.json`](../results/create-ko-role-contract-v3-mixed-final.json) — 22/23; 유일 실패는 구체적
  `담당팀 확인 필요`를 placeholder로 오탐한 evaluator 문제
- Create evaluator closure: [`create-ko-role-contract-v3-attr2-final.json`](../results/create-ko-role-contract-v3-attr2-final.json) — **1/1**
- Create human closure: [`create-ko-role-contract-v3-closure-final.json`](../results/create-ko-role-contract-v3-closure-final.json) — **6/6**
- Create 추가 regression: [`create-ko-role-contract-v3-human-final.json`](../results/create-ko-role-contract-v3-human-final.json) — **8/8**
- Conversation full: [`ab-ko-role-contract-v3-mixed-final.json`](../results/ab-ko-role-contract-v3-mixed-final.json) — 7턴, 근거/후검증/구조/card 위반 0
- Conversation S2 closure: [`ab-ko-role-contract-v3-s2-final.json`](../results/ab-ko-role-contract-v3-s2-final.json) — **1/1**
- Compose final full: [`compose-ko-role-contract-v3-mixed-final.json`](../results/compose-ko-role-contract-v3-mixed-final.json) — 7/9; 두 실패는 상태 충돌을
  안전하게 차단했으나 HTML block 정정 범위가 좁았던 것
- Compose status closure: [`compose-ko-role-contract-v3-status-final.json`](../results/compose-ko-role-contract-v3-status-final.json) — **2/2**
- 비교 기준: `create/ab/compose-ko-role-contract-v2-mixed.json`

샌드박스 네트워크 차단 상태에서 실행한 한 시도는 모든 call/token이 0이고 전부 `Connection error`였으므로
품질·시간 통계에서 제외했다. 같은 명령을 승인된 네트워크 접근으로 재실행한 성공 결과만 사용했다.

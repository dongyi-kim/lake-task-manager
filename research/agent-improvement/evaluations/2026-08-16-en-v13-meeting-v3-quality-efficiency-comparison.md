# English Agent v13 — 비정형 회의록 재구성 품질·효율 비교

> 결론: 같은 meeting battery `3.0.0`과 같은 mock world에서 EN v12 기준선은 자동 계약 **5/9**, Codex 직접 정성평가 **3.63/5**. 비정형 회의록 재구성·사람 역할 분리·최소 인터뷰·최종 결정 경계를 공통 구조로 보강한 EN v13은 자동 계약 **9/9**, Codex 직접 정성평가 **4.76/5**. 동시에 실행 시간 **15.4%**, 전체 token **2.1%**, 비캐시 prompt token **37.0%** 감소.
>
> 이 결과는 실제 OpenAI API를 사용한 1회 exploratory 비교. qualification을 위한 5회 반복·무작위 후보 순서는 수행하지 않았으므로 통계적 우월성 판정이 아니라 구조적 개선의 회귀·사람 품질 근거로 사용.

## 배터리 범위

meeting battery를 `2.0.3`의 정돈된 회의록 5건에서 `3.0.0`의 9건으로 확장.

- 대화 인용, 정리문, 첨부 문서 발췌, 요약 메모, 쓰다 만 문장과 이들의 혼합
- 사용자 앞뒤 설명·첨부·내부 Jira/Confluence/comment·안전한 외부 자료의 결합
- `from: 사람`, `내용 by 사람`, `사람: 내용`, `사람 의견`, `@이름`, `{{이름:식별자}}`, 이름 일부+호칭의 동일인 정규화
- 화자·의견자·지시자·검토자·실행 담당·미할당의 분리
- 조사 후에도 행동에 필요한 담당·기한·범위가 남을 때만 인터뷰하고, 답변 뒤 이미 확정한 내용을 다시 묻지 않고 재개
- 보류 의견과 최종 합의를 분리해 create/comment/update payload에는 확정된 행동만 반영

## 측정 식별자

| 항목 | 기준선 | 개선 후 |
|---|---|---|
| candidateCommit | `8cca3afee76299cf69da5d8167b3b285221700c2` | `af876c7094c354cdd14b65c535db2680fccc2751` |
| promptVersion | `en-role-contract-v12` | `en-role-contract-v13` |
| protocolVersion / rubricVersion | `2.0.0` / `2.0.0` | 동일 |
| batteryVersion | `meeting 3.0.0` | 동일 |
| batteryManifestSha256 | `d68d5f91299971b333942ee8c06b0e378db010a600b9231e570c9d1b00d42512` | 동일 |
| specializedReviewSpecSha256 | `0c8f46d8a2b723763613252a0f2fc80ba52259c0280fecc70ca590e1cc8877ef` | 동일 |
| dataProfile | `jira820-mock-v1` | 동일 |
| dataManifestSha256 | `87e592d3cc136e62e135e5d81c76c91121da0e85d18fdc0b74bd0304f0521621` | 동일 |
| model / simpleModel | `gpt-4o` / `gpt-4o-mini` | 동일 |
| provider / runtimeProfile | `openai` / `production-mixed-v1` | 동일 |
| cache / isolation | case별 cold private cache / separate process | 동일 |
| retry policy | `no-silent-retry` | 동일 |
| comparabilityKey | `92901b8f908484a7c6aa1718c01b4874863c7953353dbc374dc29efe930ae1b3` | 동일 |
| qualitative evaluator | Codex가 실제 reply·question·pending payload·query/evidence trace 직접 판독 | 동일 |
| LTM LLM judge 사용 | `false` | `false` |
| qualificationEligible | `false` — exploratory 1회 | `false` — exploratory 1회 |
| runKind | `exploratory` | `exploratory` |
| runGroupId | `exploratory-2026-08-16T09:22:44Z` | `exploratory-2026-08-16T10:44:21Z` |
| repetitions | `1` | `1` |
| selectionPolicy | `complete-run-no-substitution` | 동일 |
| aggregation | case=`weighted-mean-of-dimensions-after-caps`, suite=`arithmetic-mean-of-all-case-attempt-scores-in-suite` | 동일 |
| percentileMethod | `nearest-rank-over-all-case-attempts` | 동일 |
| candidateOrderIndex | `unrecorded` | `unrecorded` |
| retryPolicy | `no-silent-retry` | 동일 |
| cachePolicy | `cold-private-cache-each-case` | 동일 |
| processIsolation | `separate-process-private-cache` | 동일 |
| qualitativeEvaluatorPolicy | `direct-human-review-no-ltm-self-judge` | 동일 |
| evaluatorAgentFamily | `Codex` | `Codex` |
| evaluatorAgentModel | `GPT-5 family` | `GPT-5 family` |
| directRawOutputReview | `true` | `true` |
| ltmLlmUsedAsJudge | `false` | `false` |
| reviewerCount | `1` | `1` |
| blindedReview | `false` | `false` |

Raw 결과는 git 제외 경로에 보존.

- 기준선: `.cache/agent-evaluation/2026-08-16-en-v12-meeting-v3-baseline-r01/meeting-b3.0.0-r01.json`
- 개선 후: `.cache/agent-evaluation/2026-08-16-en-v13-meeting-v3-final-r04/meeting-b3.0.0-r01.json`

## 비교 가능성 및 evidence 선택

- 두 primary run 모두 meeting 9건 전체를 `complete-run-no-substitution`으로 실행. 실패 case를 다른 시도의 성공 결과로 교체하지 않음.
- battery manifest, 특수 review spec, mock data manifest, model routing, provider, cache·process isolation, retry policy가 동일. 따라서 prompt/code 후보 차이를 비교할 수 있음.
- 사람 평가는 각 primary raw의 최종 reply, 질문, 승인 pending payload, query plan/result/artifact, evidence, related docs, web context를 직접 읽어 수행.
- 개발 중 focused run은 실패 원인 확인과 closure에만 사용하고 아래 primary 5/9 및 9/9 집계에는 섞지 않음.
- 두 후보 모두 1회 exploratory이고 후보 순서가 무작위화되지 않아 qualification comparison에는 부적합.

## 실행 조건

- 실제 OpenAI API, `gpt-4o` main + `gpt-4o-mini` simple mixed routing
- mock Jira/Confluence/people world만 사용하며 실제 ticket/comment/field write 없음. 승인 전 pending payload까지만 평가
- case별 별도 process와 private cold cache. case 사이 provider store·world mutation·cache 공유 없음
- 기술 재시도 없음. 개발 중 실패·focused closure raw도 삭제하지 않고 ignored cache에 보존
- latency는 case attempt 전체에서 nearest-rank로 산출: 기준선 p50/p95 `37.0s / 84.5s`, 개선 후 `28.9s / 56.9s`

## 정량 결과

| 지표 | EN v12 기준선 | EN v13 개선 후 | 변화 |
|---|---:|---:|---:|
| 자동 계약 통과 | 5/9 | **9/9** | **+4건, +44.4%p** |
| 실행 시간 | 349.9s | **296.2s** | **-53.7s, -15.4%** |
| LLM calls | 82 | **79** | **-3, -3.7%** |
| prompt tokens | 458,932 | **450,120** | **-8,812, -1.9%** |
| completion tokens | 29,875 | **28,544** | **-1,331, -4.5%** |
| total tokens | 488,807 | **478,664** | **-10,143, -2.1%** |
| cached tokens | 236,928 | **319,872** | **+82,944, +35.0%** |
| 비캐시 prompt tokens | 222,004 | **130,248** | **-91,756, -41.3%** |
| 비용 | $1.446078 | **$1.410741** | **-$0.035337, -2.4%** |

비캐시 prompt token은 `promptTokens - cachedTokens`. 전체 출력 품질이 올라가면서 호출·출력 token도 함께 줄었으므로 단순 응답 축약으로 만든 효율 향상이 아님.

## 사람 품질 평가 기준

정성평가 주체는 LTM LLM이 아니라 Codex. rubric `2.0.0`에 따라 각 축을 1.0~5.0, 0.5 간격으로 채점하고 동일 가중치로 평균. applicable checklist에 minor가 있으면 해당 축 최대 4.5, major가 있으면 최대 3.5, 여러 major이면 최대 3.0.

| 축 | 사람이 확인한 질문 | 가중치 |
|---|---|---:|
| F 요청 충족·완결성 | 마지막 요청, 복합 범위, 명시 제약, 회의의 결정·담당·기한·미결을 모두 다뤘는가 | 20% |
| G 사실성·근거성 | 사람·티켓·문서·날짜·상태가 근거와 맞고, 내부 사실과 외부 일반 지식 및 상충을 구분했는가 | 20% |
| C 계약 일관성·실행 가능성 | reply·질문·승인 카드·payload가 일치하고 exact target/action/field/assignee/parent를 지켰는가 | 20% |
| S 안전성·불확실성 | 조사 가능한 것은 먼저 조사하고, 그래도 필수인 공백만 질문하며 미결·보류·미할당을 발명하지 않았는가 | 20% |
| R 가독성·간결성·렌더링 | 결론 우선, 짧은 문장, heading/table/list, mention·ticket·document marker·citation이 중복 없이 렌더링 가능한가 | 20% |

## 배터리·case 특수 검토요소

회의록 특수 checklist는 다음을 추가 적용.

- 비정형 note 재구성: 앞뒤 프롬프트, 본문, 첨부 발췌, 내부·외부 근거를 함께 사용
- identity 정규화: 한 회의 안의 가변 표기를 동일인으로 묶고 모호할 때만 후보 인터뷰
- actor-role 분리: 발언·의견·지시·검토를 assignee로 오인하지 않음
- research-then-interview: 내부 → 안전한 외부 조사 → 남은 필수 공백 질문 순서
- decision-to-action: 확정 결정만 create/comment/update payload로 변환

## 사람 품질 점수

축 순서 `F/G/C/S/R`.

| Case | 기준선 | 개선 후 | 개선 후 축 | Codex의 사람 관점 평가 |
|---|---:|---:|---|---|
| MTG1 | 4.5 | **4.8** | 5/4.5/5/5/4.5 | 결정·담당·기한·PSR·상충 자료를 보존. 현재 상태와 근거가 일부 반복되는 점만 경미 |
| MTG2 | 4.5 | **4.6** | 4.5/4.5/4.5/5/4.5 | 세 Task와 담당·기한·Epic 정확. RGP 기준+템플릿 결합과 reviewer 제외 표현은 다듬을 여지 |
| MTG3 | 4.9 | **4.9** | 5/4.5/5/5/5 | 두 티켓에 댓글만 생성하고 대상별 담당 mention, 무필드 변경을 정확히 유지 |
| MTG4 | 4.7 | **4.7** | 5/4.5/5/5/4 | exact 변경 필드와 no-comment 준수. 본문에서 `기준 기준`, 배경·범위 반복은 가독성 minor |
| MTG5 | 4.8 | **4.8** | 5/4.5/5/5/4.5 | PSR 용어·담당·reviewer·Epic·기한을 정확히 보존 |
| MTG6 | 2.4 | **4.6** | 5/4/5/5/4 | 혼합 메모를 결정·담당·미결로 복원하고 내부·외부 조사 수행. 첨부 파일명이 평문으로 남고 일부 근거 root가 중첩된 점은 minor |
| MTG7 | 2.1 | **4.8** | 5/4.5/5/5/4.5 | `from/by/콜론/의견`을 assignee와 분리, 2명+미할당 1건과 지시자 배경 정확. DoD 일부가 일반적 |
| MTG8 | 2.3 | **4.8** | 5/4.5/5/5/4.5 | 첫 turn에는 reader 담당/미할당만 질문, 답변 뒤 writer 담당·reader 미할당·기한·지시자 보존 |
| MTG9 | 2.5 | **4.8** | 5/4.5/5/5/4.5 | 보류된 priority/component/label과 무관한 모호 인물 질문 제외, 최종 세 필드만 변경. rationale의 과거 필드명 잔존은 minor |
| **평균** | **3.63** | **4.76** | **4.94/4.44/4.94/5.00/4.44** | **+1.13/5** |

자동 9/9이 사람 품질 만점을 뜻하지 않음. 남은 감점은 주로 근거 표현과 본문 문장 품질이며 행동 안전성·담당·필드 계약은 5.0 수준.

## 배터리별 실제 출력과 평가

차이가 작았던 MTG1~5는 위 점수표와 [직전 full 비교](./2026-08-16-en-v12-full-quality-cumulative-comparison.md)에 요약. 새로 강화해 차이가 큰 MTG6~9의 실제 reply/payload 핵심은 아래와 같음.

### MTG6 — 혼합 회의록·첨부·조사

기준선:

```text
### 결론
현재 Puffin 파일의 StarRocks 호환성 검증 작업이 진행 중이며, 5개 표본 Puffin 파일이 만들어졌습니다.
...
| 20개 전체 확대 | [~skcc.i2011] | [1] |

### 현재 진행 중인 Task
- {{ticket-detail:DL-9036}}

⚠️ 자동 검증 경고
- [puffin-followup-notes.docx](verified URL) ... 확인 불가한 출처
```

- 20개 확대 여부 열에 사람을 값으로 넣고, 관련 없는 `DL-9036` 노출
- 로컬 첨부명을 가짜 URL로 만들고 내부 검증 경고를 사용자 답변에 노출
- 화자·담당·기한과 운영 보류 누락

개선 후:

```text
### 결정사항
- 1차 범위로 5개의 표본 Puffin 파일을 생성하여 PoC를 진행 중
- StarRocks reader의 소비 검증이 완료되기 전까지 운영 반영은 보류
- 20개 전체 확대는 이번 회의에서 결정된 사항이 아님

### 담당·기한
| reader/optimizer 소비 검증 | [~skcc.x1402] | 2026-08-25 |
| writer 증빙 정리 | [~skcc.i2011] | 2026-08-22 |

### 미결·검증
- StarRocks reader가 Puffin 파일을 실제로 소비하는지 여부는 아직 확인되지 않음
- Puffin NDV의 20개 전체 확대 여부는 결정되지 않음
```

- Jira·회의 문서·공식 StarRocks 자료를 조사하고 결정/미결 분리
- unrelated ticket과 사용자 노출용 내부 경고 제거
- 첨부 파일명 평문 및 근거 중첩은 남은 표현 개선점

### MTG7 — 가변 사람 표기와 담당·미할당

기준선 payload 핵심:

```text
writer / reader / 로그 마스킹 3건 모두 assignee = skcc.x1210
priority = P3-Minor, components = Catalog, labels = ... 를 임의 추가
본문에 회의·지시자 배경 없음
```

reply에서는 첫 행의 `유형`에 사람 mention이 들어가고, “미할당으로 남겼다”는 rationale과 실제 세 건의 강제 배정이 서로 충돌.

개선 후:

```text
| writer 증빙 패키지 개발 | [~skcc.i2011] | 2026-08-22 |
| reader 검증 결과 정리 | [~skcc.x1402] | 2026-08-25 |
| 로그 마스킹 체크리스트 작성 | 미할당 | 2026-08-27 |

<h3>배경</h3>
<p>회의 논의에서 확정된 후속 작업 · 요청·지시자: {{mention:skcc.x1042}}</p>
```

- `from`, `by`, 콜론 발언, 의견 표기를 assignee 근거로 사용하지 않음
- 명시한 담당 두 명만 배정하고 미정은 미할당 유지
- 사용자가 말하지 않은 priority/component/label 제거

### MTG8 — 쓰다 만 메모와 최소 인터뷰

기준선 최종 payload:

```text
writer due 누락
writer / reader 모두 assignee = skcc.x1210
priority, component, label 임의 추가
```

개선 후 첫 turn:

```text
reader Task 담당자를 지정할까요, 아니면 미할당으로 둘까요?
```

개선 후 답변 재개:

```text
| writer 증빙 패키지 정리 | [~skcc.i2011] | 2026-08-23 |
| StarRocks reader 운영 판정 자료 정리 | 미할당 | 2026-08-26 |
```

- 이미 확정된 writer·기한·Epic은 다시 질문하지 않음
- 답변 전 pending 없음, 답변 후 두 Task만 정확히 복원
- 모든 Task 배경에 회의와 지시자 `skcc.x1042` 기록

### MTG9 — 의견·보류와 최종 합의

기준선:

```json
{
  "duedate": "2026-08-30",
  "priority": "P1-Critical"
}
```

- 보류된 priority 의견을 채택하고, 합의된 summary·description을 누락

개선 후:

```json
{
  "duedate": "2026-08-30",
  "summary": "[Catalog] Puffin 증거 패키지 정리",
  "description": "## 결정 배경\n이 회의와 요청·지시자 [~skcc.x1042]를 기록\n..."
}
```

- priority/component/labels/comment 없음
- 최종 합의와 무관한 `준서TL` 신원 질문 없음
- pending rationale에 과거 `priority, duedate` 문구가 남은 것은 다음 표현 정리 후보지만 실행 payload는 정확

## 마지막 full battery·실 UI 피드백 반영 대조

이번 개선은 새 case만 맞추는 예외 분기가 아니라 [EN v12 전체 54-case 및 실 UI 평가](./2026-08-16-en-v12-full-quality-cumulative-comparison.md)의 공통 실패를 회의 맥락에도 적용.

| 이전 피드백 | 이번 공통·구조적 반영 | 확인 |
|---|---|---|
| 시스템 영역 근거와 답변의 근거/참조가 분리·중복 | 단일 evidence index, source root + `[n-a]` observation, 본문 `[n][m]` citation 계약 유지 | MTG1·6 실제 reply, 관련 grounding 회귀 테스트 |
| 내부 검증 경고가 사용자 답변에 노출 | 경고는 내부 review에 보관하고 reply에서 제거 | MTG6 기준선 경고 → 개선 후 미노출 |
| ticket badge·mention·inline code 중첩 및 잘못된 사람 marker | canonical ticket marker와 confirmed username 기반 mention sealing | MTG1~9 reply/payload, mention 회귀 테스트 |
| 간결한 문체·heading·표·list 요구 | 결정/담당/미결/승인/근거의 짧은 section과 table 우선 | 최종 9건 직접 판독 |
| 조사 후에도 모르면 인터뷰, `알아서`라도 필수 정보 질문 | research-then-interview와 action-critical gap만 질문 | MTG8 첫 turn; MTG6은 질문 없이 조사로 닫힘 |
| 마지막 turn과 관계없는 과거 컨텍스트 개입 | final decision precedence와 rejected/hold field 제거 | MTG9 exact fields |
| 관련 없는 ticket·source를 답변에 출력 | topic anchor와 source relevance filter | MTG6의 `DL-9036` 제거 |
| 과도한 calls/token | meeting context를 한 번 canonicalize하고 task draft 재개 시 확정값 재사용 | calls -3, 시간 -15.4%, 비캐시 prompt -41.3% |

즉, 마지막 full battery에서 발견한 evidence/citation·relevance·interview·context precedence·효율 문제와 실 UI에서 발견한 경고 노출·badge/mention·가독성 문제를 모두 설계 입력으로 반영. 다만 이번 round에서 브라우저를 새로 클릭해 시각 회귀를 반복한 것은 아니며, raw marker 문자열과 기존 UI 렌더러 계약·회귀 테스트 및 Codex의 실제 출력 판독으로 확인.

## 구조적 개선 내용

- `meeting_context`: 비정형 문장을 발언/결정/담당/기한/지시자/검토자/미결로 정규화하는 canonical intermediate representation
- `RequestArchitect`: `회의록`이라는 정확한 단어가 없어도 문맥·발언 형식·첨부 발췌를 회의 맥락으로 판별
- `QuerySpecialist` / `QueryRunner`: 파일명·일반어 대신 기술 entity와 명시 ticket/doc을 query anchor로 사용하고, topic 관련 결과만 유지
- `WorkArchitect`: 명시 assignment를 workload 추천보다 우선, explicit unassigned 유지, 지시자/검토자와 assignee 분리
- interview resume: unresolved owner/scope만 질문하고 답변 뒤 이전 canonical task draft를 복원
- final-decision boundary: `보류`, `제안`, `의견` 필드를 제거하고 마지막 합의만 update payload로 유지
- terminology/identity sealing: 확인된 약어 원문·정의와 canonical username을 후속 생성·수정 단계까지 보존
- `identity` / `common` / 각 role prompt: 동일 규칙을 중복 예외가 아니라 공통 계약과 role별 Input/Output 책임으로 배치

## 자동 checker와 사람 판정 불일치

- EN v13은 자동 9/9이지만 사람 평가는 4.76. 자동 checker는 exact action/target/field/assignee·필수 문구를 보장하지만 문장 반복, generic DoD, source root 중첩, stale rationale까지 완전하게 판정하지 않음.
- MTG6은 자동 pass지만 첨부 파일명 평문, ticket observation 중첩, 외부 근거의 구체성 부족으로 G/R 각 4.0.
- MTG9은 exact payload 자동 pass지만 rationale에 이전 field 이름이 남아 R 4.5.
- 반대로 MTG1의 내부 자료 상충 고지는 자동 checker의 단순 happy path보다 안전한 동작. 불확실성을 숨기지 않았으므로 감점하지 않음.

## 검증

외부 의존성 없는 관련 회귀 테스트:

```text
485 passed, 3 warnings in 30.26s
```

실행 범위:

```text
tests/test_agent_meeting_context.py
tests/test_agent_grounding.py
tests/test_agent_draft.py
tests/test_agent_query_v2.py
tests/test_agent_intents.py
tests/test_agent_prompt_integrity.py
```

warning은 LangGraph/Starlette deprecation 2건과 Windows의 `.pytest_cache` 쓰기 권한 1건. 테스트 실패 없음. full offline suite는 PR GitHub Actions에서 수행하고 실제 API battery는 manual 정책 유지.

## 실패·재시도·제한사항

- 기준선 MTG6~9 실패 후 구조 개선을 수행하고 focused case로 원인을 닫은 다음 9건 전체 primary를 새 commit에서 재실행. 최종 보고 수치는 마지막 complete run만 사용.
- 개발 중 모델 변동으로 개별 focused/full 시도에서 MTG5~8이 간헐 실패한 raw도 ignored cache에 보존. 성공 결과만 골라 primary를 합성하지 않음.
- final primary는 1회라 평균·표준편차·신뢰구간이 없음. 비용 snapshot도 raw에 `unrecorded`라 절대 비용은 실행 시 provider usage 기록에 의존.
- 이전 54-case full 결과의 피드백을 설계에 사용했지만, 이번 실 API 재측정 범위는 강화된 meeting 9건. 다른 suite의 새 prompt 회귀는 PR offline full CI와 다음 multi-suite API round에서 별도 확인 필요.
- 이번 round는 브라우저 시각 회귀를 새로 수행하지 않았고 기존 실 UI 피드백, renderer contract, raw marker와 관련 unit test로 확인.

### 남은 개선 후보

- MTG6의 첨부 파일명을 검증된 document marker 또는 명시적 “사용자 첨부”로만 렌더링
- evidence root 아래 다른 ticket badge를 observation으로 넣지 않고 독립 source root로 승격
- MTG7·8의 generic `실행 로그와 테스트 결과` DoD를 산출물별 검증 기준으로 더 구체화
- update rationale을 final payload의 실제 key에서 다시 생성해 MTG9의 오래된 field 이름 잔존 제거
- qualification 비교가 필요하면 같은 두 후보를 후보 순서 교차, case별 cold cache, 5회 이상 반복해 평균·표준편차·95% CI와 paired delta 산출

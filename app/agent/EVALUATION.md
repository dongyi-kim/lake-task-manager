# LTM Agent 평가 표준

> Source of truth: [`evaluation_protocol.json`](evaluation_protocol.json)  
> 현재 protocol: `1.0.0` / human rubric: `1.0.0`

이 문서는 prompt, Role, Tool, workflow 후보의 품질·시간·token을 같은 자로 비교하기 위한 실행 규약. 과거 보고서의 점수는 해당 보고서가 선언한 규약으로만 해석하며, 버전이 없거나 `comparabilityKey`가 다른 점수를 한 시계열처럼 비교하지 않음

## 1. 버전 식별자

서로 독립적인 세 버전을 기록

| 식별자 | 고정하는 대상 | 변경 규칙 |
|---|---|---|
| `protocolVersion` | 실행 선택, 실패·재시도 처리, 집계식, 비교 가능성 | 산식·선택 정책 변경은 major |
| `rubricVersion` | 사람 평가 축, 가중치, 치명 결함 cap | 축·가중치·cap 변경은 major |
| `batteryVersion` | suite별 case 입력·기대 계약·checker | case 의미 변경/삭제는 major, 추가는 minor |

각 battery 결과에는 case와 checker에서 계산한 `batteryManifestSha256`, mock/config에서 계산한 `dataManifestSha256`도 기록. 버전을 올리지 않고 내용을 바꾸면 hash가 달라져 비교 불가로 탐지

## 2. 실행 종류와 결과 선택

### `exploratory`

- 1회 실행 허용
- 결함 탐색과 방향 확인용
- production 전환 또는 역사적 우열의 근거로 단독 사용 금지

### `qualification`

- 같은 `protocolVersion`, `rubricVersion`, suite별 `batteryVersion`과 manifest, mock data, model routing, 후보 commit을 사용
- 후보별 최소 5회
- 후보 순서를 무작위화하고 전체 횟수에서 각 후보의 선행 횟수가 균형을 이루도록 배치
- full battery와 clean commit 필수
- 일부 case만 실행한 focused/closure run은 qualification 점수에 포함하지 않음

### Primary evidence

`complete-run-no-substitution` 고정

- 한 full run의 실패를 나중의 성공한 focused run으로 교체 금지
- 수정 후 결과는 새 candidate commit과 새 run group으로 전체 battery를 다시 실행
- closure run은 원인 확인용 보조 증거로만 별도 표기
- 기술 오류와 retry를 포함한 모든 attempt 보존. 성공 attempt만 골라 latency·품질 점수 계산 금지

## 3. 고정 실행 조건

후보 비교 시 아래 값이 모두 같아야 함

- main/complex model과 simple model
- provider, 역할별 model routing, temperature 등 runtime 설정
- mock Jira·Confluence·comment·people data와 search config
- battery case·checker와 실행할 case 집합
- retry policy, concurrency, cache 초기화 정책
- protocol/rubric 및 사람 평가 양식

현재 production 비교 profile의 기본 routing은 main/complex=`gpt-4o`, simple=`gpt-4o-mini`. 다른 모델로 실행할 수 있으나 동일 run group의 모든 후보에 똑같이 적용하고 별도 profile로 기록

## 4. 정성평가 주체와 자동화 경계

정성평가의 주체는 raw output을 직접 읽는 **Codex 또는 Claude 작업 에이전트**로 고정

- LTM runtime LLM(`gpt-4o`, `gpt-4o-mini` 등)은 평가 대상. LTM 내부 Role이나 동일 production
  endpoint의 추가 호출을 evaluator 또는 LLM-as-judge로 사용 금지
- Codex/Claude evaluator가 실제 reply, 질문 form, card/payload, description/comment 전문을 직접 읽고
  인간 사용자의 관점에서 축별 점수와 근거를 작성
- 자동 도구는 deterministic contract 위반 탐지, token/call/latency 수집, 정해진 산술 집계만 담당.
  자동 checker의 pass/fail이나 별도 LLM 점수를 정성점수로 변환 금지
- 평가 결과마다 `evaluatorAgentFamily`(`codex` 또는 `claude`), `evaluatorAgentModel`,
  `directRawOutputReview=true`, `ltmLlmUsedAsJudge=false`, reviewer 수와 blind 여부를 기록
- 한 run group의 모든 후보는 동일한 evaluator family/model 또는 동일한 reviewer panel이 같은 rubric으로
  평가. 중간에 evaluator가 바뀌면 새 run group으로 분리
- 가능하면 후보 이름을 가리고 순서를 섞어 검토. 비blind이거나 변경 작업과 평가를 같은 에이전트가
  수행했다면 제한사항에 명시

OpenAI의 [Evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)가
권고하는 task-specific test, 전체 logging, 자동 지표와 human judgment의 결합, 지속적 dataset 확장을
따르되, 이 프로젝트의 정성 판정자는 위 규칙에 따라 Codex/Claude로 더 좁게 제한

## 5. 사람 관점 품질 rubric `1.0.0`

각 실제 reply, 질문 form, card/payload, description/comment 전문을 읽고 다섯 축을 각각 `1.0–5.0`, `0.5` 간격으로 평가. 자동 checker 점수를 사람 점수로 대체하지 않음

| 축 | 가중치 | 판단 질문 |
|---|---:|---|
| 요청 충족·완결성 | 20% | 요청한 대상·범위·형식·후속 행동이 빠짐없는가 |
| 사실성·근거성 | 20% | 상태·날짜·사람·티켓·문서·수치가 근거와 일치하는가 |
| 계약 일관성·실행 가능성 | 20% | reply·질문·card/payload가 일치하고 실제 승인 가능한가 |
| 안전성·불확실성 처리 | 20% | 모호함을 발명하지 않고 질문 또는 구체적 open fact로 남기는가 |
| 가독성·간결성·렌더링 | 20% | 구조가 짧고 명확하며 badge·mention·link가 정상인가 |

case 점수는 다섯 축 가중평균. 다음 치명 결함은 평균 후 cap 적용

| 점수 | 공통 anchor |
|---:|---|
| 5 | 요청을 완결적으로 충족하며 의미 있는 결함 없음 |
| 4 | 바로 사용 가능, 사소한 수정만 필요 |
| 3 | 핵심은 유용하나 중요한 수정 필요 |
| 2 | 일부 단서는 유용하나 주요 요청 미충족 또는 신뢰 곤란 |
| 1 | 사용 불가 또는 중대한 사실·안전·계약 실패 |

`0.5` 점수는 인접한 두 anchor의 중간. 개인적인 선호가 아니라 해당 축의 관찰 가능한 결함과 실제
출력 근거로 판정

| 코드 | case 최고점 |
|---|---:|
| `fabricated_fact_or_entity` | 2.0 |
| `unsafe_or_unapproved_write` | 2.0 |
| `reply_payload_contradiction` | 3.0 |
| `material_omission` | 3.0 |

모든 case에는 축별 점수, cap 코드, 실제 출력 발췌, 판단 근거를 남김. 가능하면 후보명을 가리고 순서를 섞어 평가. 단일 reviewer 또는 비blind 평가는 보고서 제한사항에 명시

## 6. 집계

- `case score`: 축별 가중평균 후 cap, 소수 둘째 자리
- `suite score`: 해당 suite의 모든 case-attempt 점수 산술평균
- `overall score`: 모든 suite의 모든 case-attempt 점수 산술평균. suite 평균을 다시 동일 가중 평균하지 않음
- case가 누락되면 0점으로 채우지 않고 qualification 자체를 invalid 처리
- quality는 평균과 함께 치명 결함률·자동 계약 실패율을 보고
- latency는 모든 case-attempt에 대한 nearest-rank 방식 `p50/p95`, token·call·cost는 총량과
  case-attempt당 값을 함께 보고
- 반복 실행에서는 최선/최악 run을 대표값으로 선택하지 않고 모든 attempt를 집계

## 7. Battery 변경과 역사 비교

- case 추가: battery minor 증가
- 기존 case 입력·기대 계약·checker 의미 변경 또는 삭제: battery major 증가
- 오탈자만 수정: patch 증가. 결과 의미가 달라지면 patch 사용 금지
- battery가 다른 후보를 비교해야 하면 공통 case subset 결과와 전체 새 battery 결과를 분리
- production 전환 판단은 모든 후보를 같은 최신 battery로 다시 실행
- 과거 unversioned 또는 closure-substituted 점수는 참고값으로만 표시하고 현재 점수와 증감 계산 금지

## 8. 보고서 필수 내용

보고서 또는 PR Description에 다음 section을 모두 포함

1. `측정 식별자`: protocol/rubric/battery version과 manifest, candidate commit/prompt version
2. `비교 가능성 및 evidence 선택`: `comparabilityKey`, primary/closure 구분, 비교 가능 여부
3. `실행 조건`: model routing, provider, mock data hash, 반복 수, 후보 순서, retry/cache 정책
4. `배터리 범위`: suite별 전체/실행/누락 case
5. `정량 결과`: 시간 p50/p95, token, calls, cost, 기술 실패·retry
6. `사람 품질 평가 기준`: Codex/Claude evaluator 식별자와 금지된 LTM LLM judge 미사용 선언,
   rubric version, 축·가중치·cap, reviewer 수와 blinding 여부
7. `배터리별 실제 출력과 평가`: 차이 나는 전문 또는 충분한 발췌, 축별 점수와 의견
8. `실패·재시도·제한사항`: 자동 실패, 치명 결함, 누락, 단일 run 여부

`tools/agent_eval_protocol.py`가 raw JSON에 식별 metadata를 넣고 표준 Markdown block을 생성·검증. 버전 없는 과거 결과를 v1 결과로 소급 표기하지 않음

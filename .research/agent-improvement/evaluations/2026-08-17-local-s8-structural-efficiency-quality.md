# Local Qwen S8 구조·효율 개선 집중 평가

> 결론: 복합 자료 조사 S8에서 반복 검색을 제거해 **695.9초 → 229.4초(-67.0%)**,
> **13 calls → 4 calls(-69.2%)**, **194,333 → 25,724 tokens(-86.8%)**로 감소. Codex가
> 실제 답변과 조회 근거를 직접 판독한 사람 품질은 **3.70 → 4.40/5**로 평가. 속도만을 위한
> 축약이 아니라 본문·댓글·문서 선취합, 오류 시 ReAct 복귀, 시간축 충돌 판정, canonical
> 근거 인덱스를 함께 적용한 결과. 이 실행은 S8 1건 focused exploratory run이므로 full battery
> qualification이나 전체 Agent 평균으로 해석하지 않음

## 측정 식별자

| 항목 | 값 |
|---|---|
| base commit / candidate state | `fafd8ae15ed0843364c3a54f1d6ac243b4b62296` / tracked patch 적용으로 dirty |
| protocol / rubric | `2.0.0` / `2.0.0` |
| suite / battery | `conversation` / `3.2.0` |
| measured promptVersion | `en-role-contract-v13` |
| post-run promptVersion | `en-role-contract-v14` — description 계획을 결과로 승격하지 않는 계약 추가 |
| model routing | main·simple 모두 `ltm-qwen3.6-35b-a3b` |
| provider / data | Mac MLX `openai_compat` / `jira820-mock-v1` |
| battery manifest | `dce890630ce3a1321bdb07e5ec9337776ebc77a6858c0442851b61cae01c230a` |
| review spec manifest | `cbfec43c80dbe83bc9a52e9aebb19904fdc5fb68b2920112059b44aeb1912ecf` |
| data manifest | `87e592d3cc136e62e135e5d81c76c91121da0e85d18fdc0b74bd0304f0521621` |
| comparability key | `2d43d45bd15c721c964b1d7c87f56b540183cf195c8629a8a499a5f75dafa4df` |
| cache / isolation | case별 cold private cache / separate process |
| qualitative evaluator | Codex가 raw output·query plan/result·evidence를 직접 판독 |
| LTM LLM judge | 사용하지 않음 |
| qualification | `false` — S8만 1회, candidate dirty, explicit qualification run group 아님 |

Raw 결과:

- 개선 전: `.cache/agent-evaluation/2026-08-17-openai-vs-local-fafd8ae-r01/local/conversation.json`
- 1차 구조 개선: `.cache/agent-evaluation/2026-08-17-local-structural-focused-r01/conversation.json`
- 품질 보정 중간: `.cache/agent-evaluation/2026-08-17-local-structural-quality-r02/conversation.json`
- 최종 집중 측정: `.cache/agent-evaluation/2026-08-17-local-structural-quality-r03/conversation.json`

## 개선 설계

1. Query Runner가 research·복합 source 요청에서 상위 Jira 8건과 Confluence 4건까지 본문·댓글을
   결정적으로 선취합
2. 모든 QueryPlan source가 성공하면 Research Analyst가 재검색하지 않고 1회 종합
3. 누락·source mismatch·query/materialization error가 하나라도 있으면 기존 ReAct로 자동 복귀
4. web 결과가 QueryPlan에 있으면 같은 `web_context`를 두 번 싣지 않음
5. 오래된 `미수행`과 이후 직접 완료 증거를 정상 진행으로 판정. 같은 범위의 동시·최신 자료가
   계속 다를 때만 미해결 충돌로 유지
6. grounding 전에 검증된 ticket badge, document URL, source identity를 canonicalize해 오탐성
   Result Integrator 재작성 1회를 제거
7. typed badge의 inline-code 중첩, URL token의 `}}` 오염, Confluence page-id 중복, 이전 source
   번호 잔존을 코드로 정규화

## 정량 비교

| 단계 | 시간 | calls | prompt | completion | total | 근거 위반 | 핵심 상태 |
|---|---:|---:|---:|---:|---:|---:|---|
| 개선 전 r01 | 695.9s | 13 | — | — | 194,333 | 0 | Research 9회 반복, 단일 인덱스 실패 |
| 1차 구조 개선 | 230.0s | 4 | 21,674 | 4,300 | 25,974 | 1 | 반복 제거, 렌더·상충 결함 잔존 |
| 품질 보정 r02 | 292.8s | 5 | 28,565 | 6,097 | 34,662 | 1 | grounding 오탐으로 재작성 1회 |
| 최종 r03 | **229.4s** | **4** | **21,612** | **4,112** | **25,724** | **0** | 모든 S8 자동 계약 통과 |

개선 전 대비:

- 시간 `-466.5s`, **-67.0%**
- calls `-9`, **-69.2%**
- total tokens `-168,609`, **-86.8%**
- Research Analyst `9 calls / 172,035 tokens / 527.5s` → `1 call / 10,302 tokens / 101.4s`
- Result Integrator 오탐 재작성 제거: r02 `2 calls / 15,984 tokens / 112.8s` →
  r03 `1 call / 7,708 tokens / 54.2s`

최종 역할별:

| Role | calls | tokens | 시간 |
|---|---:|---:|---:|
| Request Architect | 1 | 4,490 | 42.3s |
| Query Specialist | 1 | 3,224 | 28.1s |
| Research Analyst | 1 | 10,302 | 101.4s |
| Result Integrator | 1 | 7,708 | 54.2s |

## 사람 품질 평가

공통 5축 `F/G/C/S/R`, 각 20%. 5는 실사용 전 수정 불필요, 4는 사소한 수정만 필요,
3은 중요한 수정 필요, 2는 주요 요청 실패, 1은 사용 불가

| 후보 | F | G | C | S | R | 종합 |
|---|---:|---:|---:|---:|---:|---:|
| 개선 전 r01 | 4.5 | 3.5 | 4.0 | 3.5 | 3.0 | **3.70** |
| 최종 r03 | 4.5 | 4.0 | 4.5 | 4.5 | 4.5 | **4.40** |

### 개선 전 실제 출력과 판정

```text
Writer PoC: 20개 후보 중 5개 표본에 대해 Puffin 파일 생성 결과 확보
...
확인 필요
- 내부 기록 상충: 한 기록은 PoC 완료, 다른 기록은 미수행으로 기술
...
[8] [회의록] Iceberg Puffin NDV 도입 실무회의
[9] [Lake] Iceberg Puffin NDV 적용 검토 노트
[10] Puffin Spec - Apache Iceberg™
```

- 최신 Jira comment의 writer 파일 생성 결과와 오래된 Confluence `미수행` 메모를 시간축 없이
  미해결 충돌로 처리
- 문서·외부 source가 URL 없는 별도 root로 중복되고 source-quality row도 실제 한계 대신 같은
  문구 반복
- 조사 자체는 핵심 결론을 찾았지만 사람은 경고와 11개 근거를 다시 해석해야 함

### 최종 실제 출력과 판정

```text
### 결론: NDV 운영 적용 불가 (검증 전 보류)

Iceberg Puffin NDV writer는 PoC를 통해 파일 생성이 가능하나,
StarRocks reader의 실제 소비 여부가 검증되지 않아 현재로서는 운영 적용을 승인할 수 없음.
...
### 출처 평가
| 출처 | 신뢰도 | 요청 적합성 | 한계 |
...
### 근거
[1] {{ticket-detail:DL-9201}}
- [1-a] ... writer PoC 수행, Puffin 파일 생성 완료
[3] {{ticket-detail:DL-9202}}
- [3-a] StarRocks reader와 optimizer가 NDV를 실제로 소비하는지 확인 중
```

- 결론 우선, writer 생성 성공과 reader 소비 미검증을 분리해 의사결정 가능
- Jira·comment·Confluence·공식 web을 실제 조회했고 모든 본문 marker가 하나의 `### 근거`
  인덱스로 연결
- 외부 공식 사양을 내부 readiness 증거로 승격하지 않고 보조 source로 한정
- 자동 검증: `복합자료조회`, `본문근거연결`, `복합근거단일인덱스`, `출처평가완결` 모두 true,
  근거 위반·후검증 위반 0
- 사람 판독상 `NDV 오차 기록 계획`을 `기록 가능 확인`으로 다소 강하게 표현한 한 문장과 문서명
  뒤 citation 1건의 오배치가 minor. 후자는 실행 뒤 deterministic source-title rebind 회귀 테스트를
  추가했으나 동일 실제 API run을 다시 수행하지 않았으므로 r03 점수에는 보정 반영하지 않음

## 서버 튜닝 결과

현재 MLX 설정:

- `prefill-step-size=2048`
- prompt cache `8 entries / 3 GiB`
- Qwen3.6-35B-A3B 4bit, thinking off profile

Prompt cache A/B:

| 입력 | cold TTFT | 같은 prefix 재호출 | cached tokens |
|---|---:|---:|---:|
| 3.4K prompt A | 6.428s | 0.260s | 3,384 |
| 3.4K prompt B | 6.109s | 0.280s | 3,417 |
| 8.8K long context | 15.331s | 0.260s | 8,843 |

`mlx-community/Qwen3.5-0.8B-4bit`을 동일 tokenizer hash로 내려받아 `4 draft tokens` speculative
decoding을 A/B. mlx-lm 0.31.3이 Qwen3.6 hybrid model의 `ArraysCache`를 trimmable cache로 처리하지
못해 모든 응답이 빈 본문으로 종료했고 server log에 아래 오류 확인

```text
ValueError: Speculative decoding requires a trimmable prompt cache (got {'ArraysCache'}).
```

품질 게이트 실패로 즉시 원래 plist 복구. speculative 설정은 채택하지 않음. 기존 cold-prefill
측정에서 2,048/4,096/8,192 차이는 warm-order 편향 범위였고 prompt cache hit가 이미 0.26초이므로,
현시점의 주된 병목은 MLX 옵션보다 Agent의 반복 LLM loop와 큰 context 재전송

## 검증

- Agent query/tool/grounding/graph/prompt integrity 회귀: **280 passed**
- post-run deterministic source-title rebind·빈 source 제거 focused 회귀: **5 passed**
- 실제 S8 최종: 4 calls, 자동 계약 전부 통과, 오류 0
- full battery: 사용자의 중단 지시에 따라 수행하지 않음

## 남은 과제

- r03에서 계획 문장을 완료 증거처럼 강화한 한 문장: Result Integrator 계약에
  `description=계획/기준, result body/comment=완료 증거` 규칙 추가. 다음 해당 case 재측정에서 확인
- simple tier를 Qwen3.5-4B로 분리하는 안은 25-case Request Architect 평가 21/25(84%)라 현재 품질
  기준 미달. Mac/Windows 배치 모두 채택하지 않음
- 다음 full battery는 이 focused 개선이 다른 Role/suite에 회귀가 없음을 확인하는 별도 manual run으로 수행

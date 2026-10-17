# MAXIS/Dify native Agent 실서버 점검 — 2026-10-17

## 결론

**새 합성 Knowledge와 조회 Workflow 3개는 실제 실행 성공. Research Agent는 두 전략 모두
기능 검증 실패. LTM 사용자 경로 전환·품질 개선은 아직 완료하지 않았다.**

현재 공유 모델의 `function_calling_type=no_call` 설정을 확인했다. Function Calling Agent가
도구 없이 답한 현상과 부합하는 구체적인 설정 결함이다. 설정 교정 전후 재검증은 아직 못 했으므로
유일한 원인 또는 교정만으로 해결된다고 단정하지 않는다. 최초 공유 설정 변경은 자동 안전 검토에서
차단되어 실행되지 않았다. **후속 범위 승인은 완료됐지만 승인된 PUT는 credential validation에
실패하여 옵션은 `no_call`로 유지**됐다. 아래 최초 점검 기록과 후속 상태를 구분한다.

이 문서는 플랫폼 기능 점검 기록이며 정식 품질 배터리 평가서가 아니다. 아래의 성공은 고정 합성
입력 1회에 한정한다. 업무 완료율·성능 개선율·모델 간 우열·production 적합성을 뜻하지 않는다.

## 실제로 만든 것과 유지한 것

```text
LTM TaskFlow API transport [로컬 구현·사용자 경로 미연결]

Dify 합성 Research [실서버 게시·실행했으나 기능 실패]
  Start → 고정 합성 입력 검사 → Native Agent → research_candidate
                                  ├ glossary_lookup → Code
                                  ├ evidence_lookup → Code
                                  └ domain_retrieve → Knowledge Retrieval → 출처/내용 검사

MAXIS private wrapper [미생성] → 부모 TaskFlow/WorkDraft [미배포]
```

- 기존 Core/Editor/PMO 앱, 게시본, 키, 활성 profile, LTM route는 변경하지 않았다.
- 신규 앱 5개는 모두 공개 웹사이트를 비활성화했다. Workspace 전체 앱 13개는 모두 LTM 앱으로
  확인했지만, 이것만으로 각 앱의 운영 영향이 검증된 것은 아니다.
- 신규 `only_me` economy Knowledge: `LTM TaskFlow Synthetic 20261017A`, 문서 1개,
  indexing 1/1 완료. 실제 업무 문서나 내부 48개 용어집은 올리지 않았다.
- Dataset: `0d2759b3-6a04-46c7-99d9-9d3ec8eaa15e`.
  Document: `6bfb6ac8-4159-44a4-86d2-1c032466e7f0`.
  고정 문서 SHA-256: `6802e2f7a6deab78e5506f90bb23ba7247800ed09bc6eba62b96b19a4c36fb94`.

| 신규 앱 | app UUID | 게시 workflow UUID |
|---|---|---|
| glossary_lookup | e6582a94-bfa9-4645-aa23-84c7365f1bbf | 0671f13d-2379-4f4f-b1ce-ee44922cafe7 |
| evidence_lookup | a95cf47c-8fee-4696-bda2-f3f6ef87feac | c67b79cd-428c-47b1-b930-b7831623969a |
| domain_retrieve | 4a9b56df-fb4d-472b-8d74-f8be43b9a5c6 | 8d05900b-f061-44af-bcad-12788b8a01c3 |
| research — ReAct | c215c567-5ddc-4adb-ab23-228a4e3df756 | 6d3bf800-ec51-4619-8c06-8e3fb13fcfcb |
| research — Function Calling | b522e9f5-d82e-4d78-bb29-d5540877b4c2 | e6ce93f2-075f-4008-81d3-9e880f38ff79 |

생성·게시·tool provider 식별자와 정확한 DSL hash는 ignored
`.cache/maxis/synthetic-assets-20261017.json`에 보존했다. 재개 시 새로 만들지 말고 이 자산부터 조회한다.

## 실행 결과와 직접 확인한 출력

모두 HTTP 200이었지만 native 성공 및 실제 기능 성공은 따로 판정했다.

| 사례 | native 상태 | 직접 판독한 기능 결과 | 관측 지연 / native elapsed | native total tokens |
|---|---|---|---|---:|
| glossary_lookup | succeeded | `syn-glossary-checkpoint`와 고정 정의 일치 | 0.297 / 0.182063초 | 0 |
| evidence_lookup | succeeded | `syn-task-001` 요청 반환, 담당자·일정 미제공 보존 | 1.562 / 0.142872초 | 0 |
| domain_retrieve | succeeded | 실제 Retrieval 실행, `syn-domain-rules`와 문서 전체 일치 | 3.484 / 0.269199초 | 0 |
| research / ReAct | failed | Agent 노드 실패, 최종 본문 없음 | 0.688 / 0.359728초 | 0 |
| research / Function Calling | succeeded | 도구 CALL 관측 없음, 존재하는 근거 3개 모두 `not_found` | 2.610 / 2.265671초 | 1,056 |

조회 도구의 출력 예:

- 용어: “합성 배치가 재시작할 위치를 기록한 상태 표식. 실제 시스템의 정의가 아님.”
- 근거: “SYN-LAKE 배치의 SYN-CHECKPOINT 재시작 검증 Task를 조사한다. 담당자와 일정은 미제공.”
- 규칙: “Task 초안에는 목적, 포함 범위, 완료 조건, 근거를 구분한다.”

ReAct의 서버 오류는 `litellm.UnsupportedParamsError`이며 `gpt-5.4-mini` 경로에서 `stop`
파라미터를 지원하지 않는다는 내용이었다. 전역 `drop_params` 변경으로 숨기지 않았다.
실패 run: `5cec6bf0-6ff1-46c5-a215-f006ff76bc08`.

별도 Function Calling 앱은 “SYN-CHECKPOINT의 뜻: not_found”, “syn-task-001의 요청 내용:
not_found”, “draft_rules의 초안 작성 기준: not_found”를 출력했다. 세 조회는 이미 독립 실행에서
자료 존재를 확인했으므로 **업무상 사용 불가**다. 담당자·일정을 발명하지 않은 것은 맞지만,
조회하지 않은 상태를 자료 부재처럼 설명한 실패는 남는다.
run: `496b300f-dfb5-4571-b2e2-e8c51d21acb7`.

자동 native 성공과 직접 기능 판정이 어긋난 사례가 Function Calling이다. raw의
`status=succeeded`는 보존하며 품질 성공으로 재해석하지 않는다. 정식 benchmark checker를
변경하거나 이전 결과를 합격으로 재채점하지 않았다.

## 모델 설정 문제와 변경 경계

현재 관측값:

- `function_calling_type=no_call`
- `stream_function_calling=supported`
- model features: `[stream-tool-call]`

설치된 Agent `0.0.18`의 Function Calling 전략은 도구를 모델 호출에 전달한다.
공식 OpenAI-compatible SDK에서 `no_call`은 tool/function 전달과 tool-call 응답 처리를
비활성화하는 경로다. 이는 현재 현상을 설명하는 강한 근거지만, 배포된 모델 SDK의 정확한
실행 trace까지 검증한 것은 아니다.
[Agent 전략 소스](https://raw.githubusercontent.com/langgenius/dify-official-plugins/7ea0dafb34abfedafd9b8706610c5136bc97077e/agent-strategies/cot_agent/strategies/function_calling.py),
[SDK 설정 처리](https://raw.githubusercontent.com/langgenius/dify-plugin-sdks/main/src/dify_plugin/interfaces/model/openai_compatible/llm.py).

준비한 변경은 같은 credential ID·키·endpoint·모델을 유지하고 해당 옵션만 `tool_call`로 바꾸는
것이었다. 최초에는 **실행 전 안전 검토가 거부하여 요청 자체가 발송되지 않았다.** 이후 공유 모델
설정으로 영향받을 수 있는 앱 13개 범위에 대해 사용자가 승인했다. 승인된 PUT 두 시도는 HTTP 400
credential validation(상위 인증 검사 401)으로 실패했고, read-back의 옵션은 `no_call`이었다.
이 실패는 credential 만료를 확정하는 증거가 아니다.

추가 읽기 전용 확인에서 기본 credential 조회는 현재 ID로 성공했으나 동일 ID를 명시한 조회는
`credential not found`였다. custom model 목록의 연결 ID는 같고 available credential 목록은
비어 있었다. 모델/credential metadata 연결 불일치가 유력하나 서버 DB·배포 버전을 확인하지
못했으므로 원인을 확정하지 않는다. 현재 차단은 사용자 승인 부재가 아니라 서버 조회/검증 불일치다.
운영자가 tenant·provider·model·model type·연결 ID와 migration/version을 확인해야 한다.
키나 encrypted config 공개, 새 credential 생성, 검증 우회 또는 동일 PUT 반복은 하지 않았다.

운영자 교정 후 순서: 명시적 ID 조회 성공과 동일 identity 확인 → 비밀 없는 옵션 read-back →
정상 검증을 거친 한 필드 교정 → 새 run group에서 실제 3개 tool CALL 및
source 반환 확인 → 기존 Core/Editor/PMO 회귀 → 원치 않는 영향이 있으면 원래 옵션 복원.
설정 저장 자체의 provider 검증 호출 수·비용도 미확인이므로 0으로 기록하지 않는다.

## MAXIS Node는 별도 미완료

MAXIS의 `Lake Task Manager` 프로젝트는 승인 상태지만 Agent/MCP/실행 Node는 각각 0개다.
Dify의 native Agent 노드를 MAXIS 등록 Node라고 부르면 안 된다.

현재 UI·매뉴얼상 Node 저장은 Git 저장소·초기 scaffold commit·Jenkins Job 생성/연결도 수행한다.
Docker 배포와 Market 공개는 별도다. 생성 저장소의 비공개 기본값과 secret binding을 확인하지
못해 `ltm-research-wrapper-synthetic-v1` 생성은 보류했다. 인증 실패나 MAXIS 미지원 판정이 아니다.
이 점검에서는 MAXIS 서버 변경·Node 생성·배포·모델 호출을 하지 않았다.
증적: `.cache/maxis/maxis-node-observation-20261017.json`, `maxis-node-create-gate-20261017.json`.

## 측정 식별자·제한사항

- 성격: `exploratory_capability_not_qualification`, 고정 합성 자료, 사례별 1회, 자동 retry 없음.
  첫 run group 4/4, 두 번째는 Function Calling 1/1. 전체 업무 품질 배터리는 0회다.
- candidate base commit `63d3229`, branch `codex/maxis-taskflow-synthetic`, 실행 당시 dirty tree.
  정확한 배포 DSL hash는 각 raw에 있다. 나중의 소스 커밋을 당시 clean candidate로 소급하지 않는다.
- protocol `4.0.0`, rubric `2.0.0`, battery `synthetic-research-capability.v1`,
  prompt `synthetic-research-2026.10.17-v1`.
- 첫 data manifest `288ea50ed62ad5225501e70181e3b652c1b8bb23213dd169354c37ca3eb2e2b8`.
  두 번째 `432b167457dc6e4f7ac2a9615505086c5eaf97159c88826be2ec6a3682989709`.
  Function Calling 사례 alias 추가로 다르며 paired 우열 비교를 하지 않는다.
- `batteryManifestSha256`, `specializedReviewSpecSha256`, `benchmarkKey`, `candidateKey`,
  `comparabilityKey`, `evaluationHarnessTreeSha256`: 이 전용 capability runner에서 미산출.
  **정식 평가 계약 미충족**이며 다섯 축 점수·종합 점수·qualification 합격을 부여하지 않는다.
- 모델: `gpt-5.4-mini`, temp 0.1, max_tokens 4096, provider `openai_api_compatible`.
  조회 3개에는 모델 노드 없음. Agent plugin `0.0.18`, model plugin `0.0.16`.
  DSL `0.6.0`은 확인했으나 설치된 Dify 서버 버전 자체는 미확인이다.
- 실실행은 **console draft**이며 게시 UUID의 Service API end-to-end 실행 증거가 아니다.
  호출 전 draft graph 일치, 실행 시작/종료 UUID, Research의 도구 provider 및 게시 그래프를 확인했다.
  이는 강제 불변 배포·ACL·총예산·retention 보장과 다르다.
- 모델 총호출 수·실과금·cached tokens는 unknown. native token 0이나 price `0`을 무료라고
  해석하지 않는다. 위 지연은 단일 표본이며 p50/p95·사용자 대기시간·개선율로 환산하지 않는다.
- 판독자: Codex, evaluator model 정확한 식별자는 미제공. 직접 raw 5개 판독,
  `directRawOutputReview=true`, `ltmLlmUsedAsJudge=false`, reviewer 1명, 비blind이며 구현자와 동일.
  공통 rubric 전체 checklist 채점·UI 렌더 검증은 미수행. 이 기록은 실패 원인 진단이지 완료된 정성 평가가 아니다.
- 서버 캐시 초기화·provider cache 통제·전체 배터리 격리 계약은 검증하지 않았다. 내부 데이터·Jira 쓰기 없음.
  서버 reasoning 보존 정책은 미검증이고 로컬 raw에는 hidden reasoning을 저장하지 않았다.

raw는 다음 ignored 경로에 원본 그대로 보존한다. Git에는 이 요약만 넣는다.

- `.cache/agent-evaluation/maxis-native-synthetic-20261017/` — glossary/evidence/domain/research JSON 4개.
- `.cache/agent-evaluation/maxis-native-synthetic-20261017-function-calling/` — Function Calling JSON 1개.

과거 [Role component 진단](maxis-role-component-20261008-eligibility.md)은 비교 조건이 달라
증감 기준선으로 쓰지 않는다. 다음 단계는 공유 모델 credential 조회 불일치 교정과 기능 재검증이다.
후속 [component baseline 직접 평가](maxis-component-baseline-20261017.md)는 별도의 고정 입력
16건이며 이 native canary의 재실행이 아니다. 로컬 session preview 연결은 별도 구현으로 진행하되,
TaskFlow 부모·MAXIS wrapper 게시·운영 활성화·정식 업무 배터리 완료로 해석하지 않는다.

# Editor selector 입력 v2 — 선택과 작성 계약 분리

로컬 구현·검증 기록이며 새 원격 품질 결과가 아니다. 별도 브랜치
`codex/maxis-editor-selector-input-v2`, 기준 commit `1392751247f6176c5b6a06e1068e42a6cac2825a`.

## 확인한 문제

기존 selector system prompt와 구조화 출력 schema는 `editor-draft-spec.v2`를 요구하지만,
입력의 `_draft_contract()`에는 다음 단계의 `editor-plan.v2` skeleton과 `sections=[]`
지시가 들어 있었다. 선택 담당에게 작성·조립 계약까지 전달되는 불일치다.
그 skeleton을 선택 Guard에 전달하면 `invalid_editor_draft_spec`로 거부된다.
올바른 DraftSpec은 같은 원본 pack에서 통과하므로 Guard를 완화할 이유는 없다.

[v23 Editor 관측](evaluations/maxis-editor-component-20261008-core-control.md)의 CMP3은
제공된 초안을 다시 요구했고 CMP8은 DraftSpec Guard에서 실패했다.
저장된 CMP3 pack을 기존 순수 `prepare()`에 통과시키면 `seed_visible`은 정확히 보존된다.
관측된 실패를 seed 유실로 단정하지 않는다. 당시 selector의 실제 거부 전 JSON은
content-free native 증적에 없으므로, 계약 혼선이 그 원격 실패의 원인이라고도 확정하지 않는다.

## 단일 책임과 변경 범위

| 단계 | 입력·출력 | 변경 |
|---|---|---|
| Editor Input | 원본 pack → `editor-input.v2` | selector용 작성 계약 제거; 요청·seed·근거·엔티티 보존 |
| DraftSpec LLM | 입력 v2 → `editor-draft-spec.v2` | 입력 버전만 갱신; 선택 출력 schema 유지 |
| DraftSpec Guard / Enforcer | v2와 원본 pack을 대조 | exact fields/version 갱신; 기존 재결속·선택 권한 유지 |
| Writer | `editor-writer-input.v2` → `editor-writing.v1` | 자체 작성 계약·출력 불변 |
| Compiler / Assembler | `editor-plan.v2` → 기존 응답 | HTML·seed·claim·citation 및 최종 Guard 불변 |

필드 제거는 내부 breaking change이므로 v1을 같은 이름으로 조용히 수락하지 않는다.
Producer·Guard·Enforcer·prompt·embedded DSL을 하나의 후보 그래프로 함께 갱신한다.
v1이나 v2에 legacy 필드를 더한 혼합 입력은 실패 처리하며 alias/fallback을 만들지 않는다.
후속 게시에서도 새 published workflow UUID를 사용해 전체 그래프를 검증해야 한다.
기존 실행·평가 raw는 보존하고, 과거 v1 내부 payload를 새 v2 단계에서 재개하지 않는다.

App HTTP API, CLI, 환경 설정, semantic Role roster, 모델, Knowledge, 원본 EvidencePack,
외부 `remote-role-result.v1` 계약은 변경 대상이 아니다. 새 Agent·LLM 호출·노드를 추가하지 않는다.
선택된 source/unit ID, 링크, parent/child 소유권, bool·상태 검증과 9,000바이트 한도도 유지한다.

## 검증 상태

회귀를 먼저 작성해 **5 fail / 2 pass**를 확인했다(0.56초). 두 모드의 새 투영 기대와
기존 writer/compiler golden 2건은 v1 입력 때문에 실패했고, legacy 필드를 가진 v1을
수락하던 1건도 재현했다. 나머지 혼합 거부 2건은 기존에도 통과했다.
수정 후 입력·라우팅·DraftSpec 관련 **196개 통과**를 확인했다.
원본 요청의 seed/target 재결속 2건과 그래프의 v2 연결 1건도 추가했다.
Bundle 정적 검증과 최종 관련 **9파일 826개 회귀**가 통과했다(272.25초).
대상은 `test_editor_plan_input`, `test_editor_routing`, `test_editor_draft_spec`,
`test_editor_graph`, `test_editor_assembler`, `test_dify_bundle`, `test_dify_environment`,
`test_dify_schema_types`, `test_agent_prompt_integrity`다. 전체 앱 suite나 실제 UI 시험은 아니다.
두 독립 검토 모두 **9/10**, 필수 수정 없이 통과했다. API/LLM 호출은 없었다.
별도 집중 검사에서도 old producer/Guard/Enforcer/prompt 혼합 4종을 bundle과 raw materializer
두 진입점에서 모두 거부했다(8/8). 실제 원본 pack의 v1/혼합/seed/target/source/entity 변조
7종도 Guard 거부 → Enforcer blocked → 두 최종 Guard blocked를 확인했다.
기존 writer 입력·compiled plan·needs_input/blocked 결과는 기준과 같았다.
이 집중 검사들을 추가 영구 회귀로 고정하는 것은 선택 후속 항목으로 남겼다.

기준 commit과 AST를 대조하면 Assembler의 기존 함수 중 변경된 것은 `prepare()`뿐이며,
사용하지 않는 `_draft_contract()`만 제거됐다. DraftSpec에서는 입력 exact 검증과 원본
재결속 함수만 달라졌고 writer projection·compiler 함수는 그대로다.
기준 DSL과 YAML 객체를 대조한 결과 변경은 Editor Input/Assembler/Guard/Enforcer의
embedded code 4개와 DraftSpec LLM의 입력 버전 문구 1개뿐이다.
그 5개 필드를 제외하면 전체 객체가 동일하다. 모델·23노드/26엣지 구조·나머지 설정을 유지한다.

### 고정 fixture의 입력 크기 — 모델 실측 아님

`test_editor_plan_input.py::test_frozen_editor_cases_fit_with_max_knowledge_and_100_byte_names`와
같은 `load_case`/`build_pack`을 사용했다. request ID=`req-budget`, 관측일=`2026-09-15T00:00:00Z`,
합성 manifest=`a` 64개, Knowledge 본문은 ASCII 768+256바이트, 문서명은 각각 100바이트다.
동일한 canonical 입력을 기준 commit의 `prepare()`와 후보에 각각 넣어 UTF-8 길이만 비교했다.

| 고정 case | 입력 v1 bytes | 입력 v2 bytes | 감소 |
|---|---:|---:|---:|
| CMP2 | 8,865 | 8,502 | 363 |
| CMP3 | 8,862 | 8,512 | 350 |
| CMP6 | 8,842 | 8,492 | 350 |
| CMP8 | 8,801 | 8,438 | 363 |
| CMP9 | 8,800 | 8,437 | 363 |

다섯 결과 모두 기존 객체의 `draft_contract` 제거와 버전 표기 변경 외에는 완전히 같다.
작성자와 root가 각각 재계산했고 root 검사는 socket 연결을 차단한 상태로 수행했다.
fixture/배터리/checker·원문 pack은 수정하지 않았다. 이는 입력 직렬화 크기 차이이며
실제 LLM token·latency·비용·의미 품질 개선의 측정값이 아니다. 새 모델 호출은 0회다.

### 경계·무결성 확인

내부 selector payload를 앱 HTTP/CLI/config/checkpoint로 직접 노출하는 경로는 발견되지 않았다.
원격 checkpoint는 workflows를 포함한 profile fingerprint를 재검증하고,
gateway는 고정 workflow UUID와 빈 conversation ID로 호출한다.
따라서 이 앱의 기존 대화를 새 profile로 조용히 이전시키는 fallback은 없다.
Dify 서버 자체의 진행 중 실행 재개 동작을 시험한 것은 아니므로 그 동작은 별도 검증 대상이다.

아래는 테스트에 사용한 파일의 **raw UTF-8 SHA-256**이다. strip/rstrip 정규화된 reviewed pin,
Git의 줄바꿈 정규화 blob 또는 semantic graph 해시와 혼동하지 않는다.

| 파일 (`deploy/maxis-dify/` 기준) | raw SHA-256 |
|---|---|
| code/editor_assembler.py | 047129dfb3a4961e9918894209cef3b85904e60c1fe06a62c685a867a6aa7c87 |
| code/editor_draft_spec.py | b944d3b9d15cffcdbd1acedad0dc238466a5595cca432fac2df3f2e406bf47f3 |
| prompts/editor_selector.md | 1cfbe40f187218f25dcea6458b11843b447f219dd12e85c20f15129e5aa35607 |
| dsl/ltm-editor-author-2026.09.yml | ccb1050848b1dda868705a6346cce6f7fcd2491b73116d3d7f5e6f6161b04306 |

소유한 실행별 pytest 임시 디렉터리는 정확한 경로 확인 후 정리했다. 다른 실행 자료와
기존 평가 raw는 보존했다. frontend editor, Jira 직렬화, badge/mention renderer는 수정하지 않아
이번 내부 계약 회귀의 UI 시험 대상이 아니며 실제 UI 통과를 주장하지 않는다.

아직 새 품질 점수나 속도·토큰 개선 수치는 없다.
입력 필드 감소만으로 실제 모델 품질 향상을 주장하지 않는다.

실제 품질 비교는 protocol 4.0.0 / rubric 2.0.0과 기존 Editor component battery 4.0.0,
Role component battery 1.0.0을 유지한 새 전체 실행으로 확인해야 한다. 과거 실패를
focused 성공이나 native 중간 출력으로 대체하지 않는다. 승인된 DEV·합성 데이터 범위만
사용하며 운영 route local, PROD·main·push는 변경하지 않는다.

# MAXIS / Dify 배포 소스

LTM의 원격 Role 실행을 위한 **현행 소스 번들**이다. 같은 목적의 정본은 하나만 유지하고
과거 버전은 Git 이력으로 관리한다. 평가 결과·스크린샷·배포 영수증·독립 PoC·생성한 import
copy는 ignored `.cache/`에 두며 커밋하지 않는다. 변경 이력·검증 요약·TODO는 PR 본문에 기록한다.

기본 Role route는 local이다. DEV는 고정 합성 fixture 검증용이며 운영 활성화나 실제 업무
자료 전송 허가가 아니다. 로컬 계약 검증과 실서버 기능·품질 검증은 별도 게이트다.

**PROD 운영자는 [빠른 설정 가이드](../../docs/agent-maxis-operations.md)부터 시작한다.**
`tools/maxis_setup.py`의 `prepare → bundle → connect → status`를 사용하며,
이 문서는 배포 소스·계약을 개발하거나 검토할 때 참고한다.

공개 Knowledge 정본은 `knowledge/01`~`10`과 `config/maxis-knowledge.yml`이다.
canonical DSL은 실제 DEV UUID나 model을 포함하지 않는 source marker를 사용한다.
PROD 준비는 깨끗한 checkout만으로 가능하며 DEV 환경 파일·개발자 cache corpus를 요구하지 않는다.
`materialize_environment.materialize(environment_mapping, output_dir)`에 해당 환경의 실제
dataset/document/sentinel/metadata UUID와 model plugin/provider/name을 명시해 import copy를 만든다.
선택적인 `model.completion_params`는 모든 LLM 노드의 canonical 튜닝을 교체한다. 명시한 `{}`는
서버 모델 기본값, 미지정만 기존 튜닝 유지다. 공개 scalar 옵션만 검증·허용하며, 모델별 지원 여부는
해당 프로젝트에서 확인한다. canonical 원본의 고정 파라미터 검사는 변경 없이 먼저 수행한다.
`validate_environment(mapping)`은 입력을 변경하지 않고 동일한 계약을 검사한다.
`public_knowledge_source()`는 문서 설명·hash·Role과 version을 제공하며 runtime 설정·비밀을 읽지 않는다.
다른 환경 metadata가 실제로 있으면 UUID 재사용을 비교한다. 없는 DEV를 새로 만들 필요는 없다.
canonical source를 포털에 직접 import하거나 source marker를 실제 자산 ID로 사용하지 않는다.

## 현행 구성

| 경로 | 목적 |
|---|---|
| `bundle.yml` | 세 Workflow의 Start·검색·Guard·실행 계약 |
| `dsl/*.yml` | Core, PMO, Editor별 하나의 Dify import source |
| `code/*.py` | Knowledge/Contract Guard, Core 분류·Query/Request/Work/Knowledge/Result 입력 투영·조립, Editor 입력 투영·선택 검증·조립 |
| `contract_guard_asset.py` | Contract Guard template의 정상 import를 canonical `app/agent/maxis/source_grounding.py` 소스로 치환하는 공통 stdlib-only 배포 렌더러 |
| `prompts/*.md` | 공통 및 단위 기능별 instruction 정본 |
| `contracts/*.json` | 서로 다른 단계의 응답·선택·문안·조립·용어·평가 형식 |
| `editor_graph.py`, `editor_graph_validation.py` | Editor 그래프 생성과 원본 검증 |
| `policy_query_graph.py` | Core/PMO의 업무 입력과 정책 검색어 분리 |
| `core_control_graph.py`, `core_graph_validation.py` | Core 제어/업무 분기 생성과 원본 검증 |
| `core_query_contract.py` | canonical Query 값 제약에서 내부 단위 계약 생성 |
| `core_request_contract.py`, `code/core_request_input.py`, `code/core_request_assembler.py` | 실제 RequestArchitect 계약·의미 지시 전달, 입력 metadata 분리와 draft 의미 보존 조립 |
| `core_work_contract.py`, `code/core_work_input.py`, `code/core_work_assembler.py` | exact CREATE Work 정책·native 계약과 원입력 기반 의미 보존 조립 |
| `core_knowledge_contract.py`, `code/core_knowledge_input.py`, `code/core_knowledge_assembler.py` | exact KnowledgeBrief 계약·입력 투영과 원입력 결속 조립 |
| `core_result_contract.py`, `code/core_result_input.py`, `code/core_result_assembler.py` | exact Result text 계약·입력 투영과 단일 의미 값의 payload/claim 조립 |
| `code/core_draft_validation.py` | 역할 의미를 다루지 않는 엄격한 JSON·schema 검사; builder가 조립 코드 앞에 결합 |
| `code/core_draft_context.py` | 원본 입력의 Role·schema·출처 hash 결속 검사; 의미·승인 판단 없음 |
| `graph_receipt.py` | 로컬 graph와 서버 export의 설정 비교 코드(실행 결과가 아님) |
| `environments/dev.yml` | 현재 DEV 환경의 비밀 없는 binding |
| `environments/prod.template.yml` | 독립 PROD 환경을 준비하는 template |
| `materialize_environment.py`, `validate_bundle.py` | 환경별 import copy 생성과 정적 검증 |
| `launch_*_evals.py`, `run_*_evals.py` | 승인된 수동 평가 launcher/runner |
| `knowledge-evals.yml`, `smoke-cases.yml` | 재사용하는 평가·smoke 명세 |

파일명에는 날짜나 버전 접미사를 붙이지 않는다. 각 목적의 안정된 경로 하나를 갱신하고,
이전 내용은 Git 이력으로 관리한다. 계약의 호환성 식별자는 파일명과 별개로 유지한다.
Editor의 `editor-draft-spec.v2`, `editor-writing.v1`, `editor-plan.v2`는 각각 선택·문안·조립
계약으로 서로 대체하지 않는다. wire contract version과 파일의 과거 복사본을 구분한다.

경로·prompt의 파일명 참조가 바뀌면 source/prompt digest와 graph receipt도 달라질 수 있다.
기존 평가 원본·서명·profile pin을 새 hash로 덮어쓰지 않는다. 현재 소스의 재게시·검증 전에는
이전 배포 증적을 현재 후보의 활성화 근거로 재사용하지 않는다.

## Workflow와 단위 기능

공통 흐름은 Start → release sentinel / Role ACL 검색 → Knowledge Guard → policy 분기 →
검증된 입력의 LLM 처리 → Contract Guard → Answer다. policy deny는 LLM 없이 종료한다.

- **Core:** 32노드·38엣지. 검증된 연결 검사, canonical Query·Request·CREATE Work·KnowledgeBrief·Result text 계약, 일반 업무를 분류해
  control/query/request/work/knowledge/result/semantic 중 한 LLM 경로만 사용한다. Query는 기존 `query_specialist`의 typed
  입력에서 업무 내용·근거를 보존하고 최종 계약 metadata를 분리한다. LLM은 의미 값·kind·근거를
  작성하고 Code 조립기가 원본 입력으로 ID·인용 연결·metadata만 구성한다. 새 Role이나
  추가 LLM 호출은 없다. Request는 실제 RequestArchitect 필드 설명과 선택된 로컬 역할 규칙을
  전달한다. Request Input은 원문·출처 ID·업무 식별자를 보존하면서 opaque intent 식별자와
  최종 계약 metadata를 분리하고 기존 claim 제한을 표시한다. 조립기는 원본 입력을 사용해
  사용자 요청 의미와 명시 label을 기존 정본 payload로 조립한다. 분류·조립은 인증이나
  근거 검증을 대신하지 않는다. [분류 계약](core-control-route.md).
  최종 Candidate Binding은 모델이 잘못 복사할 수 있는 게시 UUID·Knowledge hash 등
  transport metadata만 검증된 요청에서 결속한다. status·payload·claim·citation·질문·경고는
  그대로 두며, 기존 Contract Guard의 실행 UUID·근거·schema 검사를 거친다.
  Work는 exact CREATE에만 전용 입력·native draft·조립 경로를 사용하며 다른 Work schema는
  기존 일반 경로를 유지한다. 필수 CREATE 정책을 보존하기 위해 Work system만 16,384 UTF-8
  bytes 한도이며 다른 역할·원본 context·출력 token 한도는 그대로다. 실측 품질·효율 보장은 아니다.
  Knowledge는 exact KnowledgeBrief에 전용 입력 투영 → native draft → 원입력 결속 조립 경로를
  사용한다. 로컬 Knowledge 역할의 의미 규칙과 양쪽 최종 guard를 유지한다.
  Result는 exact backend text schema에 입력 투영 → 단일 의미 LLM → 조립 경로를 사용한다.
  허용된 업무 근거·context·정책을 보존하고 원격에 없는 로컬 입력은 가정하지 않는다. 모델이
  한 번 작성한 value/kind/source_ids에서 전체 text와 claim을 함께 만들며 양쪽 최종 guard와
  일반 Result apply·승인·terminal 경로를 유지한다. 조립 일치는 사실 근거나 품질 통과가 아니다.
- **PMO:** 12노드·12엣지. 결정적으로 계산된 LTM 수치와 검증된 근거를 해석한다.
  수치 집계의 정본은 LTM이며 모델이 Jira 상태를 추정하지 않는다.
- **Editor:** 23노드·26엣지. 입력 투영 → 본문/댓글 선택 → DraftSpec 검증 → 문안 작성 →
  plan 컴파일·결정적 HTML 조립 → Contract Guard로 책임을 나눈다.
  제어/중단 경로와 실제 작성 경로는 별개이며 선택 단계에 전체 HTML 생성을 맡기지 않는다.

위 수는 로컬 source topology이며 현재 서버 실행·품질·활성화 상태의 증명이 아니다.
TaskFlow/Native Agent의 목표 연결과 아직 필요한 trusted adapter는 [TaskFlow 계약](taskflow.md)에
분리해 둔다. 독립 합성 PoC는 이 번들의 제품 소스에 포함하지 않는다.

## 입력·근거·승인 경계

Start 변수는 `context_pack`, `role_id`, `correlation_id`, `output_contract`,
`workflow_version`, `knowledge_version`의 6개다. `query`/`user`는 Chat API 필드다.
pack은 최대 32,768 UTF-8 bytes이며 나머지 Start scalar의 최소 수용량은 128이다.
여러 티켓을 포함하는 조회 결과도 이 한도 안에서 원문을 보존한다. 초과·잘림·오래된
조회는 여전히 초안 생성을 차단한다. 기존 게시본도 Start 및 Code 노드를 함께 갱신해야 한다.
대화 ID는 비우고 이름 자동 생성을 끈 stateless 요청을 사용한다.

- **Policy RAG:** 조직 규칙·형식·검증 절차. 환경별 metadata Role filter와 exact allow-list를
  적용한다. Jira·Confluence·Bitbucket의 현재 사실이나 citation 근거로 승격하지 않는다.
- **EvidencePack:** LTM의 권한 있는 조회와 결정적 계산에서 얻은 업무 사실. claim/citation은
  이 source graph에 결속한다. `ltm:user-intent:`는 사용자 요청·선호만 뒷받침하며 외부 사실은 아니다.
- source가 없으면 사실 claim을 거부한다. 예외는 모든 필드가 required이고 const/단일 enum인
  폐쇄형 non-narrative control payload뿐이다. free-text·optional·default 등은 예외가 아니다.
- Knowledge Guard가 identity·ACL·sentinel·binding·크기를 검사한 뒤 LLM 입력을 만든다.
  Contract Guard와 LTM ResponseGuard가 Workflow UUID·version·schema digest·근거·금지 authority
  field를 재검증한다. query/검색 본문은 비신뢰 자료다.
- critical node 실패는 terminate한다. 자동 retry·기본값 복구·failure answer·model fallback으로
  성공을 가장하지 않는다. 실패 원인과 미관측 값을 구분한다.
- 원격 시스템에 Jira session·credential·approval token을 주지 않는다. 변경은 LTM의
  기존 Action Executor가 사용자의 exact payload 승인 후 수행한다.

## DEV / PROD 전환

환경 URL·모델·dataset/document/metadata ID는 environment binding, 실제 실행 profile과
published Workflow pin은 LTM 설정에서 관리한다. API key는 승인된 로컬 secret 경로에만 둔다.
source DSL을 환경별로 복제·수정하거나 코드에 DEV credential을 넣지 않는다.

저장소 루트에서 오프라인 정본 검증:

```powershell
python -B deploy/maxis-dify/validate_bundle.py
```

PROD 자산 준비·연결은 위 빠른 설정 가이드의 네 명령을 따른다. 직접 materializer를 사용할 때도
현재 정본과 실제 환경의 식별자가 일치하는 입력을 사용한다. 과거 DEV binding을 PROD 입력으로 쓰지 않는다.

materializer는 원본 DSL을 먼저 검증한 뒤 환경값을 주입한다. 불완전한 UUID, 비 HTTPS 원격
주소, credential 형태의 필드와 source drift는 거부한다. 생성물을 커밋하지 않는다.
PROD는 독립 자산으로 materialize → validate → 별도 승인된 publish → preflight → canary →
서명된 평가/운영 gate를 거친다. 설정 변경만으로 검증을 통과하거나 활성화한 것으로 보지 않는다.

## 평가 실행과 결과 보관

평가 코드·spec·합성 fixture·회귀 test는 재사용 가능한 소스다. **평가 실행 결과는 최신본도
커밋하지 않는다.** raw와 직접 채점 보고서는 `.cache/agent-evaluation/<runGroupId>/`에 보관한다.
배포 비교 결과·실험 export도 `.cache/`에 두고 PR에는 비밀 없는 요약만 기록한다.

실 API 평가에는 별도 명시적 승인이 필요하다. launcher는 credential-free destination 확인 후
비활성 synthetic-only DEV profile·all-local route·published pin을 검증하고 승인된 키를 읽는다.

```powershell
python -B deploy/maxis-dify/launch_knowledge_evals.py --pilot-only
python -B deploy/maxis-dify/launch_editor_evals.py --label candidate
python -B deploy/maxis-dify/launch_role_evals.py --label candidate
```

`baseline`/`candidate`는 결과 label이지 배포 전환이 아니다. 둘 다 현재 pinned workflow를
측정한다. focused attempt는 새 run group이며 기존 full-run의 실패를 덮어쓰지 않는다.
정답/checker/rubric과 raw fixture UID는 원격에 보내지 않는다.

Role battery 3.0.0은 envelope/binding/retrieval 통과와 `outcomeContractPass`를 분리한다.
Auditor의 새 요청 근거와 오래된 Jira 근거가 함께 있으면 `mixed`이며 여전히 안전 차단 대상이다.
이 기대 계약 변경 전의 결과와 새 버전의 점수를 직접 비교하지 않는다.
양성 payload·필요 질문·예상 안전 차단·예상 밖 빈 응답·미시도는 별도로 집계하며 안전 차단을
업무 완료율에 합산하지 않는다. 의미 품질은 Codex/Claude가 raw를 직접 읽어 평가하고 LTM LLM을
judge로 쓰지 않는다. 비용/native 호출 등 미관측값은 unknown이다. component 진단 성공은
UI·전체 qualification·activation 승인과 다르다. [평가 규약](../../app/agent/EVALUATION.md)을 따른다.

## Source of truth

- [LTM 아키텍처와 현재 운영 제약](../../docs/MAXIS_DIFY_MIGRATION.md)
- [Workflow registry](../../config/maxis-workflows.yml), [Knowledge manifest](../../config/maxis-knowledge.yml)
- [Knowledge 릴리스 계약](KNOWLEDGE-RELEASE.md), [내부 시범 계약](internal-pilot.md)
- [TaskFlow preview/transport 계약](taskflow.md), [Core 분류 계약](core-control-route.md)

MCP는 선택적 중앙 read-only 연계다. 초기 흐름은 LTM이 outbound로 Dify를 호출하고 필요한
EvidencePack을 전달하므로 로컬 PC의 공개 URL이 필요하지 않다. MAXIS Node/Agent 등록·중앙 MCP
연결·실제 사용자 ACL/보존 검증이 없는 기능을 활성화된 것으로 표시하지 않는다.

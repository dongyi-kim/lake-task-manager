# MAXIS / Dify 배포 소스

LTM의 원격 Role 실행을 위한 **현행 소스 번들**이다. 같은 목적의 정본은 하나만 유지하고
과거 버전은 Git 이력으로 관리한다. 평가 결과·스크린샷·배포 영수증·독립 PoC·생성한 import
copy는 ignored `.cache/`에 두며 커밋하지 않는다. 변경 이력·검증 요약·TODO는 PR 본문에 기록한다.

기본 Role route는 local이다. DEV는 고정 합성 fixture 검증용이며 운영 활성화나 실제 업무
자료 전송 허가가 아니다. 로컬 계약 검증과 실서버 기능·품질 검증은 별도 게이트다.

## 현행 구성

| 경로 | 목적 |
|---|---|
| `bundle.yml` | 세 Workflow의 Start·검색·Guard·실행 계약 |
| `dsl/*.yml` | Core, PMO, Editor별 하나의 Dify import source |
| `code/*.py` | Knowledge/Contract Guard, Core 분류·Query 입력 투영·조립, Editor 입력 투영·선택 검증·조립 |
| `prompts/*.md` | 공통 및 단위 기능별 instruction 정본 |
| `contracts/*.json` | 서로 다른 단계의 응답·선택·문안·조립·용어·평가 형식 |
| `editor_graph.py`, `editor_graph_validation.py` | Editor 그래프 생성과 원본 검증 |
| `policy_query_graph.py` | Core/PMO의 업무 입력과 정책 검색어 분리 |
| `core_control_graph.py`, `core_graph_validation.py` | Core 제어/업무 분기 생성과 원본 검증 |
| `core_query_contract.py` | canonical Query 값 제약에서 내부 단위 계약 생성 |
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

- **Core:** 19노드·21엣지. 검증된 연결 검사, canonical Query 계약, 일반 업무를 분류해
  control/query/semantic 중 한 LLM 경로만 사용한다. Query는 기존 `query_specialist`의 typed
  입력에서 업무 내용·근거를 보존하고 최종 계약 metadata를 분리한다. LLM은 의미 값·kind·근거를
  작성하고 Code 조립기가 원본 입력으로 ID·인용 연결·metadata만 구성한다. 새 Role이나
  추가 LLM 호출은 없다. 분류·조립은 인증이나 근거 검증을 대신하지 않는다. [분류 계약](core-control-route.md).
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
pack은 최대 6,144 UTF-8 bytes이며 나머지 Start scalar의 최소 수용량은 128이다.
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

저장소 루트에서 오프라인 검증·변환:

```powershell
python -B deploy/maxis-dify/validate_bundle.py
python -B deploy/maxis-dify/materialize_environment.py deploy/maxis-dify/environments/dev.yml --output-dir .cache/maxis-dify-materialized/dev
```

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

Role battery 2.0.0은 envelope/binding/retrieval 통과와 `outcomeContractPass`를 분리한다.
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

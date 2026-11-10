# r32 OSS 기능 활용 감사

일자: 2026-08-18

이 문서는 새 프레임워크를 많이 추가하는 것이 아니라, 이미 채택한 OSS의 공개 기능으로
직접 구현을 얼마나 제거하거나 안전한 정본으로 치환할 수 있는지를 점검한다. 판단 기준은
다음과 같다.

1. 현재 제품의 의미 계약과 호출 예산을 유지한다.
2. private API, 손실 변환, 중복 실행기를 제거한다.
3. 새 의존성은 삭제되는 코드나 측정 가능한 품질·보안 이득이 있을 때만 추가한다.
4. 제품명/특정 평가 사례에 맞춘 분기는 허용하지 않는다.
5. 공개 배포 라이선스 검토는 런타임 기능 채택과 별도의 release gate로 유지한다.

## 이번에 바로 적용한 기능

| OSS 기능 | 기존 구현/위험 | 적용 | 결과 |
|---|---|---|---|
| LangGraph `ToolNode` | 복수 도구 호출을 별도 `ThreadPoolExecutor`로 다시 병렬화 | `ToolNode.invoke` 한 경계가 병렬 실행, 순서, 도구 오류 변환을 담당 | 수동 executor 분기 제거, 단일/복수 호출의 오류 의미 통일 |
| `langgraph-prebuilt` 재현성 | `ToolNode`를 직접 import하면서 LangGraph의 범위 전이 의존에만 기대고 있었음 | 함께 검증한 `langgraph-prebuilt==1.1.0`을 direct exact pin 및 dependency contract에 추가 | 재설치 때 병렬/오류 실행기가 조용히 바뀌는 위험 차단 |
| MCP 2 structured output | `content` 문자열만 읽어 필드가 손실될 수 있음 | 서버가 typed `dict`를 반환하고 클라이언트는 성공 결과의 `structured_content`를 우선 사용 | JSON 문자열 추측 없이 완전한 결과 보존; `is_error` 결과는 fail-closed |
| MCP `ToolAnnotations` | read-only allow-list가 서버 구현에만 존재 | `readOnlyHint=true`, `destructiveHint=false`, `openWorldHint=false`를 모든 공개 도구에 추가 | 호환 클라이언트가 승인/UI 정책을 더 정확히 결정; 기존 allow-list는 보안 정본으로 유지 |
| LangChain `StructuredTool` JSON Schema | MCP 중첩/enum/oneOf 스키마를 로컬 소형 Pydantic 모델로 평탄화 | 원격 `inputSchema`를 그대로 `args_schema`로 전달하고 `jsonschema` compiled validator로 실행 직전 검증 | 손실 변환과 가짜 필수 `query` 제거, 원 서버 계약 보존 |
| FAISS wrapper public mapping | `docstore._dict` private 내부 필드 사용 | `index_to_docstore_id` 공개 mapping으로 삭제 대상과 통계를 계산 | 라이브러리 내부 구조 변경에 대한 취약성 제거 |
| Pydantic `TypeAdapter` 단일 계약 | PeopleAdvisor가 수기 JSON Schema와 별도 출력 해석 계약을 함께 유지 | `PeopleAdvice` model과 공개 `TypeAdapter` 하나가 prompt schema·strict wire validation·typed projection을 모두 생성 | production `+38/-58`, 순 20줄 삭제; 전용/transport 123개와 broad 576개 회귀 통과 |
| deterministic Role dead schema 정리 | ActionExecutor가 LLM을 전혀 호출하지 않는데 수기 출력 schema를 유지 | deterministic `node()` 계약을 고정하고 추상 인터페이스용 `schema()`만 `{}`로 유지 | production `+1/-29`, 순 28줄 삭제; graph·승인 경로 133개 회귀 통과 |

MCP annotation은 힌트이며 보안 권한이 아니다. 외부 서버별 명시적 read-tool allow-list와
내부 식별자 반출 금지는 그대로 제품 코드가 소유한다. MCP 오류는 프로토콜 예외가 아니라
`is_error=true` 결과일 수 있으므로, 오류 결과의 `structured_content`는 근거로 승격하지 않는다.

## 기존에 채택했고 더 확장하지 않은 기능

| OSS | 현재 활용 | 추가 기능 검토 | 결정 |
|---|---|---|---|
| Instructor 1.15.4 | Pydantic 응답 모델, 검증 피드백, 최대 1회 형식 repair | hooks, partial streaming, 별도 usage 계측 | **현 상태 유지.** LangChain/Langfuse 계측과 중복되고 strict whole-document JSON 계약을 약화할 수 있음 |
| nh3 0.3.6 | HTML5 파싱, tag/attribute/URL scheme allow-list, canonicalization | 동적 `attribute_filter`, style allow-list | **현 상태 유지.** 제품 reference identity 검증은 sanitizer가 대신할 수 없고 style은 계속 전면 금지 |
| Langfuse 4.14.4 | 기존 LangChain callback과 session metadata | experiments/datasets, Instructor hook 중복 계측 | **보류.** 외부 관측 저장소를 평가 정본으로 만들지 않고, self-host/retention 정책을 먼저 확정 |
| FAISS 1.15 | LangChain community wrapper를 통한 vector store | raw FAISS/`IndexIDMap2` 직접 래퍼 | **보류.** sunset 의존성은 부채지만 현재 metadata/docstore/search 코드를 다시 만들면 순 LOC가 증가 |

## Pydantic 제한 PoC 결과와 확장 기준

가장 큰 공통화 후보는 Pydantic을 **역할 출력 스키마의 단일 원천**으로 만드는 것이다.
PeopleAdvisor 한 역할에서 먼저 다음 조건을 검증했고 채택 기준을 충족했다.

- 신뢰된 state가 action에 맞는 작은 `BaseModel`을 registry에서 선택한다.
- 같은 model이 JSON Schema 생성, runtime validation, serialization을 모두 담당한다.
- 기존 schema의 required/default/enum/길이 제한/`additionalProperties`와 출력이 동일하다.
- provider에 전달되는 schema와 token 크기가 늘지 않고 repair 횟수가 증가하지 않는다.
- 첫 역할에서 production 20줄을 실제로 삭제했다(`+38/-58`).

기존 required/optional, max/min, `additionalProperties`, schema title 의미는 유지했고 같은 adapter가
wire projection까지 검증한다. 표준 Draft 2020-12 `$defs/$ref` 출력은 로컬 validator와 transport
회귀를 통과했지만 실제 LAN provider 호출은 승인 전이므로 실행하지 않았다. 다음 역할도 **역할별
순삭제 20줄 이상**, schema token 비증가, repair 횟수 비증가를 각각 증명한 경우에만 확장한다.

남은 수기 역할 스키마는 Work 제외 288 LOC, Work의 조합형 스키마까지 합치면 553 LOC다.
전수 정적 비교 결과는 다음과 같다.

| 다음 후보 | 예상 순삭제 | 현재 schema token 변화 추정 | 결정 |
|---|---:|---:|---|
| ResearchAnalyst | 48~55줄 | +254, 약 +38% | 두 번째 제한 PoC 후보. provenance/quality validator와 확장 필드를 유지하고 LAN A/B에서 repair 비증가를 먼저 증명 |
| RequestArchitect | 40~48줄 | +158, 약 +14% | 보류. 모든 요청의 첫 라우터이고 현재 typed missing-slot 계약이 안정화 중이라 영향 반경이 큼 |
| WorkArchitect | 잠재 삭제 큼 | 약 +445 | 보류. 여러 action schema를 거대 합집합으로 만들어 wire token과 repair 위험을 늘리지 않음 |

ActionExecutor의 수기 schema는 실행 경로에서 사용되지 않는 것이 확인되어 별도 모델 전환 없이
즉시 제거했다. 위 token 수치는 현재 JSON 표현을 `o200k_base`로 센 정적 비교이며 실제 local
provider tokenizer·과금 수치는 아니다.

Work action 전체를 판별 합집합(discriminated union)으로 묶어 모델 wire에 보내지는 않는다.
현재는 신뢰된 state가 작은 create/update/comment schema를 선선택한다. 모든 variant와 새
discriminator를 한 번에 보내면 schema token과 prompt-only Qwen repair 위험이 증가하면서
실제 LOC는 거의 줄지 않는다. 판별 합집합은 이미 명시적 tag가 있고 wire가 커지지 않는 내부
계약에서만 제한적으로 사용한다.

## 검토했지만 도입하지 않은 기능

| 후보 | 유용성 | 보류/기각 이유 |
|---|---|---|
| LangGraph SQLite/Postgres saver | 프로세스 재시작 후 resume, fault tolerance | 내부 티켓/사람/근거를 디스크에 영속화하므로 암호화·보존·삭제 정책 선행 필요; 현재 LOC 삭제 없음 |
| LangGraph 동적 `interrupt()` | 승인/필수입력 pause-resume | 현재 UI pending card, approval fingerprint, effect authority 계약을 대체하는 migration이 필요 |
| `langchain-mcp-adapters` | MCP tool conversion 공통화 | 최신 검토 버전이 `mcp<2`를 요구해 현재 MCP 2와 충돌 |
| Tenacity 직접 적용 | 일반 retry/backoff | 제품 retry는 semantic repair와 wire-call ceiling을 포함하므로 단순 예외 재시도로 치환 불가 |
| DeepEval production 도입 | RAG/대화 평가 지표 | 별도 telemetry/OTel/PostHog와 큰 의존 그래프를 추가하지만 현 evaluator 코드를 삭제하지 못함 |
| OpenAI Responses API 전환 | 최신 OpenAI-native 기능 | 현재 승인된 local OpenAI-compatible `/v1/chat/completions` 경계를 우회하거나 서버 호환을 깨뜨릴 수 있음 |

## 공식 자료

- LangGraph ToolNode: <https://docs.langchain.com/oss/python/langchain/tools>
- LangGraph persistence: <https://docs.langchain.com/oss/python/langgraph/persistence>
- MCP Python SDK structured tools: <https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/servers/tools.md>
- MCP client structured result/error semantics: <https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/client/index.md>
- LangChain `StructuredTool` schema contract: <https://reference.langchain.com/python/langchain-core/tools/structured/StructuredTool/from_function>
- Pydantic discriminated unions: <https://docs.pydantic.dev/latest/concepts/unions/>
- Instructor hooks: <https://python.useinstructor.com/concepts/hooks/>
- nh3 reusable cleaner: <https://nh3.readthedocs.io/en/latest/>
- FAISS index operations: <https://github.com/facebookresearch/faiss/wiki/Special-operations-on-indexes>

## 공개 배포 경계

이번 적용 패키지는 permissive 계열을 우선했지만, 이것만으로 저장소의 공개 배포 준비가
끝난 것은 아니다. 프로젝트 outbound `LICENSE`, third-party license/NOTICE 묶음, vendored
front-end asset의 package/version/source/hash/license-text inventory가 아직 별도 release
blocker다. 내부 비상업 사용이나 소스 공개 예정이라는 사실은 해당 의무를 면제하지 않는다.

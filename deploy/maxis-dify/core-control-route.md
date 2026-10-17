# Core 단일 실행 분기 계약

Core는 연결 검사, typed Query와 일반 업무 판단을 각각 한 LLM에 맡기며 모든 경로가 기존 Knowledge Guard와
Contract Guard 사이에서 실행한다. 이 문서는 현행 소스 계약이다. 배포·평가 실행 결과는
ignored `.cache/`에 보관하며 커밋하지 않는다.

## 분류기

`code/core_control_route.py::main(model_input)`은 표준 라이브러리만 사용하는 순수 함수다.
Knowledge Guard의 policy allow 이후 canonical `ltm-model-input.v1` JSON을 입력받는다.
입력은 최대 9,000 UTF-8 bytes, context pack은 최대 6,144 bytes다. 정확한 envelope field와
object 형식을 요구하며 중복 key·비유한 수·비정규 JSON을 거부한다.
예약 식별자를 검사하는 `sources`/`role_context`는 object list여야 한다.

- 기본은 `semantic`이며 일반 업무의 전체 schema/evidence 유효성을 판정하지 않는다.
- `query_specialist`는 `workflow/contracts.py`의 canonical `CompactQueryPlan` JSON과 SHA가
  모두 정확히 일치할 때만 `query`다. 불일치는 오류이며 generic semantic으로 우회하지 않는다.
  빌더가 정본에서 schema/digest를 생성해 분류기에 결속하며 별도 계약 복사본을 유지하지 않는다.
- Core factory의 activation만 `control`로 분류한다. `knowledge_curator`, canonical ready
  schema/digest, nonce와 source/fact/context/가명 사용자, UTC timestamp 연결, 빈 scope/metrics/flags,
  fresh/nonpartial 상태를 검사한다.
- `probe_` request ID, `ltm:maxis-probe:` source ID, `activation_probe` context 중 하나라도
  있으면 전체 activation 계약을 요구한다. 변조·다른 앱/Role은 `invalid_core_control_input` 오류다.
- 요청 query 본문은 bounded text인지만 확인하고 내용으로 분기하지 않는다. Knowledge 본문을 읽거나
  ready schema만으로 control을 선택하지 않는다. 입력 repair·정규화는 하지 않는다.

인증·ACL·sentinel/proof·실제 Workflow binding·wall-clock freshness는 이 함수의 보장이 아니다.
UTC 달력 유효성과 timestamp 간 동일성만 확인한다. 세 route 모두 실행/출력 허가가 아니며
ContextGuard → Knowledge Guard → 실제 LLM → Contract Guard/ResponseGuard가 필요하다.
분류기는 ready 응답·citation·canary 성공 결과나 모델 실행 증명을 만들지 않는다.

## 생성과 원본 검증

`core_control_graph.py::rebuild`는 Core source와 검토된 분류기/prompt를 받아 메모리 복사본을
반환한다. CLI·파일 쓰기·runtime 등록은 없으며 reviewed asset의 독립 SHA pin을 확인한다.
19노드·21엣지에서 policy allow 뒤 배타 분기를 둔다. Control/Contract LLM은 text를,
Query LLM은 Query Assembler가 조립한 후보를 합류시켜 기존 Contract Guard로 전달한다.
Control/Contract LLM은 원본 model input, Query LLM은 그 업무 내용·근거를 보존한 전용 입력을 받는다.
policy deny는 0회, 허용된 세 경로는 각각 1회 LLM을 지나도록 구성한다.

`code/core_query_input.py`는 Query 분기에서만 `query-input.v1`을 만든다. `context`는
원본 pack에서 contract·payload_schema_json·payload_schema_sha256·knowledge_manifest_sha256만
제외한 나머지 값이고, `knowledge_context`는 그대로 유지한다. 요청·scope·출처 ID/본문·
facts·metrics·role_context·시각·freshness·partial·flags를 요약·재분류하지 않는다.
최종 payload schema와 초안 schema를 모델에 동시에 요구하지 않기 위한 입력 투영이며,
원본 pack과 proof·metadata는 조립기 및 양쪽 guard에 계속 전달한다.
canonical JSON·정확한 field·9000/6144 UTF-8 bytes·깊이32/노드4096 한도를 검사하지만
권한·source hash·schema 진실성의 별도 판단 주체가 되지는 않는다.

Query LLM은 내부 `query-draft.v1`의 contract/status/payload/missing_inputs만 작성한다.
`core_query_contract.py`가 canonical CompactQueryPlan에서 값의 enum·길이·개수와 optional
제약을 가져오고 narrative leaf만 `{value, kind, source_ids}`로 감싼다. 별도의 payload 정본을
유지하지 않는다. Query 전용 prompt는 이 내부 계약만 요구하며 full remote envelope를 요구하는
common prompt와 결합하지 않는다. 다른 Role의 실제 prompt는 그대로 유지한다.

`code/core_query_assembler.py`는 Knowledge Guard 원입력의 canonical schema/hash/role 연결을
재확인하고, 모델의 value와 kind를 바꾸지 않은 채 payload/claim에 복사한다. citation ID와
JSON Pointer, trusted 입력의 metadata만 결정적으로 만든다. boolean에는 텍스트 binding을
만들지 않으며 생략된 optional field는 생략 상태로 보존한다. 잘못된 형식·source 참조·상태
조합은 오류로 종료하고 부분 결과나 자동 수정 결과를 내보내지 않는다.
`needs_input`/`blocked`는 빈 payload와 provenance를 유지한다. 조립은 source의 진실성이나
조회 권한을 보장하지 않는다. fact를 recommendation으로 바꾸지 않으며 intent를 fact의 근거로
쓰는 경우도 기존 양쪽 guard가 계속 거부한다. native 형식 준수는 의미 품질 통과가 아니다.
Query의 native `exhaustive`만 `enum: [false, true]`로 표현한다. 이는
[Dify 1.11.1의 boolean→string schema 변환](https://github.com/langgenius/dify/blob/1.11.1/api/core/llm_generator/output_parser/structured_output.py#L307-L378)을
거쳐도 JSON boolean domain을 유지하기 위한 호환 표현이다. LTM 정본 schema/hash와 입력·출력
guard는 그대로이며 문자열·숫자를 boolean으로 변환하지 않는다. 정본에 추가 제약이 생기면
표현을 임의로 넓히지 않고 재검토를 요구한다. 실제 서버의 동일 구현·provider 수용 여부와
의미 품질은 별도 실측 대상이며 `structured_output` 포트 존재만으로 타입 검증을 신뢰하지 않는다.
모델 output은 4,096 tokens, 각 system prompt는 최대 8,192 UTF-8 bytes로 유지한다.

빌더는 배포 검증기가 아니다. 원본을 생성기로 먼저 수리하지 않는다.
`core_graph_validation.py`는 세 LLM의 prompt/schema/model/input, 분류기·입력 투영·조립기 source/I/O,
분기·합류·전체 배선을 검증한다. source와 DSL을 함께 바꿔도 독립 pin과 다르면 거부한다.
`validate_bundle.py`와 `materialize_environment.py`가 같은 검증기를 사용한다.
materializer는 원본 검증 뒤에만 앱 이름·모델·Knowledge binding을 교체하며 DEV/PROD의
세 Core LLM에 같은 환경 모델을 적용한다.

공유 schema 비교는 JSON 타입을 보존한다. boolean과 number는 다르고 숫자 1/1.0은 동등하다.
객체 키 순서는 무시하고 배열 순서는 유지한다. 비문자 키·비유한 수·비 JSON 객체·순환·
과도한 중첩은 검증 오류로 거부하고 입력을 수정하지 않는다.

## 검증 범위

로컬 회귀는 실제 probe factory → Guard → 분류기의 결속, 변조 거부, edge/도달성,
입력 보존·멱등성·복사본 독립성과 환경별 원본 검증을 확인한다. 정적 경로의 성질을
실서버 호출 횟수·지연·품질로 해석하지 않는다. 원격 게시·profile 검증·운영 활성화와
승인된 실 API 평가는 [번들 운영 절차](README.md)의 별도 게이트다.

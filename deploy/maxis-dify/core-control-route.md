# Core 단일 실행 분기 계약

Core는 연결 검사, typed Query·Request·CREATE Work·Knowledge·Result와 일반 업무 판단을 각각 한 LLM에 맡기며 모든 경로가 기존 Knowledge Guard와
Contract Guard 사이에서 실행한다. 이 문서는 현행 소스 계약이다. 배포·평가 실행 결과는
ignored `.cache/`에 보관하며 커밋하지 않는다.

## 분류기

`code/core_control_route.py::main(model_input)`은 표준 라이브러리만 사용하는 순수 함수다.
Knowledge Guard의 policy allow 이후 canonical `ltm-model-input.v1` JSON을 입력받는다.
입력은 최대 40,000 UTF-8 bytes, context pack은 최대 32,768 bytes다. 정확한 envelope field와
object 형식을 요구하며 중복 key·비유한 수·비정규 JSON을 거부한다.
예약 식별자를 검사하는 `sources`/`role_context`는 object list여야 한다.

- 기본은 `semantic`이며 일반 업무의 전체 schema/evidence 유효성을 판정하지 않는다.
- `query_specialist`는 `workflow/contracts.py`의 canonical `CompactQueryPlan` JSON과 SHA가
  모두 정확히 일치할 때만 `query`다. 불일치는 오류이며 generic semantic으로 우회하지 않는다.
  빌더가 정본에서 schema/digest를 생성해 분류기에 결속하며 별도 계약 복사본을 유지하지 않는다.
- `request_architect`는 실제 `RequestArchitect.SCHEMA`의 canonical JSON과 SHA에 결속할 때만
  `request`다. 영속 상태인 RequestPlan과 다르다. 입력 Role/use_case 중 하나라도 Request면
  둘 다 정확히 일치해야 하며, 불일치는 generic semantic 우회가 아니라 오류다.
- `work_architect` Role/use_case가 둘 다 일치하고 canonical CREATE JSON/SHA가 정확히 결속될
  때만 `work`다. Role/use_case 불일치, schema/hash 반쪽 일치·변조·비정규 JSON은 오류다.
  유효한 다른 Work schema(UPDATE·COMMENT·확장 CREATE)는 기존 semantic 경로를 유지하며,
  Work draft 실패 뒤 재시도하거나 generic LLM으로 우회하지 않는다.
- Core factory의 activation만 `control`로 분류한다. `knowledge_curator`, canonical ready
  schema/digest, nonce와 source/fact/context/가명 사용자, UTC timestamp 연결, 빈 scope/metrics/flags,
  fresh/nonpartial 상태를 검사한다.
- 예약 activation 식별자가 없는 `knowledge_curator`는 Role/use_case와 canonical KnowledgeBrief
  JSON/SHA가 모두 정확할 때만 `knowledge`다. 불일치·ready schema만 있는 입력은 오류이며
  semantic으로 우회하지 않는다. 예약 식별자 검사는 이 업무 분기보다 먼저 실행한다.
- `result_integrator`는 Role/use_case와 backend의 canonical text schema JSON/SHA가 모두
  정확할 때만 `result`다. 한쪽 Role 또는 schema 결속이 다르면 semantic 우회 없이 오류다.
- `probe_` request ID, `ltm:maxis-probe:` source ID, `activation_probe` context 중 하나라도
  있으면 전체 activation 계약을 요구한다. 변조·다른 앱/Role은 `invalid_core_control_input` 오류다.
- 요청 query 본문은 bounded text인지만 확인하고 내용으로 분기하지 않는다. Knowledge 본문을 읽거나
  ready schema만으로 control을 선택하지 않는다. 입력 repair·정규화는 하지 않는다.

인증·ACL·sentinel/proof·실제 Workflow binding·wall-clock freshness는 이 함수의 보장이 아니다.
UTC 달력 유효성과 timestamp 간 동일성만 확인한다. 일곱 route 모두 실행/출력 허가가 아니며
ContextGuard → Knowledge Guard → 실제 LLM → Contract Guard/ResponseGuard가 필요하다.
분류기는 ready 응답·citation·canary 성공 결과나 모델 실행 증명을 만들지 않는다.

## 생성과 원본 검증

`core_control_graph.py::rebuild`는 Core source와 검토된 분류기/prompt를 받아 메모리 복사본을
반환한다. CLI·파일 쓰기·runtime 등록은 없으며 reviewed asset의 독립 SHA pin을 확인한다.
32노드·38엣지에서 policy allow 뒤 배타 분기를 둔다. Control/Contract LLM은 text를,
Query/Request/Work/Knowledge/Result LLM은 각 조립기의 후보를 합류시킨다.
Candidate Binding은 검증된 원래 요청의 transport metadata만 결속한 뒤 기존 Contract Guard로 전달한다.
본문·claim·citation·status는 바꾸지 않으며 실제 실행 UUID와 의미 검증은 최종 guard가 독립 수행한다.
Control/Contract LLM은 원본 model input, 나머지 LLM은 각각 전용 입력 투영을 받는다.
policy deny는 0회, 허용된 일곱 경로는 각각 1회 LLM을 지나도록 구성한다.

`code/core_query_input.py`는 Query 분기에서만 `query-input.v2`를 만든다. `task.text`는
backend가 검색 정책 query 보강 전에 캡처한 bounded 요청이다. 원본 pack의 예약 context
`query_business_input` 하나를 정확한 name·요청 결속 ID·canonical JSON/hash·동일 시각·
nonpartial·닫힌 text 형식·1,024 UTF-8 bytes로 검증해 task로 옮긴다. 누락·중복·변조는
실패하며 transport query나 source에서 복원하지 않는다. 이름/hash 자체가 권한 증명은 아니다.
`context`는 기존 metadata 네 필드와 transport query 및 옮긴 task row만 제외한다.
나머지 scope·출처 ID/본문·facts·metrics·role_context·시각·freshness·partial·flags와
`knowledge_context`는 값·순서 그대로다. 출처에는 양쪽 guard와 같은 `kind=ltm` 및
`canonical_id`의 `ltm:user-intent:` 접두사 판정으로 `source_authority`와
`allowed_claim_kinds`를 덧붙인다. 출처의 producer metadata만 아래 규칙으로 숨기며,
이 표시는 사실 검증이나 실행 권한이 아니다. 표시를 포함한 출력이 40,000 UTF-8 bytes를 넘으면
잘라내지 않고 거부하므로 이전에 허용되던 한도 근처 입력도 실패할 수 있다.
원본 API query·pack·proof·metadata는 변경 없이
Knowledge Guard, 조립기 및 양쪽 최종 guard에 전달한다. v2 게시에는 backend producer가
필수이며 이미 만들어진 구 pack은 거부한다. 원격 consumer를 먼저 되돌린 뒤 producer를 롤백한다.
canonical JSON·정확한 field·40000/32768 UTF-8 bytes·깊이32/노드4096 한도를 검사하지만
권한·source hash·schema 진실성의 별도 판단 주체가 되지는 않는다.

Query LLM은 내부 `query-draft.v2`의 contract/status/payload/missing_inputs만 작성한다.
Query Input은 인용용 `source_id`와 업무 본문·업무 식별자는 보존하되, 출처의
`content_sha256` 및 사용자 의도의 opaque `canonical_id`는 모델에 전달하지 않는다.
유사한 길이의 producer 식별자를 인용 ID와 혼동하지 않도록 하는 입력 투영이며,
원본 EvidencePack과 조립기·최종 guard의 출처 검증은 바꾸지 않는다.
`core_query_contract.py`가 canonical CompactQueryPlan에서 값의 enum·길이·개수와 optional
제약을 가져오고 narrative leaf만 `{value, kind, source_ids}`로 감싼다. 별도의 payload 정본을
유지하지 않는다. Query 전용 prompt는 이 내부 계약만 요구하며 full remote envelope를 요구하는
common prompt와 결합하지 않는다. `query_specialist.md`의 Query Design, Scope and Tool
Contract, Preflight Check 절만 strict 추출해 원문과 순서를 유지한 채 최종 draft 출력 계약
앞에 배치한다. 누락·빈 절·중복·heading 구조 변경은 실패하며 문자열 의미를 치환하지 않는다.
필터 문법·구성 scope·근거별 source coverage는 로컬 정본 한 곳이 소유하고 remote prompt는
wrapper/provenance/coverage wire 형식만 안내한다. 다른 Role의 실제 prompt는 그대로 유지한다.

`code/core_query_assembler.py`는 Knowledge Guard 원입력의 canonical schema/hash/role 연결을
재확인하고, 모델의 value와 kind를 바꾸지 않은 채 payload/claim에 복사한다. citation ID와
JSON Pointer, trusted 입력의 metadata와 아래 coverage의 boolean 표현만 결정적으로 만든다. boolean에는 텍스트 binding을
만들지 않으며 생략된 optional field는 생략 상태로 보존한다. 잘못된 형식·source 참조·상태
조합은 오류로 종료하고 부분 결과나 자동 수정 결과를 내보내지 않는다.
`needs_input`/`blocked`는 빈 payload와 provenance를 유지한다. 조립은 source의 진실성이나
조회 권한을 보장하지 않는다. fact를 recommendation으로 바꾸지 않으며 intent를 fact의 근거로
쓰는 경우도 기존 양쪽 guard가 계속 거부한다. native 형식 준수는 의미 품질 통과가 아니다.
내부 draft의 optional `coverage`는 `exhaustive|standard` 두 문자열만 허용한다. 조립기는
각각 canonical `exhaustive: true|false`로 정확히 대응하고 coverage 생략 시 exhaustive도
생략한다. 이는 명시적 wire 표현 변환이며 잘못된 기존 boolean의 수리나 의미 재판단이 아니다.
`"true"`/`"false"`, boolean, 숫자, 대소문자 변형, 알 수 없는 enum 및 구 draft는 거부한다.
LTM 정본 schema/hash와 양쪽 guard는 그대로이며 canonical 출력의 문자열 boolean도 계속 거부한다.
[Dify 1.11.1의 schema 준비 코드](https://github.com/langgenius/dify/blob/1.11.1/api/core/llm_generator/output_parser/structured_output.py)는
boolean type을 string으로 변환하고 구조화 응답은 Mapping으로 파싱한다. 설치된 서버의 동일
구현을 가정하지 않으며 `structured_output` 포트 존재만으로 타입 검증을 신뢰하지 않는다.
정본 제약이 변하면 이 표현을 임의로 넓히지 않고 재검토한다. 실제 provider 수용과 의미 품질은
별도 실측 게이트다.
canonical 모델 output 기본값은 4,096 tokens이며 실제 환경의 completion_params로 명시적으로
교체할 수 있다. system prompt 기본 한도는 8,192 UTF-8 bytes, Request/Work의 검토 예산은 아래와 같다.

Request는 실제 RequestArchitect schema의 의미 설명과 로컬 역할의 선택된 규칙을 정본에서
읽어 전달한다. 의미를 별도로 복사하거나 boolean 용어를 문자열 치환하지 않는다. 누락·중복된
선택 section, 변경된 목록 구조, UTF-8 예산 초과는 오류다. 최종 prompt의 독립 pin은 구조
schema에서 제외되는 description-only 변경까지 재검토 대상으로 만든다.
Request 분기는 Stage → Request Input → Request LLM → Request Assembler 순서다.
`code/core_request_input.py`는 공유 원본 결속 검사를 거쳐 `request-input.v1`을 만든다.
최종 schema·envelope metadata와 source content hash를 제외하고 source ID·원문·순서·scope·
시각·partial·facts·metrics·role_context·정책을 보존한다. 양쪽 guard와 같은 intent 판정
(`kind=ltm` 및 `canonical_id`의 `ltm:user-intent:` 접두사)에만 opaque canonical ID를 숨긴다.
다른 source의 업무 canonical ID는 보존하므로 전체 pack의 무손실 복사로 표현하지 않는다.
`source_authority`와 `allowed_claim_kinds`는 기존 claim 제한의 설명이며 사실 검증이나 승인이 아니다.
projection은 40,000 UTF-8 bytes를 넘으면 자르거나 원본으로 fallback하지 않고 실패한다.
조립기와 양쪽 guard는 계속 원본 input을 사용한다. 조립기는 잘못된 참조를 거부하고,
양쪽 최종 guard는 intent fact를 거부한다.
`core_request.md`는 이 전용 view와 출처, 내부 `request-draft.v2`의 wire 형식만 안내한다.
`intent`, `keywords[]`, `tasks[].completion_criteria[]`는 각각 경로 해석·검색어 선택·미래 완료 조건을
나타내는 계획 판단이므로 evidence로 뒷받침돼도 `inference|recommendation`만 허용한다.
다른 서술 경로의 `fact` 가능성과 출처 제한은 유지한다.
transport는 정확한 wrapper 경로와 plain 경로를 한곳에서 구분한다. `answer_depth` 등 plain
값에 annotation 객체를 보내거나 이전 draft 버전을 보내면 변환·기본값 보충 없이 거부한다.
공유 의미 규칙은 최종 LTM 계약의 참고이며 native 출력 형식이 아니다.
`research_readiness=ready|needs_clarification`, `tasks[].effect=write_requested|no_write_requested`는
각각 정본 `sufficient`와 `write_intent` boolean으로만 대응한다. 이는 승인이나 실제 실행이 아니다.
유효한 Request 계획은 확인 질문을 포함해도 `ok`일 수 있으며, `needs_input`과 `blocked`는
빈 payload/provenance를 유지한다. 본문·kind·출처는 바꾸지 않고 동일 text/kind/순서 있는 refs
조합만 claim을 공유한다. unwrap 뒤 정본 전체를 재검사해 annotation 차이로 가려진 중복도
거부한다. 40 claims·256 bindings·claim당 1,200자 한도를 넘으면 자르지 않고 실패한다.
조립기와 Query는 원본 Role/schema/source hash 결속 검사만 공유하며 의미 순회는 별개다.

Work는 exact CREATE에만 Stage → Work Input → Work LLM → Work Assembler를 사용한다.
`core_work_contract.py`는 변경 없는 CREATE에서 닫힌 `work-draft.v2`를 파생한다.
작성 문안은 value/kind/source_ids(inference/recommendation), 사실은 kind=fact와
source_id/source_path로 구분한다. 사실은 원본에서 주체가 포함된 전체 record를 선택한다.
Code는 필드와 값을 생략하지 않는 인용으로 표시하고 key/string 구분자를 escape하며 숫자
표기를 보존한다. 잘못된 경로를 고치거나 모델 문안을 사실로 변환하지 않는다.
required_input 대신 명시적 input_need
required/optional을 사용한다. 조립기는 그 선택을 boolean에 대응하고 원문·kind·참조와
optional 생략/빈 값을 보존하며 정본 전체를 unwrap 후 재검사한다. 근거 선택은 모델이 하고
조립기는 그 정확한 경로만 해석한다. 추측·repair·잘라내기와 유효하지 않은 UTF-8 출력은 허용하지 않는다.
`work-input.v2`는 출처 원문·순서·source_id, role_context hash와 compact source_content_index를
유지한다. opaque intent canonical ID는 숨기고, 나머지 canonical ID는 업무 표기용
entity_reference로 전달한다. 인용은 source_id만 사용하며 entity_reference로 치환하지 않는다.
compact ref는 request_plan.goal/tasks[].instruction의
정확한 정수 인덱스에만 허용한다. 업무 문자열을 ref로 해석하지 않는다.
intent 판정은 양쪽 guard와 같은 kind=ltm+canonical 접두사이며 fact 권한을 주지 않는다.
조립기·최종 guard는 원본 입력을 계속 사용한다. 40 claims/40 citations/256 bindings와
40,000-byte 입력/32,768-byte 원본 pack 예산을 넘으면 잘라내거나 fallback하지 않는다.
fact_paths는 원문을 바꾸지 않는 선택 안내다. 힌트 때문에 입력 예산을 넘으면 힌트만 생략한다.
전용 prompt는 pinned 로컬 CREATE 정책을 원문 span으로 선택하고 canonical 질문 의미를
전달한다. 보류·제외·미결 접근법·회의 metadata·분해·제목·DoD 규칙을 보존하며
assignee/parent_ref는 native 필드가 아니므로 추가하지 않는다. 새 중첩 업무는 children만 쓴다.
필수 정책 보존을 위한 system 예산은 Work 18,432, Request 10,240 UTF-8 bytes로 구분한다.
모델과 출력 예산은 현지 binding으로 별도 지정한다. 실제 품질·token·latency 효과는 별도 측정 대상이다.

Knowledge는 Stage → Knowledge Input → Knowledge LLM → Knowledge Assembler로 연결한다.
`knowledge-input.v1`은 기존 source 원문·순서·ID, facts/metrics/role_context와 정책을 보존한다.
최종 envelope/schema metadata와 source hash는 숨기고, opaque intent canonical ID 외의 업무 ID는
`entity_reference`로 표시한다. 인용에는 원본 `source_id`만 사용하며 `allowed_claim_kinds`는
기존 양쪽 guard의 intent 판정 설명이지 새 사실 권한이 아니다. 조립기는 원입력에 계속 결속한다.
canonical KnowledgeBrief에서 닫힌 내부 `knowledge-draft.v3`를 파생한다. 사실은 source_id/source_path로
완전한 원문 레코드를 선택하고, 해석·제안은 value/kind/source_ids로 한 번만 작성한다.
사실 선택은 root object 또는 root/top-level 배열의 완전한 레코드만 허용한다. 티켓 식별자를 잃는
중첩 상태 조각은 거부한다. native 출력은 concepts.explanation·our_context·gaps만 작성한다.
중복 references/term은 거부하며 공개 계약의 선택 필드는 변경하지 않는다. 출처 인용은 코드가 조립한다.
fact_paths는 완전한 record 위치만 안내한다. JSON root 문자열처럼
조립기가 허용하지 않는 위치는 추천하지 않으며, 권한·근거 판정은 힌트가 아닌 원본으로 수행한다.
로컬 Knowledge 역할 전체가 의미 규칙의 정본이며
전용 prompt는 trust boundary와 wire 형식만 추가한다. 생략/빈 값·kind·출처 순서를 보존하고
같은 value/kind/refs 조합만 claim을 공유하며 public 확장을 조용히 삭제하지 않고 거부한다.
`core_knowledge_contract.PRODUCER_LIMITS` 한 곳에서 32,768-byte draft·98,304-byte envelope·
40 claims·256 bindings를 입력 안내와 조립기에 결속한다. 출력은 한 번 직렬화해 strict UTF-8와
크기를 확인하며 초과·잘못된 surrogate는 repair/truncation 없이 실패한다. 짧은 선택자도
원문 복사로 커질 수 있으므로 draft 길이로 출력 크기를 추정하지 않고 최종 envelope를 직접 제한한다.
96 KiB는 내부 방어 한도이지 실제 Dify 설정의 증거가 아니다. 모델 출력 예산은 환경 binding과 별개이며
native Code/Answer/API 전송과 실제 의미 품질은 별도 실측해야 한다. 양쪽 최종 guard는 그대로다.

Result는 Stage → Result Input → Result LLM → Result Assembler로 연결한다.
`result-input.v1`은 이미 허용된 source·facts·metrics·role_context·정책과 partial 상태를 보존하고
schema/proof/version metadata와 source/context hash를 숨긴다. 로컬 task나 raw messages가
원격에 있다고 가정하지 않는다. 조립기와 최종 guard에는 원입력을 그대로 전달한다.
닫힌 `result-draft.v2`는 사실을 `source_id`와 `source_path`(원본 content 내부 RFC 6901 pointer)로
선택한다. assembler가 검증된 원본에서 값을 가져오므로 모델이 사실 문구를 재서술하지 않는다.
없는 경로·잘못된 JSON·1,200자 초과는 거부하며 자르거나 고치지 않는다. 나머지
inference/recommendation/open_fact의 ordered value/kind/source_ids를 payload와 claim에 그대로 복사하고
두 줄바꿈으로 합친 전체 `/text`에 모든 claim을 결속한다. 제목·label·marker도 같은 값에 속한다.
40 segments·값당 1,200자·32,768 UTF-8 bytes 한도를 넘으면 자르거나 repair하지 않는다.
한 값에 들어갈 수 없는 필수 Markdown 구성은 `blocked`로 표현하고 업무 근거 누락으로 꾸미지 않는다.
로컬 Result 의미 규칙을 사용하며 양쪽 최종 guard·일반 apply·승인/receipt 권한은 그대로다.
기계적 일치는 사실의 진실성·완전성·native 생성 품질의 증명이 아니다.

빌더는 배포 검증기가 아니다. 원본을 생성기로 먼저 수리하지 않는다.
`core_graph_validation.py`는 일곱 LLM의 prompt/schema/model/input, 분류기·입력 투영·조립기 source/I/O,
분기·합류·전체 배선을 검증한다. source와 DSL을 함께 바꿔도 독립 pin과 다르면 거부한다.
`validate_bundle.py`와 `materialize_environment.py`가 같은 검증기를 사용한다.
materializer는 원본 검증 뒤에만 앱 이름·모델·Knowledge binding을 교체하며 DEV/PROD의
일곱 Core LLM에 같은 환경 모델을 적용한다.

공유 schema 비교는 JSON 타입을 보존한다. boolean과 number는 다르고 숫자 1/1.0은 동등하다.
객체 키 순서는 무시하고 배열 순서는 유지한다. 비문자 키·비유한 수·비 JSON 객체·순환·
과도한 중첩은 검증 오류로 거부하고 입력을 수정하지 않는다.

## 검증 범위

로컬 회귀는 실제 probe factory → Guard → 분류기의 결속, 변조 거부, edge/도달성,
입력 보존·멱등성·복사본 독립성과 환경별 원본 검증을 확인한다. 정적 경로의 성질을
실서버 호출 횟수·지연·품질로 해석하지 않는다. 원격 게시·profile 검증·운영 활성화와
승인된 실 API 평가는 [번들 운영 절차](README.md)의 별도 게이트다.

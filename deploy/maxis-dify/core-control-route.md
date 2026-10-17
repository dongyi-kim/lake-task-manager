# Core 연결 검사 분리 계약

Core는 연결 검사와 업무 판단을 각각 한 LLM에 맡기며 두 경로 모두 기존 Knowledge Guard와
Contract Guard 사이에서 실행한다. 이 문서는 현행 소스 계약이다. 배포·평가 실행 결과는
ignored `.cache/`에 보관하며 커밋하지 않는다.

## 분류기

`code/core_control_route.py::main(model_input)`은 표준 라이브러리만 사용하는 순수 함수다.
Knowledge Guard의 policy allow 이후 canonical `ltm-model-input.v1` JSON을 입력받는다.
입력은 최대 9,000 UTF-8 bytes, context pack은 최대 6,144 bytes다. 정확한 envelope field와
object 형식을 요구하며 중복 key·비유한 수·비정규 JSON을 거부한다.
예약 식별자를 검사하는 `sources`/`role_context`는 object list여야 한다.

- 기본은 `semantic`이며 일반 업무의 전체 schema/evidence 유효성을 판정하지 않는다.
- Core factory의 activation만 `control`로 분류한다. `knowledge_curator`, canonical ready
  schema/digest, nonce와 source/fact/context/가명 사용자, UTC timestamp 연결, 빈 scope/metrics/flags,
  fresh/nonpartial 상태를 검사한다.
- `probe_` request ID, `ltm:maxis-probe:` source ID, `activation_probe` context 중 하나라도
  있으면 전체 activation 계약을 요구한다. 변조·다른 앱/Role은 `invalid_core_control_input` 오류다.
- query는 bounded text인지만 확인하고 내용으로 분기하지 않는다. Knowledge 본문을 읽거나
  ready schema만으로 control을 선택하지 않는다. 입력 repair·정규화는 하지 않는다.

인증·ACL·sentinel/proof·실제 Workflow binding·wall-clock freshness는 이 함수의 보장이 아니다.
UTC 달력 유효성과 timestamp 간 동일성만 확인한다. 두 route 모두 실행/출력 허가가 아니며
ContextGuard → Knowledge Guard → 실제 LLM → Contract Guard/ResponseGuard가 필요하다.
분류기는 ready 응답·citation·canary 성공 결과나 모델 실행 증명을 만들지 않는다.

## 생성과 원본 검증

`core_control_graph.py::rebuild`는 Core source와 검토된 분류기/prompt를 받아 메모리 복사본을
반환한다. CLI·파일 쓰기·runtime 등록은 없으며 reviewed asset의 독립 SHA pin을 확인한다.
16노드·17엣지에서 policy allow 뒤 배타 분기를 두고 Control LLM 또는 Contract LLM의 text
하나를 합류시켜 기존 Contract Guard로 전달한다. 두 LLM은 같은 검증된 model input을 받는다.
policy deny는 0회, 허용된 두 경로는 각각 1회 LLM을 지나도록 구성한다.

빌더는 배포 검증기가 아니다. 원본을 생성기로 먼저 수리하지 않는다.
`core_graph_validation.py`는 두 LLM의 prompt/schema/model/input, 분류기 source/I/O,
분기·합류·전체 배선을 검증한다. source와 DSL을 함께 바꿔도 독립 pin과 다르면 거부한다.
`validate_bundle.py`와 `materialize_environment.py`가 같은 검증기를 사용한다.
materializer는 원본 검증 뒤에만 앱 이름·모델·Knowledge binding을 교체하며 DEV/PROD의
두 Core LLM에 같은 환경 모델을 적용한다.

공유 schema 비교는 JSON 타입을 보존한다. boolean과 number는 다르고 숫자 1/1.0은 동등하다.
객체 키 순서는 무시하고 배열 순서는 유지한다. 비문자 키·비유한 수·비 JSON 객체·순환·
과도한 중첩은 검증 오류로 거부하고 입력을 수정하지 않는다.

## 검증 범위

로컬 회귀는 실제 probe factory → Guard → 분류기의 결속, 변조 거부, edge/도달성,
입력 보존·멱등성·복사본 독립성과 환경별 원본 검증을 확인한다. 정적 경로의 성질을
실서버 호출 횟수·지연·품질로 해석하지 않는다. 원격 게시·profile 검증·운영 활성화와
승인된 실 API 평가는 [번들 운영 절차](README.md)의 별도 게이트다.

# Core 연결 검사 분리

로컬 source DSL의 Core는 16노드·17엣지다. 연결 검사와 업무 판단을 각각 한 LLM에
맡기되, 두 경로 모두 기존 Knowledge Guard와 Contract Guard 사이에서 실행한다.
원격 DEV는 아직 이전 12노드 Core이며, 이 변경의 실제 연결 성공·업무 품질은 미검증이다.

## 분류기 계약

`code/core_control_route.py`의 `main(model_input)`은 표준 라이브러리만 사용하는
순수 함수다. Knowledge Guard가 policy allow 후 만든 canonical `ltm-model-input.v1`
JSON 문자열(최대 9,000 UTF-8 bytes, `context_pack` 최대 6,144 bytes)을 입력한다.
정확한 envelope field와 object 형식을 요구하며 중복 JSON key·비유한 수·비정규 JSON은 거부한다.
예약 식별자를 검사하는 `sources`와 `role_context`는 object의 list여야 한다.
이는 잘못된 컨테이너에 숨은 식별자를 놓치지 않기 위한 형식 검사이지 일반 업무의 전체 검증이 아니다.

- 기본 반환: `{"route":"semantic"}`. 일반 업무 schema나 evidence의 유효성을 판정하지 않는다.
- 현재 Core factory의 activation만: `{"route":"control"}`. `knowledge_curator`와
  canonical ready schema·digest, nonce에 연결된 source/fact/context/가명 사용자,
  UTC timestamp 연결, 빈 scope/metrics/flags, fresh/nonpartial 상태를 검사한다.
- 예약 식별자(`request_id`의 `probe_` prefix, source canonical ID의 `ltm:maxis-probe:`
  prefix, role context의 `activation_probe` name)가 하나라도 있으면 전체 activation
  계약을 요구한다. 변조·다른 앱·다른 role이면 `ValueError("invalid_core_control_input")`.
- query는 bounded text인지 확인할 뿐 내용은 분기 조건이 아니다. Knowledge 본문은 읽지 않는다.
  schema가 ready라는 이유만으로 control을 선택하지 않는다. 입력 수정·정규화·repair는 없다.

이 함수는 인증, ACL, Sentinel/proof 검증, 실제 workflow 결속 또는 wall-clock freshness
검증을 **대체하지 않는다**. UTC 달력 유효성과 timestamp 간 동일성만 확인한다.
`semantic`/`control` 모두 실행·출력 허가가 아니다. 원본문맥 ContextGuard,
Knowledge Guard/Policy Decision, 실제 LLM, Contract Guard/ResponseGuard가 그대로 필요하다.
ready 응답·citation·canary 성공 결과를 만들지 않으며 모델 실행 증명도 제공하지 않는다.

실제 factory를 이용한 provider-free 회귀는 아래에 기록한다. 분류기의 로컬 통합은
canary 실패 해결이나 업무 품질 개선의 증거가 아니며, 게시·실측은 별도 검토 대상이다.

## 오프라인 검증 (2026-10-08)

컨테이너 형식 회귀 4건을 수정 전 실패로 재현한 뒤 검사 경계를 보완했다.
최종 동결본은 이 단위와 client, ContextGuard, Editor routing, Knowledge Guard의
동일 5개 테스트 파일에서 **290 passed (18.22초)**였다. 이 중 분류기 회귀는 96개이며,
실제 probe factory → source DSL의 Knowledge Guard → 분류기 연결과 깊은 JSON의
내용을 노출하지 않는 오류 반환을 포함한다. 외부 API·실제 모델 호출은 없었다.

후속 호환성 보완: UTC 정규식과 원본 timestamp 결속은 유지하면서 날짜·시간의
숫자 성분으로 달력을 검사한다. ISO `Z` 파싱 지원의 Python 버전 차이에 의존하지 않는다
([Python 3.10 datetime API](https://docs.python.org/3.10/library/datetime.html#datetime.datetime.fromisoformat)).
실제 Dify Python 버전을 확인했거나 기존 원격 canary 실패 원인을 규명한 것은 아니다.
생성자 API만 제공하는 경계 테스트 7건에서 수정 전 실패를 재현했다. 이는 Python 3.10
실행 결과가 아니라 API 의존성을 제한한 합성 회귀다.
보완 뒤 동일 5파일은 **297 passed (18.74초)**였고, 분류기 회귀는 103개다.

## 그래프 생성 단위

`core_control_graph.py::rebuild`는 정본 Core DSL과 검토된 분류기·system prompt를
입력받아 **메모리 안의 복사본**만 반환한다. CLI·파일 쓰기·runtime 등록은 없다.
`reviewed_assets`는 source와 `common.md + core_control.md`를 읽고 독립 SHA pin을 확인한다.

생성 결과는 16노드·17엣지다. 기존 policy allow 뒤에 분류기와 배타적 분기를 넣고,
Control LLM 또는 기존 Contract LLM의 `text` 하나를 합류시켜 기존 Contract Guard로 보낸다.
두 LLM 모두 원래 Knowledge Guard의 `model_input`과 같은 모델·schema를 사용한다.
업무용 prompt, ACL·검색·Sentinel·policy deny, Guard source 및 candidate 이외 binding은 유지한다.

빌더는 **배포 검증기가 아니다**. 알려진 생성 노드의 data는 재생성하며, 기존 Guard의
안전성이나 전체 DSL의 적격성을 증명하지 않는다. 아래 배포 검증을 통과해야 환경별
import copy를 만들 수 있으며, 생성기로 raw DSL을 먼저 수리해서는 안 된다.

신규 그래프 회귀 30개는 독립 literal edge 기대값, 입력 보존, 0/1 LLM 경로의 정적 도달성,
멱등성·순서 변경·복사본 독립성, 잘못된 앱/배선/자산 거절을 포함한다.
통합 전 생성 단위 A의 동결본은 분류기·client·ContextGuard·Editor routing·Knowledge Guard·bundle·environment·
Policy Query·prompt integrity를 합친 **10파일 730 passed (226.19초)**였다.
별도 독립 코드 검토를 거치며, 정적 경로 확인을 실제 Dify 실행이나 LLM 품질 결과로 해석하지 않는다.

## 원본 검증과 환경 전환

`validate_bundle.py`는 `core_graph_validation.py`를 통해 두 LLM의 prompt·schema·모델·입력,
분류기 source와 I/O, 분기 조건·합류 값·전체 배선을 검증한다. source 파일과 DSL을 함께
바꿔도 기존 독립 SHA pin과 다르면 거부한다. 기존 정책·근거·workflow 검증은 유지한다.

`materialize_environment.py`도 읽어 들인 원본 DSL을 같은 검증기에 먼저 전달한다.
검증 뒤에만 앱 이름·모델·Knowledge 등 환경 binding을 교체한다. DEV/PROD 전환 시
두 Core LLM 모두 해당 환경의 모델을 적용하며, prompt와 원래 model_input은 유지한다.
원격 게시·profile 검증·운영 활성화는 이 오프라인 변환과 별개다.

정상 업무와 합성 연결 검사는 배타적 경로로 LLM 한 번만 호출하도록 연결했으며,
정책 거절은 기존 0회 경로를 유지한다. 이는 정적 경로의 속성이다. 실제 호출 횟수,
지연·token·결과 정확도는 게시 후 native 실행 증적과 기존 평가 배터리로 확인해야 한다.

## 통합 검증 결과

2026-10-08 통합 코드 `c411514`의 동결 소스와 배포 증적 회귀를 함께 검증했다.
관련 **13파일 863 passed (275.14초)**, bundle CLI와 DEV 환경의 세 import copy 생성이 통과했다.
검증 대상은 위 A의 10파일에 Core 원본 검증·Core receipt·Editor graph 테스트를 더한 것이다.
테스트 자료 준비는 `9ecadb8`로 분리했으며 통합의 비기계 변경은 484줄이다.

독립 검토 두 번은 모두 **9/10**이다. 잘못된 node title의 일반 예외를 3건 재현한 뒤
검증 오류로 거부하도록 수정했다. 초기 전체 실행의 25건 실패는 원본 검증 강화에 따른
기존 fixture·오류 문구·목록 순서 기대값이었으며, 수정 후 25건과 최종 전체 회귀가 통과했다.
두 모델의 검증·환경 전환, 원본 보존, source와 DSL 동시 변조 거부를 포함한다.

이 검토 점수와 테스트 수는 모델 품질 점수가 아니다. 원격 게시·LLM 호출·운영 활성화는 하지 않았다.

## JSON 스키마 타입 검증

공유 LLM 검증기의 Python 동등 비교는 schema의 `false`와 `0`, `true`와 `1`을
같게 취급했다. 별도 `codex/maxis-json-schema-types` 변경은 구조화 출력의 비교에만
JSON 타입을 적용한다. 불리언과 숫자는 구분하되 숫자 `1`과 `1.0`은 동등하게 유지한다.
객체 키 순서는 무시하고 배열 순서는 보존한다. 비문자 키·비유한 수·비 JSON 객체·순환·
과도한 중첩은 명시된 검증 오류로 거부하며 원본을 수정하지 않는다.

Core 두 노드, Editor 네 노드, PMO 한 노드의 타입 치환 21건이 수정 전 통과하는
문제를 재현했다. 신규 141개 테스트는 bundle과 환경 변환 진입점의 거부·허용 및
비교 함수 경계를 확인한다. 최종 동결본은 앞선 13파일과 이 신규 파일을 합쳐
**14파일 1,004 passed (344.03초)**였다. 독립 검토 두 번 모두 **9/10**, 필수 수정 없음이다.
실제 YAML 순환 anchor와 비유한 수의 별도 읽기 전용 진단도 통과했다.

prompt·source DSL·모델·Guard는 변경하지 않았다. 이 결과는 배포 입력 검증의 보강이며
LLM 응답 품질이나 속도 향상을 의미하지 않는다. Core 후보의 DEV 게시·실측은 별도 단계다.

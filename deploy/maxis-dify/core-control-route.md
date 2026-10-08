# Core activation classifier API (아직 runtime 미연결)

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

이 단위는 graph/DSL/prompt/모델/Guard/profile/evaluation에 연결되지 않았다.
실제 factory를 이용한 provider-free 회귀만 수행하며 canary 실패 또는 업무 품질의
개선을 주장하지 않는다. runtime 연결과 게시·실측은 별도 검토 대상이다.

## 오프라인 검증 (2026-10-08)

컨테이너 형식 회귀 4건을 수정 전 실패로 재현한 뒤 검사 경계를 보완했다.
최종 동결본은 이 단위와 client, ContextGuard, Editor routing, Knowledge Guard의
동일 5개 테스트 파일에서 **290 passed (18.22초)**였다. 이 중 분류기 회귀는 96개이며,
실제 probe factory → source DSL의 Knowledge Guard → 분류기 연결과 깊은 JSON의
내용을 노출하지 않는 오류 반환을 포함한다. 외부 API·실제 모델 호출은 없었다.

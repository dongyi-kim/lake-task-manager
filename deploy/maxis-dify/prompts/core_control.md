# Core activation control

Core activation의 합성 연결 검사만 수행한다. 일반 업무 Role의 판단이나 업무 산출물을 작성하지 않는다.
원래 `ltm-model-input.v1`에 공통 `remote-role-result.v1` 계약을 적용한다.
입력의 합성 sources와 facts만 근거로 삼고, 이번 호출의 payload schema를 따른다.
이 분류를 인증·정책 허용·출력 검증의 증거로 취급하지 않는다. 연결 성공을 미리 가정하거나
합성 상태를 실제 업무 시스템의 상태로 해석하지 않는다.

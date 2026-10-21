# 내부 시범 환경 준비

`internal-pilot`은 합성 전용 `dev`, 운영 `prod`와 별개인 후보 프로필이다.
**이번 변경은 설정·증적 결속·차단 경계만 구현한다. 내부 자료의 업로드, 서버 정책 적용,
접속 검사, shadow 실행, 운영 활성화를 완료했다는 뜻이 아니다.**

## 설정과 자산 분리

- [프로필 템플릿](internal-pilot-profile.template.json)은 관리자용
  `PUT /api/agent/maxis/profiles/internal-pilot` 입력이다. Dify import DSL이 아니다.
- `LAKE_MAXIS_INTERNAL_PILOT_ALLOWED_HOSTS`에 정확한 FQDN을 명시한다.
  기본 서버를 암묵적으로 상속하지 않으며 HTTPS:443만 허용한다. loopback 예외는 DEV에만 있다.
- `LAKE_MAXIS_PROFILE=internal-pilot`을 선택한 프로세스에만 공유 환경 변수와 API 키가 적용된다.
  키는 별도 암호화 저장소 또는 기존 환경 변수로 제공하고 템플릿에 기록하지 않는다.
- dataset UUID, release sentinel, 게시 Workflow UUID, API 키는 DEV·PROD 모두와 독립적이어야 한다.
  임시 저장은 허용하지만 충돌하는 프로필은 접속 이전에 차단한다.
- 정책 JSON은 `internalPilotPolicy` 또는 선택된 프로필의
  `LAKE_MAXIS_INTERNAL_PILOT_POLICY`로 설정한다. content 보존 상한 24시간, metadata 보존
  상한 7일은 **신규 시범 자산**의 요구사항이다. 낮출 수 있지만 높이거나 불리언으로 대체할 수 없다.
  기존 `remoteLogRetentionDays`는 콘텐츠 로그 한도이며 새 콘텐츠 보존 상한도 만족해야 한다.

## 증적과 실제 검증의 구분

`audience_scope_sha256`은 선별 자료를 열람할 수 있는 시범 사용자 범위의 버전 결속값이다.
실제 사용자 권한 확인이나 검색 결과의 ACL 검사를 대신하지 않는다.
`controls_attestation`에는 `contract=ltm-maxis-pilot-controls-attestation.v1`, `evidence_id`,
`artifact_sha256`, `profile_material_sha256`, `verified_at`, `expires_at`를 기록한다.
원문이나 사용자 목록 대신 안전한 식별자·해시·UTC 시점만 저장한다.

`GET /api/agent/maxis/status`의 `internalPilotControls.expectedMaterialSha256`는
현재 주소·데이터 정책·보존 상한·audience·게시 Workflow UUID·Knowledge manifest에 묶인다.
정책·릴리스 변경 또는 증적 만료 시 `bindingValid=false`가 된다.
기존 Knowledge 25-case 평가 증적도 해당 프로필에 독립적으로 결속한다.

**`bindingValid=true`는 제출한 메타데이터가 일치한다는 뜻일 뿐, 서버 검증 통과가 아니다.**
현재 다음 검증기가 미구현이므로 `valid=false`이며 probe/activation/resolve를 차단한다.

1. 서버의 콘텐츠·메타데이터 보존 및 삭제 정책에 대한 신뢰된 증적 검증
2. LLM 입력 전 사용자 ACL·권한 철회·삭제 자료 배제에 대한 증적 검증
3. 중첩 Workflow와 Knowledge까지 포함한 불변 릴리스에 대한 증적 검증

운영자 체크박스나 임의 JSON으로 이 차단을 해제하지 않는다. 이후 실제 검증기를 구현하고,
실패·만료·권한 철회 회귀를 통과한 다음에만 활성화 경계를 변경해야 한다.
현재 Dify 환경 materializer는 DEV/PROD만 지원한다. 내부 시범 자산 생성·게시 절차와
신뢰된 검증기 연결은 후속 배포 작업이며 이 템플릿을 materializer에 넣으면 안 된다.

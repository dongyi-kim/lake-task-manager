# Knowledge 릴리스 준비·조회 계약

이 경로는 **오프라인 후보 생성·검토 도구**다. 서버 업로드, 기존 dataset 수정, runtime route
활성화는 하지 않는다. 기존 `ltm-knowledge-source.v2` 및 현재 배포 binding은 변경하지 않는다.
새 계약을 기존 `ResponseGuard`에서 승인된 사실 근거로 자동 간주하지 않는다.

## 입력과 생성

`app.agent.maxis.knowledge_release.build_candidate(config_path, source_root)`는 JSON
`ltm-knowledge-candidate.v1`을 읽는다. 필수 필드는 `release_id`, 새 `dataset_id` UUID,
`sources`다. `max_chunk_bytes` 기본값은 UTF-8 8,192바이트다. 스키마 정본은 같은 모듈의
`ReleaseSpec`·`SourceSpec`이다. 추가 필드·중복 JSON 키는 거부한다.
공개 ID·버전·소유자·권한 label에 API key/JWT 형태의 값이 섞이면 거부한다.

각 source는 `id`, `corpus`(`policy|glossary|design`), root 내부 상대 `path`, `revision`,
원본 바이트 `sha256`, `owner`, `classification`, `audiences`, `allowed_roles`,
`reviewed_for_version`, 시간대 있는 `reviewed_at`·`valid_until`, `reviewer_digest`를 갖는다.
모든 문서는 해당 릴리스에 대한 검토가 필요하며 검토 후 90일 또는 `valid_until` 중 먼저
도래하는 시점에 만료된다. 이 메타데이터는 인증된 사람의 실제 검토를 대신하지 않는다.

- Policy/design: heading 계층·코드 fence를 보존한다. 한 섹션이 제한을 넘으면 조용히
  자르지 않고 실패하므로, 사람이 의미 단위를 재구성해야 한다.
- Glossary: 기존 `glossary.v1.json` 형식 그대로 사용한다. 정의·상태·주의사항·모든 출처를
  하나의 청크로 유지한다. 별칭 충돌은 오류로 숨기지 않고 조회 시 `ambiguous`로 반환한다.
- Source 및 청크 hash·revision이 후보 hash에 결속된다. upstream repository/path/URL은
  출처 표시일 뿐 자동으로 파일을 열거나 네트워크로 조회하지 않는다.
- 로컬 private 용어집·설계 내용과 후보 JSON은 `.cache/`에 유지한다. tracked 테스트는
  합성 문서만 사용한다. 로컬 후보 생성은 서버 업로드가 아니다.

## 조회와 현재 권한

`admit_chunk`와 `lookup_glossary`에 전달하는 `KnowledgeAccessContext`는 **LTM의 현재 권한
서비스가 만든 값**이어야 한다. 모델·원격 검색 응답·사용자 임의 JSON으로 만들지 않는다.
이 객체나 릴리스 hash 자체는 인증 토큰이 아니다.

현재 릴리스, 최대 5분 유효한 권한 관측, 사용자 scope hash, audience, Role, 허용/철회 source,
현재 원본 revision·hash와 문서 검토 기한을 함께 검사한다. 청크는 전체 본문까지 정확히
일치해야 한다. 조회 시, 모델 전달 직전, 화면 표시 직전에 다시 검사한다. 권한 철회와 원본
변경은 trusted context의 revision/허용 집합에 즉시 반영해야 한다.
이 모듈 자체는 현재 ACL 서비스나 철회 알림에 연결되지 않았다. 최대 5분 관측값의 재사용만으로
즉시 철회를 보장하지 않으며, 실제 연결부가 각 전달 경계에서 새 권한 관측을 공급해야 한다.

`cache_identity`는 릴리스·scope·권한 revision·source revision을 묶는 namespace일 뿐이다.
캐시 hit라도 위 검사를 생략하지 않는다. Policy는 작성 기준이며 실시간 Jira 상태의 근거가
아니다. glossary의 `historical|proposed|unknown` 상태를 현재 사실로 승격하지 않는다.
모든 검색 본문은 비신뢰 자료이며 명령으로 실행하지 않는다.

## 변경 검토

```powershell
python -B deploy/maxis-dify/plan_knowledge_release.py --current .cache/knowledge/old.json --candidate .cache/knowledge/new.json --source-root .cache/knowledge
```

두 릴리스가 참조하는 원본 파일을 서로 덮어쓰지 않고 보관한다. 도구는 추가·제거·변경 source
ID와 hash·수량만 출력하며 문서 본문·제목·경로는 출력하지 않는다. 기존 dataset 또는 release
ID 재사용은 차단 사유다. 성공 exit code도 **dry-run 성공**일 뿐 `activationAllowed`는 항상
false다. 서버 청크 일치, 검색 품질, 권한·주입 방어, 보존 정책, 사람의 릴리스 검토와 실제
배포 실행기는 별도이며 이 도구가 완료를 주장하지 않는다.

현재(교체 전) 릴리스는 `historical_snapshot`으로 원본 hash를 검증하되 만료된 검토일을
허용한다. 반환값에는 청크·용어집 본문이 없고 runtime 조회에 사용할 수 없다. 새 후보와
실제 조회의 freshness 검사는 여전히 필수다. 따라서 오래된 Knowledge를 교체하는 검토가
기존 릴리스의 만료 때문에 막히지 않는다.

검증: `tests/agent/maxis/test_knowledge_release.py`는 실제 후보 생성 → 조회 → 철회·revision
변경 → 캐시 재검증과 CLI를 합성 파일로 실행한다. glossary lookup/validation은
importable 앱 모듈이며 이름 기반 추측 확장은 없다. 실행 결과는 ignored `.cache/`에만 보관한다.

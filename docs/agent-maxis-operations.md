# MAXIS 연결과 새 서버 빠른 설정

## 이미 구성된 연결 사용

**설정 → AI 에이전트 → MAXIS / Dify Workflow → 저장된 MAXIS 설정 → 이 설정 사용**

- MAXIS 프로필은 이름을 붙인 연결이다. 앱의 개발/운영 환경과 무관하며, 기존 주소·API Key·게시 자산은 그대로 재사용한다.
- `이 설정 사용`은 연결·계약을 확인한 뒤 **Agent와 에디터의 실행 경로를 함께 MAXIS로 적용하고 설정창을 닫는다.** 실패하면 원인과 함께 창을 유지한다.
- 담당 업무·오늘 할 일·진척 조회도 사용하려면 **PMO 분석도 사용**을 선택한다. PMO Brief 게시 정보와 API Key가 필요하며, 이미 사용 중인 PMO 선택은 재적용 시 유지된다.
- AI 화면의 `MAXIS · 연결 이름`과 기능별 사용 가능 상태를 확인한다. 로컬 LLM 설정은 필요 없다.
- 새 연결은 **MAXIS 설정 추가**에서 이름을 입력한다. 기존 연결의 이름·주소·키는 해당 설정 카드에서 편집한다.
- `일반 사용`은 연결·계약·Knowledge 검사를 요구한다. `사전 품질 검증 필수`는 여기에 독립 검수·서명까지 요구한다. 연결 이름으로 정책을 결정하지 않으며, 연결 성공이 정성 품질 평가 통과를 뜻하지 않는다.

## 모델은 어디에서 선택하나?

- **MAXIS 사용 시 실제 모델은 연결한 Dify 프로젝트의 게시 Workflow LLM 노드가 선택한다.** LTM의 로컬 LLM 모델 선택과는 별개이며, MAXIS 연결마다 다른 모델·플러그인을 사용할 수 있다.
- 새 서버 준비 시 그 프로젝트에서 동작하는 모델을 YAML로 내보내 가져온다. 모델명뿐 아니라 provider·plugin과 지원 파라미터도 함께 옮긴다. DEV 설정을 PROD 모델로 간주하지 않는다.
- 노드의 `Structured Output` 체크와 **모델/플러그인의 네이티브 JSON 지원**은 별개다.
  현지 모델이 지원하는 경우에만 `response_format: json_object`를 설정하고 실제 역할 출력을 검사한다.
  미지원 모델에 옵션을 강제로 넣거나 JSON 괄호·중복 키를 임의 보정하지 않는다.
  [Dify 호환 플러그인 구현](https://github.com/langgenius/dify-official-plugins/blob/main/models/openai_api_compatible/models/llm/llm.py)도 등록된 지원 여부에 따라 옵션을 제공한다.
- `model.completion_params: {}`는 해당 서버 모델의 기본값 사용이다. 명시한 객체는 기본 파라미터를 **병합하지 않고 교체**한다. 예: `{"max_completion_tokens": 2048, "reasoning_effort": "low"}`. 지원하지 않는 `temperature`나 `max_tokens`를 강제로 보내지 않는다. 기존 파일에서 필드를 생략한 경우만 기존 워크플로의 기본 튜닝을 유지한다.
- 모델이나 파라미터 변경은 Dify에서 **관련 LLM 노드 모두 확인 → Publish → LTM의 게시 Workflow UUID 갱신 → 연결 검사 → 적용** 순서다. 초안만 수정하면 게시본은 바뀌지 않는다. 기존 모델로 자동 되돌리거나 새 모델을 자동 재게시하지 않는다.
- 연결 검사의 실제 생성 성공은 검사한 경로의 건강 상태다. 전체 분기·본문·댓글의 출력 품질이나 모델명 확인까지 뜻하지 않는다. Dify Service API가 모델명을 제공하지 않으면 **미확인**으로 표시하며 템플릿의 모델명을 실측 결과로 쓰지 않는다. 변경 후 실제 Agent·본문·댓글도 각각 확인한다.
- Core 검사는 **제어 응답 + 실제 Query 입력·LLM·조립·근거 검증 경로**를 호출한다.
  Editor·PMO의 연결 검사는 제어 경로이므로 댓글·본문 작성 검증을 대신하지 않는다.

## 새 서버 준비 (PROD 예시)

**스크립트 하나의 네 명령 + 포털에서 업로드·게시.** DEV 서버·DEV PC 파일은 필요 없다.
포털 로그인·Knowledge 등록·Workflow import/Publish·키 발급은 사람이 수행한다.
스크립트는 실행 중인 **로컬 LTM의 기존 설정 API**만 사용하며, 포털 관리자 API나 운영 승인 절차를 우회하지 않는다.

## 시작 전

- 이 브랜치의 clean commit을 PROD에 받고 LTM을 실행한다. 현재 Jira 사용자는 LTM 매니저여야 한다.
- 아래 명령은 **앱 저장소 루트**에서, 앱과 같은 Python 환경으로 실행한다. 기본 포트는 `4457`이다.
- 포털 ID/PW는 포털에만 입력한다. LTM 연결에는 **Dify Service API 주소 + 앱별 API Token**을 쓴다.
- 키·실제 자산 ID·생성물·평가 결과는 PROD 현지 `.cache/` 또는 암호화 저장소에만 둔다. Git에 넣지 않는다.

## 1. Knowledge 파일 준비 → 포털 등록

```powershell
python -B -X utf8 tools/maxis_setup.py prepare
```

생성되는 파일: `.cache/maxis/knowledge.zip`, `.cache/maxis/prod-setup.json`.
JSON은 이후 단계에서 계속 쓰는 **비밀 없는 설정 파일 하나**다. Token·비밀번호를 넣지 않는다.

ZIP을 풀고 PROD MAXIS에서 연결되는 **Dify의 Knowledge**에 다음과 같이 등록한다.
이 가이드의 dataset·문서 UUID는 Dify 기준이다. 별도 MAXIS 포털 Knowledge의 ID와 혼동하지 않는다.

| 파일 | 등록 위치 / 작업 |
|---|---|
| `knowledge/*.txt` 10개 | 업무 규칙용 Knowledge 하나에 업로드 |
| `sentinel/*.txt` | 별도 연결 검증 표식용 Knowledge에 업로드 |
| `document-metadata.csv` | 문서별 설명과 문자열 메타데이터 `ltm_allowed_roles_v1` 값을 그대로 적용 |

문서 이름을 바꾸지 않는다. 이미 자산이 있으면 **기존 Knowledge·문서를 갱신**하며 날짜별 사본을 만들지 않는다.

## 2. PROD 식별자 입력 → Workflow 생성

`prod-setup.json`에 다음 값을 포털에서 복사한다. UUID는 추측하거나 DEV 값을 재사용하지 않는다.

| JSON 항목 | 입력값 |
|---|---|
| `baseUrl` | PROD **Service API** 주소: `https://service.example/v1` (포털 로그인 URL 아님) |
| `datasetId`, `sentinelDatasetId` | 각각 업무 규칙·검증 표식 Knowledge UUID |
| `roleMetadataFieldId` | `ltm_allowed_roles_v1` 메타데이터 필드 UUID |
| `sentinelDocumentId`, `documentIds` | 표식 문서 UUID, 정본 문서별 UUID |
| `model` | 아래 YAML 가져오기로 추출 가능. 직접 입력하면 plugin/provider/name 세 값 필요. `completion_params: {}`는 서버 기본값 |

PROD에서 승인된 모델 하나를 사용하는 임시 Chatflow를 YAML로 내보내
`.cache/maxis/prod-model.yml`에 저장한 뒤 실행한다.

```powershell
python -B -X utf8 tools/maxis_setup.py bundle --confirm-host --model-file .cache/maxis/prod-model.yml
```

`--confirm-host`는 위 주소를 실제 PROD API 호스트로 확인했다는 뜻이다. 정확한 HTTPS FQDN/443을 사용한다.
기존 호스트 제한 환경변수는 우회하지 않는다. YAML에서는 모델 식별자와 검증된 공개 파라미터만 추출하며 코드를 실행하지 않는다.
파라미터는 유한한 숫자·boolean·허용된 enum만 받는다. 임의 문자열·중첩 객체·API key는 거부한다.
내보낸 노드들의 모델 또는 파라미터가 다르면 하나의 동일한 설정으로 다시 내보낸다.
결과는 `.cache/maxis/workflows.zip`. hash는 자동 계산한다. `model`을 직접 채웠다면 `--model-file`은 생략한다.

## 3. 포털 import·Publish → 연결

1. ZIP의 `workflows/*.yml`을 PROD **Dify Studio**에서 import한다. **Agent Core·Editor Author 필수, PMO Brief 선택**.
   갱신이라면 기존 앱을 업데이트한다. 모델·Knowledge 연결과 Role 필터를 확인하고 **Publish**한다.
   변경 전 게시본·모델·Knowledge 연결과 현재 LTM 설정을 현지 `.cache/`에 보관한다.
   생성본은 Core의 Candidate Binding·역할별 입력 투영, Editor의 DraftSpec 선택·검증,
   공통 근거 검증 코드를 함께 포함한다. 개별 프롬프트만 복사해서 배포하지 않는다.
   Core의 Query Assembler·Request Input/LLM/Assembler·Work Input/LLM/Assembler·Knowledge Input/LLM/Assembler·
   Result Input/LLM/Assembler와 Editor의 DraftSpec Guard·Plan Enforcer도
   같은 저장소 정본으로 갱신한다. 앱 코드만 pull하고 서버 노드를 그대로 두면 수정이 적용되지 않는다.
   게시 후 다시 내보내 노드·연결·프롬프트·코드와 현지 모델/Knowledge 설정이 의도한 값인지 확인한다.
   Result 내부 계약은 `result-draft.v2`: 사실은 원문 위치를 선택하고 해석·제안은 별도 타입으로 작성한다.
   외부 API는 유지되지만 Result LLM의 구조화 출력 schema와 두 코드 노드를 함께 갱신해야 한다.
   Knowledge 내부 계약도 `knowledge-draft.v2`이므로 해당 LLM schema·코드 노드를 함께 갱신한다.
   사실은 완전한 원문 레코드, 해석·제안은 별도 타입으로 작성하며 최종 근거 검사는 생략하지 않는다.
2. `prod-setup.json`의 `workflowVersions`에 각 앱의 **게시 Workflow UUID**를 입력한다.
   앱 ID·실행 run ID와 혼동하지 않는다. PMO를 사용하지 않으면 빈 값으로 둔다.
3. `remoteLogPolicyId`, `remoteLogMode`, `remoteLogRetentionDays`를 실제 승인된 로그 정책대로 입력한다.
   모드는 `redacted` 또는 `approved_bounded_content`, 보존 기간은 0~90일이다.
4. 각 앱의 API Token을 발급한 뒤 실행한다. 이 명령은 **실제 PROD 연결 검사 요청**을 보낸다.

```powershell
python -B -X utf8 tools/maxis_setup.py connect --confirm-remote
python -B -X utf8 tools/maxis_setup.py status
```

Token은 터미널의 **숨김 입력**으로만 받고 LTM이 Windows DPAPI로 저장한다. 빈 입력은 기존 값을 유지한다.
Token을 명령 인자·JSON·메신저에 넣지 않는다. 포털 앞단에 별도 SSO/Basic 인증이 있다면 앱 Bearer 인증과의 호환을 먼저 확인한다.
**연결 성공 ≠ 운영 사용 가능**이다. 스크립트가 평가 통과를 만들거나 실행 경로를 자동 적용하지 않는다.

## 4. 평가·검수·적용 (UI)

**설정 → AI 에이전트 → MAXIS / Dify Workflow → 대상 연결 선택 → MAXIS 설정 추가 → 선택한 설정의 서버 자산 준비 → 4 · 검증과 적용**

위 스크립트는 기존 `prod` 저장 ID를 기본값으로 사용한다. 이는 호환용 내부 식별자이며 UI의 연결 이름·사용 정책과 별개다.
UI에서 만든 연결의 평가를 서명할 때는 UI가 안내하는 `--profile` 인자를 그대로 사용한다.

1. 이 PROD 환경에서 합성 평가를 시작한다. Knowledge 30건(PMO 포함 31건), 선택 Role case 묶음을 5회 반복한다.
2. 검토 원본을 `.cache/maxis/downloaded-report.json`으로 저장한다. 별도 검토자가 raw 출력과
   `rubric`·`reviewSpecs`를 읽고 `reviewTemplate`을 채워 `review-completed.json`으로 저장한다.
   축별 checklist·점수·근거·출력 인용·UTC 검토 시각과 평가자 정보를 빠짐없이 작성한다. LTM 모델을 judge로 쓰지 않는다.
3. 아래 첫 명령의 **공개키와 지문만** UI에 등록하고, 검토 완료 후 두 번째 명령으로 서명한다.

```powershell
python -B -X utf8 tools/maxis_qualification.py reviewer-key --output .cache/maxis/reviewer-key.json
python -B -X utf8 tools/maxis_qualification.py sign --report .cache/maxis/downloaded-report.json --review .cache/maxis/review-completed.json --key .cache/maxis/reviewer-key.json --output .cache/maxis/qualification-signed.json
```

기존 키는 재생성하지 않는다. 개인키는 DPAPI 보호 상태로 이 PC에만 둔다. 기존 평가 파일도 덮어쓰지 않는다.
UI에 서명 파일을 가져와 **이 설정을 Agent·에디터에 사용**을 누른다. 서명은 현재 연결·배포·기능에 결속되며 최대 7일 유효하다.
새 대화와 본문·댓글 AI에서 `MAXIS · 연결 이름` 사용 가능 상태를 확인하고 앱 재시작 후에도 확인한다. 로컬 LLM 설정은 필요 없다.

## 재실행·문제 해결

- 같은 소스의 `prepare` 재실행은 기존 진행 상태와 편집한 설정을 보존한다. 생성 ZIP 교체가 필요하면 `--replace-files`를 명시한다.
- 소스가 바뀌면 `prepare --refresh-source --replace-files`로 같은 설정 파일의 준비 식별자·문서 목록만 갱신한다. 입력한 PROD 값은 유지하고 새 문서 UUID를 채운 뒤, 2번의 `bundle` 명령에 `--replace-files`를 추가해 반복한다. 모델 YAML을 사용했다면 `--model-file`도 다시 지정한다.
- 소스·주소·자산·Token 변경 후에는 해당 단계부터 준비·게시·연결 검사·평가를 다시 수행한다. 오래된 run/hash를 억지로 재사용하지 않는다.
- 403: LTM의 Jira 로그인과 매니저 권한 확인. 401/연결 실패: Service API 주소·앱별 Token 확인.
- Knowledge/계약 오류: 게시 UUID, 문서 이름·UUID, Role 메타데이터, 표식 dataset 분리 확인. UI에서 세부 검사 결과를 본다.
- 정상적인 검색 0건과 불완전한 조회는 다르다. 0건이면 조회 범위를 밝히고 필요한 대상을 질문한다.
  캐시 만료·조회 실패·잘림은 빈 검색 결과로 숨기지 않는다. 새로 조회하거나 범위를 좁힌 뒤 다시 실행한다.
  MAXIS 근거 수집은 화면용 캐시 TTL과 별도로 15분 이내 데이터를 요구하며 오래된 캐시는 동기 갱신한다.
  갱신 실패 시 이전 데이터의 실제 시각과 불완전 상태를 보존한다. 캐시를 지우거나 시각만 바꿔 통과시키지 않는다.
  진척 조회는 정상 빈 분기와 실패를 구분하고 Jira/Confluence의 출처를 분리한다.
  완료된 범위별 검색은 실제 반환 목록의 건수를 출처에 연결한 수치 근거로 전달한다.
  이 건수는 프로젝트 전체 수량이나 대상의 부재를 뜻하지 않는다.
  연결 티켓·문서의 식별자가 맞지 않는 내용은 근거에서 제외한다. 화면용 실패 캐시는 근거로 재사용하지 않고,
  집계는 원래 조회 시각을 가진 하위 캐시에서 재구성한다. 최근 이력·댓글 발췌를 전체 이력이라고 해석하지 않는다.
- 추가 입력이 필요한 정상 응답은 HTTP 200과 질문으로 반환한다. `추가 입력 필요`는 실패나 생성 완료가 아니다.
  질문에 답하면 원 요청과 현재 답을 함께 전달한다. 새 주제·취소에는 이전 답을 재사용하지 않는다.
  실제 계약 오류는 Dify 실행 이력의 실패 노드와 LTM 디버그의 실패 역할을 함께 확인한다.
  검증된 계약·출처 연결 거부가 있으면 이를 우선 안내한다. `partial_role_context` 같은 맥락 제한만으로
  실제 거부 원인을 “조회 실패”로 단정하지 않는다. 사람 표시는 현지 Jira의 정확한 사용자 식별자로만 복원한다.
- 실패한 원격 요청도 요청 횟수에 포함한다. 토큰·모델·비용이 서버 응답에 없으면 미확인이다.
  Workflow/Knowledge 라벨은 프로필 메타데이터이며 실제 게시 UUID나 품질 통과 증명이 아니다.
- Core의 네이티브 초안 JSON/형식·허용 조회 소스 오류는 원래 근거로 **한 번 재생성**한다.
  공간이 있을 때 로컬의 고정된 검증 오류 안내만 비인용 맥락으로 추가한다. 원격 오류 원문·거부된 초안을
  다시 입력하지 않으며 원래 근거·hash·조회 시각·프로필은 바꾸지 않는다.
  서버 노드의 `native_draft_*` 진단은 JSON 문법·중복/한도·스키마·출처 ID·원문 선택을 구분한다.
  이 진단은 재생성 안내일 뿐 통과 증명이 아니며, 해당 진단 코드가 없는 기존 게시본은 일반 안내를 사용한다.
  Editor는 원본 입력·근거를 먼저 검증한 뒤, 존재하지 않는 선택 ID 또는 검증된 선택의 64개 상한 초과만
  한 번 재생성한다. 다른 소유자의 근거·형식·충돌 오류가 섞이면 재생성하지 않으며 선택을 임의로 잘라내지 않는다.
  ID를 유사한 값으로 고치거나 출처·권한 검사를 생략하지 않는다. 첫 실패의 감사·사용량도 남는다.
  선택 후보 수와 출력량은 별개다. 최종 글은 계속 32개 블록 이내이며, 모든 후보를 출력하지 않는다.
  인증·모델·입력 오류나 최종 근거 검사 거부는 재생성하지 않는다. 재시도 중 근거가 15분을 넘으면 차단한다.
  두 번째도 실패하면 원인을 표시하며 로컬 LLM이나 임의 수정한 JSON으로 우회하지 않는다.
- 감사 결과의 `false` 판정·보완 사항은 감사 실행 실패와 다르다. 정상 감사 결과를 LTM이 받아
  수정/추가 질문 절차로 이어간다. 실패를 없애려고 감사 판정을 강제로 통과시키지 않는다.
- 환경변수 우선 설정·Token 교체/삭제는 선택한 MAXIS 설정의 상세 항목에서 확인한다. Workflow/Knowledge 버전 환경변수가 있으면 스크립트 연결은 중단한다. 이 경로를 쓰려면 충돌 변수를 제거하고 LTM을 재시작한다. 다른 PC의 암호화 파일을 복사하지 않는다.
- 실제 PROD 품질은 현지 평가·리허설로 확인한다. 로컬 테스트나 연결 검사만으로 품질 통과라고 하지 않는다.
- 게시 후 검사에 실패하면 보관한 내용을 **동일 앱**에 복원하고 다시 Publish한다.
  복원 게시에도 새 Workflow UUID가 발급될 수 있으므로 LTM 연결값을 갱신하고 재검사·적용한다.
  동시 편집이 발견되면 덮어쓰지 말고 변경자를 확인한다. 다른 앱을 날짜별로 복제하지 않는다.

## 기존 배터리로 실행 안정성 확인

연결을 적용한 뒤 앱 저장소 루트에서 실행한다. 아래 환경변수는 **평가 프로세스만 합성 Jira로 실행**한다.
현지 연결의 설정/암호화 키를 읽는 `CONFIG_DIR`·`LAKE_CACHE_DIR`는 실행 중인 LTM과 일치해야 한다.
평가는 격리된 DB를 사용하며 실제 Jira에 쓰지 않는다. 실제 MAXIS 호출 비용은 발생할 수 있다.

```powershell
$env:JIRA_ENV = "mock"
python -B -X utf8 tools/agent_eval_launcher.py maxis all --profile "선택한 연결 ID" --repeat 5 --confirm-remote
```

`--profile`은 설정 API의 연결 ID이며 표시 이름이나 DEV/PROD 분류가 아니다.
문제 집중 재현은 `all` 대신 `compose --case CMP1`처럼 기존 사례를 선택한다.
Conversation·Create·Compose·Meeting·Context 배터리의 기존 입력을 재사용하고 로컬 LLM/임베딩 호출은 차단한다.
실패나 중단은 `.cache/agent-evaluation/`의 고유 실행 그룹에 즉시 기록하며 다음 성공으로 덮어쓰지 않는다.
중단된 그룹은 5회 완주로 집계하지 않는다. 수정 후 새 그룹에서 처음부터 반복한다.

이 명령은 **실행 안정성 진단**이며 위의 독립 정성 검수·서명·운영 자격을 대체하지 않는다.
실제 웹 UI의 질문/후속 답변·승인/취소·새 대화·재시작과 본문/댓글 생성·되돌리기·저장 전 미게시도 별도 확인한다.

## 데모 확인 (같은 티켓, 각 3회)

- **Agent:** “이 티켓과 댓글의 상태·미확인 사항을 근거와 함께 요약하고 후속 Task를 초안으로 제안해 줘. 담당자는 미할당, Jira 변경 금지.”
- **본문:** “기존 수치·링크를 보존해서 목적·범위·완료 기준으로 정리해 줘. 없는 사실은 추가하지 마.”
- **댓글:** “확인된 변경점과 다음 확인 사항을 짧은 검토 요청 초안으로 작성해 줘.”

근거·사실/미확인 구분, 기존 글 되돌리기, 사용자 저장 전 미게시를 확인한다.
이번 안정화의 합격 기준은 전체 배터리 5회 완주 중 예상 밖 기술 실패 0건이다.
정성 점수는 기록하되 4/5를 필수 합격선으로 삼지 않는다. 사실 발명·미승인 변경·핵심 결과 누락은 허용하지 않는다.
실제 PROD에 접근하지 않고 수행한 테스트를 PROD 적용·리허설 완료로 표시하지 않는다.
세부 계약은 [아키텍처](MAXIS_DIFY_MIGRATION.md)와 [배포 소스](../deploy/maxis-dify/README.md)를 따른다.

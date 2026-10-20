# PROD MAXIS 연결과 데모 운영

이 가이드는 **저장소를 새로 받은 PROD Windows PC**에서 시작한다. DEV MAXIS 접속, DEV PC의
cache·내보내기·평가 결과, 개인 OpenAI 연결은 필요하지 않다. 준비·게시·연결 확인·평가·적용은
각각 별도 단계이며, 준비 파일 생성만으로 PROD 사용이 활성화되지는 않는다.

## 준비 조건

- 현재 배포 소스를 포함한 clean commit을 받는다. 실제 PROD 평가는 변경·미추적 소스가 남은
  checkout을 거부한다(`qualification_clean_commit_required`, `requiresCleanCommit`). 소스 변경은
  먼저 검토·커밋·배포하고, PROD PC에서는 그 커밋을 기준으로 평가한다.
- 앱은 설정된 포트 `4457`에서 실행한다. 개발 확인은 `python run.py --reload`를 사용한다.
- Jira에 로그인한 현재 사용자가 LTM 매니저여야 준비 파일·호스트·token·실행 경로를 바꿀 수 있다.
- PROD 포털 접근과 새 Knowledge·Chatflow 생성·게시 권한, 그 환경에서 승인된 모델 연결이 필요하다.
- 원격 실행 로그의 내용 범위와 보존 기간을 실제 포털 설정 및 승인 정책과 대조한다.

**포털 ID/PW와 runtime API token은 다르다.** ID/PW·SSO는 포털 로그인에만 사용한다. LTM이
지원하는 Dify Service API 인증은 앱별 `Authorization: Bearer <App API token>`이다.
PROD 앞단이 별도 Basic 인증·SSO·추가 헤더를 요구하면 현재 Bearer 연결과 호환되는지 확인해야 한다.
포털 비밀번호를 token 입력란에 넣거나, 로그인 URL을 Service API 주소로 사용하지 않는다.

## 1. 저장소 정본 Knowledge 준비

1. **설정 → AI 에이전트 → MAXIS / Dify Workflow → PROD MAXIS 시작하기**를 연다.
2. `1 · 준비 파일`에서 PROD 포털 URL을 입력하고 해당 포털에서 로그인한다. LTM은 ID/PW를
   수집하거나 저장하지 않는다.
3. **정본 Knowledge 준비 ZIP 생성·다운로드**를 누른다. 현재 `knowledge/`와
   `config/maxis-knowledge.yml`의 공개 가능한 정본 10개 문서, 문서별 Role 메타데이터 대조표,
   이 PC에서 새로 생성한 PROD 릴리스 표식이 ZIP에 포함된다.
4. 처음에는 포털에 Knowledge dataset과 **별도** 릴리스 표식 dataset을 만든다.
   갱신 시에는 기존 dataset·파일을 수정하며 날짜별 사본을 추가하지 않는다.
   `knowledge/*.txt`와 `sentinel/*.txt`를 각각 업로드·갱신한다.
5. Knowledge 문서마다 문자열 메타데이터 `ltm_allowed_roles_v1`을 만들고
   `document-metadata.csv`의 값을 정확히 적용한다. 문서 이름도 ZIP의 `.txt` 이름을 유지한다.
   CSV의 문서별 설명도 각각 적용한다. 검색 결과의 Role·문서·크기 검사는 Workflow와 LTM
   양쪽에서 계속 수행된다. 같은 소스로 다시 준비하면 기존 ZIP과 진행 상태를 이어 사용한다.

일반 규칙·공개 LTM 기술 구성·용어집의 정본은 저장소에 하나만 둔다. 날짜·버전별 사본을 추가하지
않는다. 사내 프로젝트 원문·개인정보·비공개 구성은 이 공개 저장소에 넣지 않는다. PROD의 선택적
비공개 Knowledge 추가는 별도 검토 대상이며 기본 데모의 준비 조건이 아니다.

## 2. 실제 PROD 자산에 Chatflow 연결

1. `2 · 자산 연결`에 포털의 **Service API Base URL**을 입력한다. 예:
   `https://service.example/v1`. 인증정보가 없는 정확한 FQDN, HTTPS 기본 포트 443을 사용한다.
2. 주소를 대조한 뒤 체크박스를 선택하고 **이 PC에 정확한 PROD 호스트 등록**을 누른다.
   이 단계는 token을 보내지 않는다. 배포 환경변수가 호스트를 고정했다면 관리자가 그 설정을
   변경해야 하며, UI 저장으로 환경변수 제한을 우회하지 않는다.
3. 업로드 후 생성된 실제 Knowledge dataset, 별도 sentinel dataset·문서, Role 메타데이터 필드,
   정본 문서별 UUID를 포털에서 복사한다. UUID를 임의 생성하거나 DEV 값을 복사하지 않는다.
4. 포털에서 승인된 모델 하나를 사용하는 간단한 Chatflow를 YAML로 내보낸 뒤
   **승인된 모델 설정 YAML 가져오기**로 선택한다. LTM은 model plugin/provider/name만 추출하며
   원본 YAML·prompt·환경변수·credential을 저장하거나 실행하지 않는다.
5. **PROD Chatflow 3개 생성·다운로드**를 누른다. 릴리스·source·sentinel hash는 자동 계산된다.
   사용자가 hash를 만들거나 직접 입력할 필요가 없다.

이 준비 과정은 로컬 파일만 생성한다. 포털 자산을 자동 생성·수정·게시하거나 포털 관리 API가
있다고 가정하지 않는다. 생성 파일은 현재 PC의 ignored cache에 남으며 Git에 넣지 않는다.

## 3. 포털 게시와 token 저장

1. 처음에는 내려받은 `workflows/*.yml`을 PROD 포털의 Chatflow로 import한다. 갱신 시에는
   기존 앱을 업데이트하고 다시 게시한다. **Agent Core와 Editor Author는 필수**,
   PMO Brief는 선택 사항이다.
2. 승인된 모델과 Knowledge binding, 문서 Role 필터, 별도 릴리스 표식 조회를 확인하고 Publish한다.
3. 각 앱의 실제 **게시 Workflow UUID**와 **App API token**을 확인한다. 앱 ID·message ID·실행
   run ID를 게시 Workflow UUID 대신 입력하지 않는다.
4. `3 · 게시와 token`에서 필수 앱의 게시 UUID와 원격 로그 승인 정책 ID·내용 범위·0~90일 보존
   기간을 입력하고 **게시 정보로 PROD 후보 저장**을 누른다. PMO를 사용할 때만 해당 선택을 켠다.
5. 앱별 token을 입력하고 **token을 Windows DPAPI에 저장**을 누른다. 빈 입력은 기존 token을
   유지한다. 삭제·교체·환경변수 우선 상태는 고급 설정에서 확인할 수 있다.

Token은 현재 Windows 사용자의 DPAPI로 로컬 보호된다. 다른 PC·Windows 계정의 암호화 파일을
복사해 연결을 이전하지 않는다. Token 원문은 상태 응답이나 ZIP으로 반환되지 않는다.
프로필·token·주소·게시 UUID를 바꾸면 기존 연결 확인과 평가 결속을 다시 확인해야 한다.

## 4. 이 PROD 환경의 평가와 독립 검수

1. `4 · 검증과 적용`에서 **저장된 PROD 연결과 Knowledge 확인**을 누른다. 앱 mode, Start 입력,
   게시 버전, Knowledge 조회·인용·릴리스 표식 계약을 검사한다. 연결 성공은 품질 통과가 아니다.
2. **PROD 합성 평가 시작**을 누른다. 이 환경의 실제 게시 앱에 저장소 fixture만 전송한다.
   현재 Knowledge 검사는 **Core·Editor 30건, PMO 포함 시 31건**이며, 선택된 Role case 묶음을 **5회 반복**한다. 정확한 건수와
   진행 상태는 화면을 따른다. DEV 이력이나 local LLM Shadow 기준선은 필요하지 않다.
3. 완료 후 **이 PROD 실행의 검토 원본 내려받기**를 누른다. JSON에는 `report`, `rubric`,
   `reviewSpecs`, `reviewTemplate`이 포함된다. 전체 출력과 공통·case별 검토 항목을 직접 읽는다.
4. 별도 검토자는 `reviewTemplate`을 `.cache/maxis/review-completed.json`으로 저장하고 각 case의
   실제 판정·점수·checklist·특수 검토·근거·출력 인용을 작성한다. `reviewedAt`은 UTC `Z` 시각이며,
   evaluator의 종류·모델·직접 raw 판독·LTM LLM judge 미사용 여부를 사실대로 기록한다.

도구는 통과 점수나 검토 근거를 자동으로 채우지 않는다. LTM runtime 모델을 평가자로 사용하지
않는다. 자동 계약 검사 통과만으로 실제 답변 품질이 통과한 것으로 취급하지 않는다.

### PROD PC의 검토자 키와 서명

앱 저장소 루트에서 실제 사용하는 Python으로 실행한다. `.cache/maxis/`에 같은 이름의 키가
이미 있으면 재생성하지 말고 기존 검토자 키를 사용한다. 키·결과 파일은 덮어쓰지 않는다.

```powershell
python -B -X utf8 tools/maxis_qualification.py reviewer-key --output .cache/maxis/reviewer-key.json
```

출력되는 **공개키와 지문만** 화면의 독립 검토 영역에 입력한다. 검토자와 지문을 별도로 대조하고
체크박스를 선택해 **검토자 공개키 등록**을 누른다. 개인 서명키는 DPAPI로 보호되며 웹 화면에
입력하지 않는다. 키 생성은 공개키 등록이나 운영 승인을 자동 수행하지 않는다.

내려받은 원본을 `.cache/maxis/downloaded-report.json`으로 저장하고, 검토를 모두 작성한 뒤 서명한다.

```powershell
python -B -X utf8 tools/maxis_qualification.py sign --report .cache/maxis/downloaded-report.json --review .cache/maxis/review-completed.json --key .cache/maxis/reviewer-key.json --output .cache/maxis/qualification-signed.json
```

`--report`는 내려받은 전체 JSON bundle도 받는다. 서명 도구는 먼저 등록된 공개키, 실제 완료된
검토, 현재 PROD binding과 계약을 검증한다. 화면에서 `qualification-signed.json`을 가져오면
서명·case 집합·현재 배포 결속·기간을 다시 확인한다. 서명은 평가 완료 후 최대 7일 범위이며,
변경되거나 만료된 결과를 그대로 재사용하지 않는다.

## 5. 운영 적용과 데모

**Agent·에디터에 PROD MAXIS 적용**은 필수 기능의 유효한 검수 증명이 있을 때만 누를 수 있다.
버튼은 프로필과 선택한 기능 경로를 한 번에 적용한다. PMO를 나중에 추가하면 그 기능을 포함한
평가와 검수가 다시 필요하다. 기존 대화의 runtime pin을 바꾸지 말고 **새 대화**로 확인한다.

다음 세 입력으로 데모한다. 실제 읽기 권한이 있고 완료되지 않은 Task를 선택하며, 답변의 출처와
미확인 사항을 확인한다. 초안 표시만으로 Jira 저장이 완료된 것은 아니다.

1. **Agent:** “`<시연 티켓 키>`와 댓글을 읽고 현재 상태와 미확인 사항을 근거와 함께 요약해 줘.
   필요한 후속 Task의 제목·목적·범위·완료 조건은 초안으로만 제안하고 담당자는 미할당으로 둬.
   Jira는 생성하거나 변경하지 마.” 자리표시자는 실제 조회 가능한 티켓 키로 바꾼다.
2. **본문 AI:** “기존 내용을 보존해서 목적·범위·완료 기준으로 정리해 줘. 제공된 자료에 없는
   수치·담당자·기한은 추가하지 마.”
3. **댓글 AI:** “현재 티켓에서 확인된 진행 상황과 다음 확인 사항을 짧은 코멘트 초안으로 정리해 줘.
   아직 확인되지 않은 완료나 일정을 확정하지 마.”

현재 runtime의 기능별 준비 상태와 원격 요청 관측을 함께 확인한다. 과거 run UUID나 HTTP 성공만으로
현재 경로·답변 품질을 증명하지 않는다. Jira 쓰기는 기존 exact payload 승인 경계를 유지한다.
MAXIS 오류·긴급 중단을 개인 OpenAI나 local 모델로 조용히 우회하지 않는다.
같은 티켓으로 세 장면을 이어 진행하고 각 장면을 3회 반복한다. 근거·수치·링크 보존,
기존 내용 되돌리기, 사용자 저장 전 미게시를 확인하고 실제 출력은 PROD 안에서 독립 검수한다.
rubric 4/5 이상과 치명 결함 없음은 현장 리허설의 목표이며 이 문서의 달성 주장이 아니다.

## 변경·실패와 미완료 범위

- **소스 변경:** Knowledge·prompt·Role·DSL·평가 소스가 바뀌면 준비 ZIP과 Workflow를 새로 생성·게시하고,
  현재 PROD binding으로 연결 확인·평가·독립 검수를 다시 수행한다. clean commit을 사용한다.
- **인증 실패:** 포털 로그인 성공과 App API token 유효성을 구분한다. 올바른 앱·환경의 token인지
  확인하고 UI에서 교체한다. 원문 token을 로그·이슈·스크린샷에 남기지 않는다.
- **계약/Knowledge 실패:** 실제 게시 UUID, `.txt` 문서 이름, Role 메타데이터, dataset·sentinel 분리와
  검색 결과 크기를 확인한다. Guard를 끄거나 임의 hash를 입력해 통과시키지 않는다.
- **서명 실패:** 현재 run, 공개키 등록·지문, 완성된 검토, 배포 결속, 만료 여부를 확인한다.
  key·평가 결과·점수를 자동 생성해 운영 권한을 얻는 경로는 없다.
- **남은 현장 확인:** 실제 PROD 서버의 권한·모델 가용성·포털 게시, 선택 기능의 30/31건 Knowledge·5회 Role 평가,
  독립 검수·서명과 위 세 데모 리허설은 **PROD 현장에서 실행해야 한다**. 로컬 단위·mock 브라우저
  테스트 통과는 이 현장 실행을 완료했다는 의미가 아니다.

계약과 책임 경계는 [MAXIS 아키텍처](MAXIS_DIFY_MIGRATION.md), 재사용 배포 소스는
[배포 안내](../deploy/maxis-dify/README.md)를 따른다. 실행별 원본·평가 결과·영수증·스크린샷은
ignored cache에 보관하고 이 가이드에 누적하지 않는다.

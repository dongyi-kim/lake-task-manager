# LTM MAXIS / Dify 적용 아키텍처와 운영 가이드

MAXIS/Dify는 정책을 검색하고 의미 판단·초안을 제안한다. LTM은 최신 업무 사실,
접근 권한, 근거 검증, 사용자 승인과 실제 쓰기를 통제한다.
이 문서는 현행 설계와 사용 경계만 설명하며 배포 이력·평가 결과·화면 캡처를 누적하지 않는다.

## 책임 분리

| 영역 | 담당 | 경계 |
|---|---|---|
| MAXIS | 사내 Project·접근 정책·Knowledge 운영 | LTM의 업무 권한과 사용자 승인을 대신하지 않음 |
| Dify | Role별 의미 판단, 정책 RAG, 구조화 초안, Workflow 내부 검증 | Jira credential·승인 token·직접 업무 쓰기 권한을 보유하지 않음 |
| LTM | 조회·계산·EvidencePack·orchestration·검증·HITL·감사 | 원격 결과를 검증 없이 실행하지 않음 |

현재 Role adapter는 Dify Service API를 호출한다. MAXIS Agent/MCP 등록과 Dify Workflow
호출은 별개 기능이며, Dify 연결만으로 MAXIS Agent/MCP를 사용한다고 표현하지 않는다.
로컬 앱은 서버로 outbound 요청하므로 이 경로에 public 수신 주소가 필요하지 않다.

### MAXIS Node와 Dify 노드는 다른 구성요소

MAXIS의 Node는 Project에 속한 코드·배포 endpoint를 재사용하는 기능이다. Dify 캔버스의
LLM·Code·Knowledge Retrieval 노드와 같은 뜻이 아니다. MAXIS의 Creator는 Node와 Knowledge를
선택해 Dify 흐름을 준비하는 입구이며, 게시된 앱의 실제 실행·모델·로그는 Dify에서 확인한다.
모델이 포털이나 제공자 목록에 등록됐다는 사실만으로 해당 게시 앱이 그 모델을 사용하는 것은 아니다.

- 현재 제품 경로는 **LTM → Dify Service API → 게시 Chatflow → 모델/정책 검색 → LTM 검증**이다.
  MAXIS 포털의 Knowledge가 Dify dataset과 자동 동기화된다고 가정하지 않는다. 실제 dataset·문서 ID,
  내용 hash와 검색 결과로 결속을 확인한다.
- Chatflow를 사용하지만 호출은 stateless다. 대화의 정본은 LTM checkpoint 하나이며 원래 요청·대상·
  확정 답변·초안·근거를 요청마다 명시한다. Dify memory와 별도로 대화 상태를 쌓지 않는다.
- 정해진 단계는 Workflow/Code로 표현한다. Dify Agent는 실행 중 도구 선택이 필요한 조사에 한해
  검토한다. 플러그인 설치나 PoC 앱 등록을 제품 사용 증거로 간주하지 않는다.
- MAXIS Node/MCP 적용 후보는 제한된 읽기 전용 조회다. 서버의 업무망 접근과 사용자별 권한 전달,
  timeout·호출 예산·감사로그를 입증하기 전에는 로컬 Query Runner를 대체하지 않는다.
  기능 사용 개수를 늘리려고 로컬 앱을 공개하거나 Jira 쓰기 권한을 서버에 넘기지 않는다.

## 실행 흐름

1. LTM이 현재 사용자 권한과 검색 범위에 따라 업무 자료를 조회한다.
2. ContextGuard가 최소 근거, 조회 시각, source identity와 payload schema를 검사하고
   민감정보·UTF-8 예산·불완전 자료 여부를 반영한 EvidencePack을 구성한다.
3. 선택된 환경의 Dify Workflow가 릴리스 증명과 Role별 정책 검색 결과를 검사한 뒤
   의미 결과를 작성한다. 정책 자료는 업무 사실의 인용 근거가 아니다.
4. 원격 Contract Guard와 로컬 ResponseGuard가 계약·출처·freshness·릴리스 결속을 검증한다.
   차단 결과는 차단으로 남기며 정상 초안이나 업무 완료로 처리하지 않는다.
5. 쓰기가 필요한 결과는 기존 승인 경로에서 exact payload에 대한 사용자 승인을 받는다.
   Action Executor만 승인 범위 안에서 쓰기를 실행한다.

Role과 Dify 앱의 정본은 [Workflow registry](../config/maxis-workflows.yml)다.
Agent Core는 요청·조회 계획·지식·작업 초안·감사·최종 종합 역할을 담당하며,
Editor Author와 PMO Brief는 별도 앱으로 묶는다. 실제 role roster는
[role manifest](../app/agent/workflow/role_manifest.py)를 따른다.
Query Runner와 Action Executor는 로컬 서비스 경계에 남긴다.

### 작은 단위 계약

LLM에는 의미 판단을, Code에는 입력 투영·출처 선택·형식 조립을 맡긴다. 한 단계의 복잡성을
줄이기 위해 새 semantic Role이나 연쇄 LLM 호출을 늘리는 것은 기본 해법이 아니다.

- Work CREATE의 `work-draft.v2`는 제안 문안(`value/kind/source_ids`)과 원문 사실 선택
  (`kind=fact/source_id/source_path`)을 분리한다. 조립기는 원본 record의 필드·값을 보존한
  인용을 만들며 사실을 재서술하거나 recommendation으로 바꿔 통과시키지 않는다.
- Knowledge의 `fact_paths`는 주체가 포함된 기록, `string_fact_paths`는 티켓 키·용어 등
  문자열 선택 안내다. 두 목록은 힌트일 뿐 권한이나 사실 증명이 아니며 최종 검사는 원본으로 수행한다.
- 조회 0건·부분 조회·오래된 자료·인증 장애·형식 오류를 구분한다. 실패를 0건이나 완료로 감추지 않는다.
- 의미 품질과 기계적 안정성은 별도 측정한다. 같은 모델·합성 자료·배터리를 고정하고 실패 attempt를
  보존한다. 이 구조만으로 품질 개선이나 전체 배터리 통과를 주장하지 않는다.

## Knowledge와 근거

- [Knowledge manifest](../config/maxis-knowledge.yml)는 문서 소유자, 등급, 허용 Role,
  revision과 릴리스 정책의 정본이다. 정책·용어·설계 자료를 업무 사실과 구분한다.
- 정책 검색에는 Role allow-list와 Knowledge Guard를 적용한다. 문서·권한·Workflow가 바뀌면
  릴리스 결속과 기존 검증의 유효성을 다시 확인한다.
- EvidencePack의 사실 인용은 LTM이 검증한 source에만 연결한다. 오래되거나 잘린 자료로
  완전한 업무 초안을 만들지 않는다.
- `ltm:user-intent:` 출처는 사용자의 요청·선호를 보존하는 용도다. 외부 시스템의 사실로
  승격하지 않는다. Work의 `source_content_index`는 같은 pack의 source content를 가리키며,
  원본 plan이나 조회 근거를 대체하지 않는다.
- 임의 내부 문서 업로드는 허용하지 않는다. 사용자 ACL, 자료 철회·삭제, 보존 정책과
  불변 릴리스의 실제 서버 집행이 확인되어야 내부 자료 경로를 활성화할 수 있다.

## 환경 전환과 운영

주소·키·게시 Workflow·Knowledge binding을 코드에 직접 고정하지 않고 profile과 환경
설정으로 공급한다. DEV에서 PROD로 옮길 때도 같은 adapter와 검증 경로를 사용한다.

| 설정 | 원칙 |
|---|---|
| `dev` | 고정 합성 자료 전용. 실제 업무 snapshot을 전송하지 않음 |
| `internal-pilot` | 별도 시범 자산·HTTPS allow-list·서버 통제 검증이 필요한 후보 |
| `prod` | DEV와 독립된 앱·dataset·sentinel·key 및 운영 승인 필요 |
| 기능 route | `local`, `shadow`, `maxis`를 기능별로 선택. 기본 경로는 local |
| 비밀값 | 승인된 credential 저장소·환경 변수 사용. Git·문서·로그에 원문을 남기지 않음 |

Profile 저장이나 연결 확인만으로 활성화하지 않는다. 릴리스·권한·보존 통제와 품질 승인,
사용자 수용 조건을 만족한 뒤 명시적으로 전환한다. 환경·릴리스 변경이나 검증 만료는
기존 증적을 무효화하며, 설정을 완화해 우회하지 않는다.

생성·검증·배포 절차와 현재 단일 자산은 [배포 가이드](../deploy/maxis-dify/README.md)를 따른다.
실행 코드와 계약은 [Agent 패키지 안내](../app/agent/README.md)에서 확인한다.

## 미완성 경계와 후속 연결

TaskFlow는 로컬 계약·조정기·Workflow transport 및 내부 session preview를 제공한다.
Preview의 조회는 기존 Query Runner와 ContextGuard를 사용하지만, 일반 사용자용 HTTP/UI
진입점과 승인 채택 경로가 완성된 기능은 아니다. 기본 runtime composition을 비워 둔다.

활성화 전에 다음을 별도로 연결해야 한다.

- 서버의 사용자 ACL·자료 철회·보존·불변 릴리스와 중첩 호출 예산에 대한 신뢰된 검증기
- Native Research/부모 Workflow의 실제 실행 경계와 제한된 read-only 도구
- 사용자 UI, 초안의 기존 승인 경로 채택, 취소·만료·idle cleanup
- 동일 평가 계약에 따른 업무 품질·지연·오류·수정 비용 검증과 운영 승인

로컬 timeout은 이미 시작한 서버 실행을 강제 종료하거나 서버 총예산을 집행하는 장치가 아니다.
MCP는 필수 구성요소가 아니며 향후 도입하더라도 인증·사용자 identity·host allow-list를 갖춘
read-only 범위부터 검토한다. Jira write credential과 승인 권한은 LTM에 남긴다.

## 문서·검증 산출물 관리

저장소에는 최신 코드·계약·재사용 가능한 평가 harness와 test만 유지한다.
버전별 대체 구현, 실행별 평가 결과, raw 응답, 배포 관측과 캡처는 ignored `.cache/`에
보존하고 PR 파일 목록에 넣지 않는다. 실패·재시도·측정 식별자를 지우거나 과거 raw를
다시 쓰지 않으며, 필요한 결과 요약은 PR 본문과 Changelog에서 관리한다.

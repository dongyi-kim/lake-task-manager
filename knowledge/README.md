# knowledge — 에이전트의 정적 지식 (RAG 1계층)

이 폴더는 공개 저장소에 포함할 수 있는 기본 정적 코퍼스다. 로컬 FAISS 색인과
사내 MAXIS/Dify의 게시된 Knowledge는 별개이며 파일 저장만으로 원격 지식이 갱신되지 않는다.
사내 프로젝트 원문·기술 구성·용어집은 공개 Git에 넣지 않는다.

## 사내 Knowledge 릴리스

- 사내 최신 원본·source manifest·환경 바인딩은 ignored `.cache/maxis/`에 보관한다.
- 환경별 MAXIS 프로필의 `knowledge_source_manifest`와 `knowledge_environment_binding`을
  함께 설정한다. 두 값은 저장소 내부 `config/` 또는 `.cache/maxis/` 아래 YAML 경로만 허용한다.
- 미설정 프로필은 기존 기본 코퍼스를 그대로 사용한다. 사내 원본을 설정하면 기존 문서 검토·Role ACL·
  최신성·content/segment hash 검증을 동일하게 적용한다. 누락·변조는 기본 지식으로 폴백하지 않고 차단한다.
- 환경 YAML의 `knowledge.source_manifest`는 해당 릴리스 원본을 지정한다. 문서 수를 8개로 고정하지
  않지만 원본·배포 문서 집합, 실제 UUID와 Role allowlist는 정확히 일치해야 한다.
- 환경 파일의 내용 해시는 프로필·probe·평가 증명에 묶인다. 파일 누락·다른 원본 선택·UUID 변경 시
  이전 검증을 재사용할 수 없다. 사내 PROD 바인딩은 `knowledge.dev_environment_binding`으로 비교할
  사내 DEV 바인딩을 지정하며, 기본 DEV와 사내 DEV의 문서·dataset UUID를 모두 재사용하지 않아야 한다.
- 환경별 정본 Knowledge의 이름과 논리적 문서 식별자는 안정적으로 유지한다. 같은 목적의 지식을
  날짜·버전별 Knowledge로 추가하지 않는다. 갱신 이력과 복구 원본은 ignored `.cache/`에 보관한다.
- 정본 문서를 갱신·색인하고 원문/청크·권한 검증을 마친 뒤 Workflow를 materialize/import/publish한다.
  실제 게시 UUID와 profile pin, 릴리스 표식·검색·답변을 검증해야 서버 반영으로 보고한다.
- 갱신 전 원본·연결을 백업하고 변경된 검토 해시와 환경 바인딩으로 다시 검증한다. 중복 지식의
  영구 삭제는 명시적 승인 후 수행한다. DEV 적용과 PROD 전환은 별도이며 기본 local route를
  바꾸지 않는다. 평가 결과·원문·배포 영수증은 `.cache/`에만 보관한다.

## 왜 프롬프트가 아니라 RAG 인가

이 규칙들을 시스템 프롬프트에 통째로 넣으면 매 호출마다 수천 토큰을 낸다. 그런데 실제로
필요한 건 매번 한두 절이다 — 담당자를 고를 때 진척률 산식은 필요 없다. 검색해서 **그때
필요한 절만** 가져오는 게 싸고, 규칙이 늘어나도 프롬프트가 부풀지 않는다.

## 무엇을 여기 두고 무엇을 두지 않나

| 둔다 | 두지 않는다 |
|---|---|
| 코드에 없는 **운영 제약** (SP 는 Story 에만) | 코드가 이미 강제하는 것 → `domain/bulk.py` 가 막는다 |
| **판단 기준** (언제 쪼개나, 무엇을 되묻나) | 티켓 데이터 자체 → 동적 RAG 가 실시간으로 가져온다 |
| **왜 그런가** (Bug 가 분모에서 빠지는 이유) | 자주 바뀌는 값(사람 목록·Epic 키) → `config/*.yaml` 이 원본 |

산식·상수를 여기 적을 때는 **코드가 원본이고 여기가 사본**이다. 갈라지면 코드가 맞다.

## 문서

| 파일 | 내용 |
|---|---|
| `01-ticket-rules.md` | 티켓 유형·SP 제약·Epic Link·Component·Label·제목/본문 규칙 |
| `02-progress-formula.md` | 완료 판정(`statusCategory`)·SP 가중 산식·분모 제외·WBS 롤업 |
| `03-modules-and-people.md` | 6개 모듈·사번 규칙(x=개발/i=운영)·**담당자 추천 4신호와 근거 형식** |
| `04-work-breakdown.md` | 한 문장 요구 → 티켓 트리까지의 절차, 좋은/나쁜 분해 예 |

## 색인

첫 사용 시 자동으로 만들어지고, **파일이 바뀌면(mtime+해시) 자동으로 다시 만든다** —
따로 빌드 명령을 기억할 필요가 없다. 색인은 `.cache/agent_index/static/` 에 저장된다.

미리 만들어 두려면(첫 질의를 빠르게):

```bash
python -m app.agent.retrieval.static_index
```

문서를 고치면 **다음 질의에서 자동 반영**된다.

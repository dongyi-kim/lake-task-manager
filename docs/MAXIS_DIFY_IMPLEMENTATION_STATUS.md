# MAXIS / Dify 적용 구현 체크포인트

> 중단 시점: 2026-09-04
> 상태: **일시중지 — 재개 전 아래 미완료 항목부터 처리**

## 작업 위치

- 앱 worktree: `C:\Users\PC\repo\dongyi-kim\lake-task-manager-deploy\.cache\worktrees\maxis-dify-migration`
- 브랜치: `codex/maxis-dify-migration`
- 기준 커밋: `7799e39 release: add v2026.09.03 notes`
- 원래 checkout의 변경 사항은 건드리지 않았다.
- 중단 시점 변경은 알려진 실패 상태를 포함한 **checkpoint commit**으로 보존한다. 원격 저장소에는 push하지 않는다.

## 구현된 범위

### 1. dev / prod 전환 구조

- MAXIS 접속 프로필, Role별 실행 경로(`local` / `shadow` / `maxis`), Dify 앱·워크플로 버전 설정을 분리했다.
- 자격증명은 설정 본문과 분리하고 공개 상태 응답에서 비밀값을 제거했다.
- prod 활성화 전 연결·계약·지식 검색 sentinel 검증을 통과해야 하는 fail-closed 구조를 추가했다.
- dev 프로필은 고정된 합성 데이터만 외부로 보내며 authoritative MAXIS 실행을 막았다.

### 2. LTM Agent 역할 이관

- Editor Author, People Advisor, Auditor, PMO Brief를 MAXIS/Dify Role로 라우팅할 수 있게 했다.
- LTM에는 Jira 조회·계산·검증·승인·쓰기 등 결정적 기능을 유지했다.
- Dify에는 의미 기반 판단과 초안 생성을 맡기되, 정확히 6개의 Start 변수와 구조화 응답 계약을 강제했다.
- `needs_input` 응답, Role별 telemetry, workflow/knowledge version pin, correlation ID를 추가했다.
- 병렬 People Advisor/Auditor 결과는 Role별 sidecar로 분리한 뒤 join에서 결정적으로 투영하도록 변경했다.

### 3. MCP / 보안 / 운영 통제

- Streamable HTTP MCP 서버와 Bearer 인증, Host 검사, 사용자 identity 일치 검사를 추가했다.
- MCP에는 읽기 도구만 노출하고 Jira 쓰기는 기존 LTM 승인 경계 뒤에 유지했다.
- 승인 이후 runtime profile/route가 바뀌면 토큰을 폐기하며, 변경 제안·실행 감사로그를 추가했다.
- 외부 전달 Context Pack의 허용 필드·크기·출처·freshness를 제한하고 응답 schema 및 evidence grounding을 검증한다.
- shadow 평가는 원문/개인 식별자를 저장하지 않고 Role별 승격 지표를 집계한다.

### 4. 심사 피드백 대응 기능

- AI 사용 시간·처리량·오류·승인 결과를 기록하는 impact 측정과 설정 화면 요약을 추가했다.
- Jira Dashboard/수동 처리 baseline은 자동 수집값처럼 표시하지 않도록 명확히 구분했다.
- PMO snapshot 간 변화량을 프로젝트별로 저장·표시한다.
- 지식 manifest, dataset/version, release sentinel 검증으로 판단 기준의 재사용성과 최신성을 확인한다.
- 증거 링크는 검증된 단일 Jira 키에 대해서만 로컬 `/browse/{KEY}` 링크로 만든다.

### 5. 문서 및 Dify 배포 자산

- 설계·도입 검토 보고서: `docs/MAXIS_DIFY_MIGRATION.md`
- MAXIS/Dify 구성 화면 캡처 4개: `docs/images/`
- 지식 manifest: `config/maxis-knowledge.yml`
- Role/workflow registry: `config/maxis-workflows.yml`
- Dify 구성 bundle, prompt, contract, smoke case, validator: `deploy/maxis-dify/`

## 확인된 테스트 결과

아래 결과는 중단 전 실제 실행 결과다.

| 범위 | 결과 |
|---|---:|
| Dify client/bundle, 설정 route, profile, UI 계약 최신 묶음 | 90 passed |
| sentinel/runtime/profile/bundle 관련 묶음(앞선 시점) | 117 passed |
| 승인 runtime pin 및 audit 실패 경계 | 3 passed |
| PMO delta 실제 route | 1 passed |
| MCP stdio + 실제 ASGI/Streamable HTTP | 42 passed |
| 실제 compiled production graph + mock HTTP transport | 2개 신규 시나리오 포함, 관련 묶음 90 passed |
| Description/Comment editor 브라우저 회귀 | 7 / 7 passed |
| Dify bundle validator | 3 workflows, 6 Start variables 통과 |
| Python compileall | 통과 |
| `git diff --check` | 통과 |

## 미완료 / 알려진 문제

1. **병렬 `needs_input` 신규 회귀 테스트 1건 실패**
   - 테스트: `tests/agent/core/test_agent_maxis_role_runtime.py::test_remote_fanout_needs_input_joins_questions_and_routes_to_response`
   - 관찰: `INVALID_CONCURRENT_GRAPH_UPDATE`를 피하는 Role별 질문 reducer/merge 자체는 반영됐으나, 테스트의 종착 노드가 `AgentState`에 없는 `answer` 필드를 반환해 결과에서 사라진 정황이 있다.
   - 아직 테스트 문제인지 제품 route 문제인지 최종 판정하지 않았다. 재개 시 가장 먼저 고치고 동일 테스트를 다시 실행한다.
2. 최신 병렬 질문 변경 이후 전체 MAXIS/Agent 테스트 묶음과 전체 pytest를 실행하지 않았다.
3. MAXIS 설정 화면의 브라우저 수동 QA는 서버를 띄운 직후 중단 요청을 받아 실행하지 않았다. 로컬 서버는 종료했다.
4. 실제 사내 MAXIS/Dify dev endpoint에는 이번 코드 상태로 재연동하지 않았다. prod 앱 생성·게시와 API key 입력도 남아 있다.
5. 변경 파일 최종 리뷰, 비밀정보 스캔, 테스트 임시 디렉터리 정리와 완료 상태의 정식 commit은 아직 하지 않았다. 현재 commit은 재개용 checkpoint일 뿐이다.

## 재개 순서

1. 위 병렬 `needs_input` 테스트의 종착 상태를 스키마 내 필드로 바꿔 route를 검증하고, 실제 제품 문제라면 join/route를 수정한다.
2. MAXIS/Agent/MCP/PMO 관련 전체 묶음을 실행한다.
3. 전체 `pytest`와 Dify bundle validator를 실행한다.
4. `JIRA_ENV=mock`으로 `127.0.0.1:4457` 서버를 띄워 MAXIS 설정 UI, dev 차단, 상태 표시, 콘솔 오류를 브라우저에서 확인한다.
5. diff·비밀정보·생성물 정리 후 완료 commit을 추가한다. push는 별도 요청 전까지 하지 않는다.

## 재개용 핵심 명령

```powershell
Set-Location 'C:\Users\PC\repo\dongyi-kim\lake-task-manager-deploy\.cache\worktrees\maxis-dify-migration'
git status --short --branch
& 'C:\Users\PC\repo\dongyi-kim\lake-task-manager-deploy\.venv\Scripts\python.exe' -m pytest `
  tests/agent/core/test_agent_maxis_role_runtime.py::test_remote_fanout_needs_input_joins_questions_and_routes_to_response -q `
  --basetemp=.cache/test-tmp/resume-fanout-check
```

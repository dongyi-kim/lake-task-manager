# Codex + LTM MCP 조회 품질 평가

실행 중인 앱과 같은 `LocalMcpRuntime`·JiraClient·인증 HTTP 전송을 사용하되 사례마다 별도의 전체 mock
World와 SQLite를 구성한다. 운영 Jira와 내장 LTM LLM은 호출하지 않는다. 모델은 설치된 Codex CLI의
기존 ChatGPT 로그인을 사용한다. 비밀 파일을 읽거나 복사하지 않으며 MCP bearer만 자식 환경변수로 전달한다.

```powershell
$env:LTM_CODEX_EXECUTABLE = 'C:/Users/PC/AppData/Local/Programs/OpenAI/Codex/bin/codex.exe'
python -X utf8 tools/agent_eval_launcher.py codex-mcp --run-group 20261104-codex-mcp-r01 --date 2026-11-04
```

수동 실행은 network-enabled process에서 한다. 기본 모델 `gpt-6-astra`, reasoning effort `ultra`.
launcher가 argument와 기존 로그인을 확인하고 고유 attempt를 예약한 뒤 짧은 실제 Codex 응답으로
연결을 확인한다. preflight 비용·토큰은 별도 기록하며 사례 시간에는 합산하지 않는다.
포트는 4457 고정이다. 점유 중이면 다른 포트를 선택하지 않고 실패한다. 단위 테스트만 명시적으로 port 0을 쓴다.
직접 runner 실행은 거부한다. 재실행은 새 `--run-group`이나 `--repeat-index`를 사용하며 기존 claim/raw를 지우지 않는다.

`--cases MCP-J1 MCP-O1`처럼 고유 ID로 focused 실행할 수 있다. focused 성공으로 이전 full run 실패를 대체하지 않는다.
버전은 external suite `1.0.1`, protocol `3.0.0`, human rubric `2.0.0`이며 기존 내장 Agent 배터리와
동일 benchmark라고 주장하지 않는다. S3~S6 원문 질문과 기대 사실을 재사용하고 별도의 MCP 전송·출력 계약을 기록한다.

| 사례 | 검증 |
|---|---|
| S3-이력 | 8개 관련 티켓, 날짜순 변화, 30분/4시간 정합성 합의, 진행 상태 |
| S4-사람 | 실제 이다은 식별자, 전체 미완료 할당 key 집합·건수; 완료/다른 사람 제외 |
| S5-내일 | 현재 사용자의 미완료 업무 중 우선 추천과 선택적 대안, priority·기한·상태 판단 근거 |
| S6-진척 | 실제 Jira 2/3 완료, 남은 DL-9095, 댓글/문서의 완료 주장과 충돌 |
| MCP-U1 | Jira/Confluence 통합검색, 관련 티켓 본문·댓글과 문서 전체 offset 읽기 |
| MCP-J1 | exact JQL, 페이지당 3건, 같은 snapshot으로 끝까지, 8개 정렬 결과 |
| MCP-O1 | 두 동치 OR 쿼리 각각 조회, 중복 제거·순서·전체 건수 비교 |
| MCP-P1 | 자식의 full/light 권한 실패와 없는 티켓, 부분 결과/미확인 공개 |

자동 gate는 전체 사례 통과, 외부 도구·쓰기·world mutation 0건이다. 그 뒤 사람이 raw를 직접 읽어
기존 5축 rubric과 case-specific checklist를 채점한다. 목표는 평균 4.5/5 이상, 중대한 허위 사실 및
무단 쓰기 0건이다. 자동 판정을 정성 점수로 변환하지 않으며 한 번의 실행은 exploratory 증거다.

Codex의 cwd는 빈 실행별 폴더이고 프로젝트 문서/host skills/shell/apps/plugins/browser/computer/image/
subagents/web를 끈다. MCP 서버에도 읽기 도구만 등록하고 provider 쓰기를 차단한다.
답안·oracle·mock source는 Codex cwd에 놓지 않는다. 일반 출력 schema에는 사실 값 없이
`reply`, 주 결과 `resultKeys`·`reportedTotal`, nullable `progress`, 실제 `sources`만 정의한다.
원문 답변은 최종 agent message이며 중간 commentary와 private reasoning을 섞지 않는다.
private reasoning은 저장하지 않는다. timeout도 이미 받은 안전한 event와 측정 가능한 usage를 보존한다.

raw·manifest·출처 snapshot·MCP 요청/응답·SDK 오류·캐시 출처·upstream 수/시간·세계 fingerprint는
`.cache/agent-evaluation/<runGroup>/`에 저장한다. model request 수·USD 비용·TTFT는 CLI가 제공하지 않아
`null`/unavailable이며 tool 호출 수로 대체하지 않는다. latency는 fixture 초기화를 제외한 Codex process
기동+모델+MCP 시간이다. token usage는 CLI가 반환한 값만 기록한다.

직접 검수 결과는 `research/agent-improvement/evaluations/`에 Markdown으로 남긴다. 모델·commit·manifest·
fixture 날짜·평가자·비교 한계·모든 checklist 결과·대표 출력·실패를 포함해야 평가 완료다.

`1.0.1`은 S5 자동 검사의 한 항목 제한을 수정한다. 원래 질문은 우선 업무와 대안을 함께 제시하는
답변을 금지하지 않는다. 비어 있지 않은 고유한 본인 미완료 티켓 목록과 답변 내 언급은 계속
검사하고 우선순위 판단은 사람이 평가한다. 원문 질문·출력 schema·기대 사실은 바꾸지 않았다.
`1.0.0` 실행과 실패 판정은 그대로 보존하며 새로운 전체 실행으로 검증한다.

첫 전체 실행에서 발견한 jira820 0.12의 미지원 `parent` 조건은 mock 전용 adapter가 기존 AST와
실제 parent 관계로 평가한다. 설정된 읽기 거부는 개별 조회와 검색에 공통 적용하고 pagination
전에 제외해 total/offset을 유지한다. 모델 실행 전 별도 fixture에서 parent 검색·부분 권한·전체
pagination을 검증한다. 이 preflight는 사례 캐시를 데우지 않는다. mock 코드 변경은 data manifest,
fixture 변경은 harness hash에 포함되므로 이전 실행과 성능 향상률을 직접 비교하지 않는다.

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

보고 업무 배터리는 `--battery reporting --date YYYY-MM-DD`로 선택한다. 날짜별 일간·주간·월간과
개인·Component 모듈·Epic·Task 범위 12개, 하위 이력·신규 적재 테이블·권한 제한 3개를 포함한다.
reporting `1.1.0`은 날짜 이동 시 요구하는 취소 댓글과 하위 활동 근거도 해당 기간으로 계산한다.
포함 테이블은 기간 내 실제 기여 티켓 중 하나 이상을 결과에 인용하고, 그 티켓의 성공한 MCP 응답에서
테이블 식별자를 확인해야 한다. 다른 티켓의 언급이나 실패 응답·요청 인수는 근거로 인정하지 않는다.
같은 테이블에 여러 기여 티켓이 있으면 그중 한 티켓의 근거로 충분하다.
이력은 해당 기간의 상태·이름 변경 사건의 티켓·날짜·전후값을 실제 응답과 비교한다. 전체 본문·댓글을
충분히 읽었는지, 제외 사유와 업무 의미를 올바르게 해석했는지는 여전히 사람이 raw를 직접 검수한다.

이 변경은 oracle·checker 계약의 보강이며 질문·출력 schema·mock 업무 데이터는 유지한다.
`1.0.0` 전체 실측 결과와 manifest는 그대로 보존한다. 새 버전의 단위 회귀 성공으로 기존 실측 점수를
대체하거나 재채점하지 않으며, 새 비교 점수에는 별도 run group의 전체 실행이 필요하다.

사람 지칭 배터리는 `--battery identities`로 선택한다(`codex-mcp-identity` 1.0.1).
정식 이름+회사, 성을 뺀 이름, 붙여 쓴 호칭, 회사 단서, 이름/성명 동명이인,
복성, 없는 사람, 티켓 담당자 문맥의 9개 질문을 별도 Codex 세션에서 평가한다.
평가 대상 합성 사용자 7명 중 4명은 실제 username에도 한글·공백·회사명을 포함하고, 나머지는
표시 이름과 다른 ID를 쓴다. mock에는 사용자/티켓 자료만 넣고 별칭 검색 구현은 추가하지 않는다.
신원 확인과 모호성 처리 원칙을 공통 system context로 제공하지만 정답·후보는 주지 않는다.
도구 응답에서 확인한 ID, 그 사람의 전체 미완료 일반 Task 및 건수는 자동 검사한다.
동명이인/미확인에서는 임의 집계 없이 확인 질문을 해야 하며, 질문의 적절성과 호칭 해석은
직접 raw 검수로 판정한다. 이 한 번의 합성 사례 실행은 운영 사용자 전체의 정확도를 보장하지 않는다.

1.0.1 fixture는 별도 합성 로그인 계정을 추가하고, Jira DL scope·빈 Confluence scope·
합성 TEST 인력 설정·빈 WBS를 고정한다. 호스트의 사용자/모듈 설정은 평가 모델에 전달하지 않는다.
기존 조회·보고 배터리의 scope 요구와 동작은 유지한다. 질문과 7명의 업무 정답은 같지만
fixture/검사기 manifest가 달라지므로 이전 실행과 성능·점수의 개선률을 계산하지 않는다.

identity `1.0.1`은 자동 근거 검사를 보강한다. 미확인 유나는 사전등록된 `유나`/`유나대리` 검색의
성공한 빈 사용자 응답이 있어야 하며, 티켓 문맥은 같은 응답 레코드의 key와 실제 담당자 ID가
연결돼야 한다. 무관한 인증 성공·다른 티켓의 담당자·실패 응답은 근거가 아니다.
질문·fixture는 그대로이고 `1.0.0` 전체 실행과 점수는 재채점하지 않는다. 새 버전은 별도 전체 9개 실행으로 평가한다.

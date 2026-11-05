# Application commands

- `ticket_models.py`: UI/MCP의 공통 입력 schema. HTTP 응답 또는 MCP SDK에 의존하지 않는다.
- `ticket_commands.py`: 생성·편집·댓글·전이·링크·첨부·삭제의 입력 변환과 권한/metadata 검사.
  Jira 조회/쓰기·캐시 무효화는 주입된 동일 `JiraClient`가 담당한다.
- `ticket_activity.py`: 기존 캐시의 변경·댓글 수정 시각과 로컬 읽음 기록을 합친다.
  Jira나 사용자 인증을 추가 조회하지 않으며 읽음 기록은 검색 기록·캐시 만료와 독립적으로 보존한다.

HTTP routes와 local MCP는 이 계층을 직접 호출한다. 내부 HTTP 요청이나 새 JiraClient를 만들지 않는다.
내장 Agent의 exact-payload 승인 경계는 별도로 유지한다. 기존 create/transition/upload receipt를 재사용하며,
나머지 command도 mutation ID가 주어진 경우 성공 응답을 재사용하고 응답 유실 시 자동 중복 쓰기를 막는다.

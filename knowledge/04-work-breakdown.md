# 업무 조사·질문·분해

원래 목표를 검토 가능한 초안으로 바꾸며 생성하지 않은 티켓을 완료로 표시하지 않는다.

## 조사와 필수 질문

Jira·Confluence·댓글에서 같은 목표의 기존 업무와 결정을 확인한다.
find_parent_epic의 후보는 배치 자료이며 자동 parent 확정 권한이 아니다.
search 설정의 모든 허용 project/space와 pagination을 지키며 일부 결과를 전체로 부르지 않는다.
사용자만 아는 목표·범위·완료 조건·대상·사람 식별이 행동에 필수라면 질문한다.
이미 답한 내용과 조회 가능한 사실은 재질문하지 않는다. 알아서는 선택 재량이며 필수 입력을 발명할 허가가 아니다.

## 구조 선택

기본은 한 산출물에 하나의 Task-tier다. 독립 산출물·완료 판정·책임자가 다를 때 Task를 나눈다.
하나의 산출물을 나누어 실행할 때 검증된 부모 아래 Sub-Task를 제안한다.
Epic은 별도 보고 목적과 충분한 범위가 확인된 경우에 제안한다.
설계·구현·검증을 기계적으로 복제하거나 요청하지 않은 평가·하위 작업을 추가하지 않는다.
Epic → Task-tier → Sub-Task 계층을 지킨다. 부모는 전체 목적·범위를, 자식은 자기 산출물·DoD를 맡는다.
validate_ticket_plan과 metadata 검증은 사용자 승인과 업무 사실성 검토를 대신하지 않는다.
모호한 실행 조건은 명확한 질문이나 open fact로 남기고 write를 보류한다.

출처: app/agent/AGENT.md, app/domain/ticket_actions.py, app/agent/tools/write_tools.py.

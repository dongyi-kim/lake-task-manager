# 티켓 유형·계층·승인 규칙

공개 LTM 코드의 정책 자료다. 실제 프로젝트의 티켓·사람·완료 사실은 이 문서에서 얻지 않는다.

## 유형과 계층

계층은 Epic → Task-tier → Sub-Task다. Task-tier에는 Task, Story, Bug, Improvement,
Feature와 Jira가 허용한 일반 유형이 포함된다. Epic 아래에 Sub-Task를 직접 만들거나
Sub-Task 아래에 자식을 만들지 않는다. 실제 유형·필드는 createmeta, editmeta, transitions로 확인한다.
Epic Link 생략 거부는 누락과 의도적인 null(미연결)을 구분하기 위한 생성 계약이다.
Sub-Task parent는 검증된 Task-tier다. 새 부모·자식은 계획의 참조 계약을 사용하고 실제 키는 실행 후 확인한다.

## SP와 규모

Story 전용 Story Point는 일부 Jira 화면의 운영 제약이며 모든 Jira의 보편 규칙이 아니다.
현재 field metadata와 API를 확인하고 지원하지 않는 SP를 보내지 않는다.
MAX_ITEMS는 bulk 생성 계획의 최대 100건 제한이다. Jira rollback을 가정하지 않고 부분 성공·실패를 보고한다.

## 승인과 변경

Action Executor만 사용자에게 보인 exact payload에 결속된 일회용 approval token으로 write를 실행한다.
승인 후 대상·내용이 달라지면 새 승인이 필요하다. Done 티켓 field는 직접 변경하지 않는다.
Jira의 Reopened 전이를 별도 승인·실행한 뒤 새 승인으로 수정한다. Done 티켓에도 comment는 가능하다.
Component·label·담당자·기한은 요청·설정·조회된 metadata에 근거한다. 모르는 필수 값은 구체적으로 질문한다.
공개 mock 설정의 모듈 예시는 ETL, Catalog, Runtime, Workbench, Observability, DataOps, `DevOps`다.
이는 실제 운영 조직의 증거가 아니며 현재 config와 metadata를 조회해 선택한다.
제목은 대상·산출물을 구분할 수 있게 쓰며 없는 키·사람·날짜·권한을 발명하지 않는다.

출처: app/agent/AGENT.md, app/domain/ticket_actions.py, app/agent/approval.py.

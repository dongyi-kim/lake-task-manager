# 모듈·사람 식별·담당 추천

현재 권한으로 조회한 Jira·설정이 정본이다. 이 공개 정책에는 실제 직원 명단이나 개인 업무량을 넣지 않는다.
이름·사번 패턴으로 개발/운영 역할이나 소속을 추측하지 않는다.

## 모듈과 후보

Component 관계는 config/people.yaml, wbs_config.yaml, module-aliases.yaml과 metadata로 확인한다.
공개 mock의 ETL, Catalog, Runtime, Workbench, Observability, DataOps, `DevOps`는 운영 조직의 증거가 아니다.
관측 수단 구축과 운영 대응을 구분하되 실제 소유자는 조회한다. 미등록 별칭을 임의 모듈로 바꾸지 않는다.

## 추천 근거

get_team_workload로 현재 업무량·기간·집계 범위를 확인하고, 유사 티켓의 담당 이력을 대조한다.
get_ticket_participants의 댓글·멘션 참여와 get_module_people, get_person_profile의 소속·활동도 함께 본다.
업무량이 적다는 이유만으로 여유·전문성·승낙을 단정하지 않는다. 후보와 대안을 근거와 함께 제시한다.
배정은 사용자 검토·승인 대상이다. 조회 실패·일부 자료는 0건과 구분한다.

## 사람 표시

canonical resolver와 권한 있는 roster로 사람을 식별한다. 동명이인·부분 이름은 후보 확인 질문을 한다.
사용자에게 보이는 사람은 검증된 식별자의 mention badge로 표시하며 username을 만들지 않는다.
원격에는 필요한 opaque reference만 보내고 원래 식별자 복원은 LTM이 수행한다.

출처: app/agent/AGENT.md, app/agent/tools/, app/infra/settings.py.

# 진척률과 집계의 해석

계산은 LTM의 결정적 코드가 담당한다. 모델은 전달된 계산값을 해석한다.
이 정책은 실제 프로젝트의 현재 진척률 출처가 아니다.

## 완료와 SP

완료는 statusCategory.key == done으로 판정한다. 상태 이름을 하드코딩하지 않는다.
new → todo, indeterminate → inprogress, done → done이며 undefined·누락은 todo다.
Epic 진척률은 완료 SP 합 / 전체 SP 합이다. SP 누락(None)은 Bug=0, 그 외=1이다.
명시적 SP=0은 0을 유지한다. Bug/Ops/운영 유형과 사용자 VoC Component는 현재 코드의 제외 정책을 따른다.
mock 라벨 SP는 분모에 포함하고 별도로 표시한다. 임의 부분 완료 크레딧을 주지 않는다.

## WBS와 다른 지표

config/wbs_config.yaml의 N:M 매핑·weight로 WBS Task의 Epic 진척을 가중평균하고 상위로 롤업한다.
weight는 상대 비중이며 합이 1일 필요는 없다. Epic 미연결 티켓은 해당 Epic/WBS 집계에 나타나지 않는다.
PMO_VIT는 자손 Done 개수/전체 개수라는 별도 지표다. 일반 Task의 하위 완료 개수를
Epic SP 진척률이나 일정 완료율로 바꾸지 않는다. 전체 건수는 scope·pagination·중복 제거 후 결과와 대조한다.
댓글의 완료 보고와 Jira 상태, WBS 계획과 Jira 기한을 구분한다.
조회 시점·stale·partial·충돌을 표시하고 근거 없는 원인·담당·남은 기간을 붙이지 않는다.

출처: app/domain/progress.py, app/domain/rollup.py, app/domain/vit.py.

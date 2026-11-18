# Task · WBS · 현안 · 워크로드 구현 기록

기준: `DESIGN.md`, `feature-preservation.md`, 기존 `7e8e9efc` 동작. 기존 checkout/branch에서 작업. 서버·브라우저 실행, API 호출, Git branch/commit/stash 변경 없음.

## 실제 변경

- `mytasks.css`: 일반 두 줄 카드의 20px 제목/18px 메타와 6px 수직 패딩으로 정상 배율 약 56~58px를 목표로 정리. 높이 상한으로 콘텐츠를 잘라내지 않음. 인라인 자손은 한 줄 유지. 기존 중복 `.mt-card`/`.mt-gcard2`/`.mt-gh` 선언을 통합하고 누적된 진한 그림자·다크 전용 면 색 override 제거.
- Task 부모는 `--panel-2`, 자손 lane은 `--panel-3`, 개별 티켓은 `--panel`로 구분. Epic 소속 레일/타입/우선순위/VoC/나·유관/마감 상속/완료 표현 유지. 유관 카드는 텍스트 전체 투명도를 낮추는 대신 레일 농도와 담당자 강조로 구분. 미읽음은 `--info`의 얇은 ring·기존 표식, 긴급도는 기존 계산과 별도 붉은 면/경계. 초과 마감은 임박 색이 덮지 않게 유지.
- `mytasks-board.css`: 상태 칼럼을 중립 lane으로 통일하고 헤더의 상태 표식은 유지. 좌측 옵션 패널의 별도 라이트 팔레트 제거. 그룹 제목/컨트롤 위계와 focus 정리. 축·그룹·조건·너비/접힘·paging·FAB 회피 패딩 유지.
- `mytasks-find.css`: 기존 spotlight 동작과 모든 카드의 원래 위치 유지. 형광 별도 팔레트 대신 공통 focus 토큰, 30px 컨트롤·8px 프레임·작은 shadow로 정리.
- `wbs.css`: Module/WBS/Epic/Task/Sub-Task 구조·JS 고정 행 높이·SP/가중치/계산·시작/종료·시간 축·lazy load/표시/재시도 그대로. KPI를 하나의 strip으로 정리하고 레이블 pane을 416px로 조정. 다크에서 간트 퍼센트가 읽히도록 토큰 foreground/면 적용, Task 상태 바의 고정 라이트 색을 의미 토큰으로 교체. 범주형 bar 채움 정책 유지.
- `vit.css`, `workload.css`: 기존 표/패널/사람/버킷/분포/활동/위험/상태·기간 옵션 배치와 의미 유지. 헤더·타이포·반경·표면 대비 정리, 미읽음 파랑 구분. 워크로드 로딩의 지속 pulse 제거. 날짜/숫자·기존 실패/복구 컨트롤 유지.
- WBS 막대 제목·퍼센트는 동일한 `--panel` label 위에 `--text`를 사용해 진한 범주형 fill에서도 가독성 확보. Workload Epic 배지는 범주색을 레일·옅은 면으로 보존하고 글자는 `--text`로 통일. `VitView.js`의 상단 활성 집계 chip foreground만 `--on-accent`로 교체.
- Task VoC head는 기존 타입색의 16% tint 면과 `--text`로 표시해 다크 테마의 밝은 타입색 위 흰 글자 대비 문제를 제거. VoC 분류·제목 파싱·상태/필터는 그대로 유지.
- WBS/현안/워크로드의 일반 클래스(`.mini`, `.act`, `.tbl`, `.detail` 등)를 각 view 아래로 scope해 다른 화면과 CSS 충돌 축소.
- `TaskCard.js`는 표시 제목 tooltip, link 카드의 role/tabindex만 추가. Enter/Space는 기존 app-root 전역 키보드 위임 사용. 별도 keydown handler·상태·API 변경 없음. 접근 가능한 이름은 기존 카드 내용 전체에서 생성.

## 검증 및 한계

- 관련 정적 검사 22개 통과: `frontend/static_assets/test_task_ui_contracts.py`, `frontend/wbs/test_wbs_fault_recovery.py`, `frontend/vit/test_vit_fault_recovery.py`, `frontend/static_assets/test_workload_ui_contracts.py`. 실행 scratch/cache는 OS 임시 폴더를 사용하고 finally 정리. `git diff --check` 통과.
- 기존 compact-card 정적 테스트의 radius/shadow/다크 override/정확한 바 높이 결합을 제거하고 width/min-width/box-sizing/자손 grid/fold·담당자 layout 계약 검사는 유지. 기능 assertions와 원래 JS 소유 로직은 유지.
- 기존 media 조건과 좁은 창의 250px WBS 레이블 pane 유지. media 안에 중복된 공통 간격 선언과 소비자가 없는 `wl-pulse` keyframes는 제거. 모바일 신규 설계 없음.
- 실제 DOM의 68px 이하 카드 높이, 1280/1440/1720·라이트/다크·200% 확대·focus·긴 한글·48/24/200 자손은 root 브라우저 검증 대기. 위 수치는 CSS 목표이며 브라우저 실측 통과 주장이 아님.
- 브라우저 회귀 우선순위: Task grouping/compact/filter/find/hydration/update/drag; WBS 네 단계·시간 축·부분 재시도; VIT 자손/댓글/독립 재시도; Workload 사람/버킷·기간/정렬·부분 실패. 기존 테스트 `frontend/mytasks`, `frontend/wbs`, `frontend/vit`, `frontend/static_assets/test_workload_ui_contracts.py`, `jira/workload`, `domain/test_rollup.py`와 함께 확인.
- 자손을 새 단계별로 펼치는 로직은 추가하지 않음. 기존 fold/더 보기/전체 접근과 API를 그대로 유지해 검색/필터 후 slicing 및 비동기 보존 위험을 늘리지 않음. 신규 최근 탭은 별도 작업이며 이 변경에 포함하지 않음.

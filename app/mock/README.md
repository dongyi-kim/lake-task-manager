# app/mock — 결정적 데이터 세계 (dev mock / local)

실 Jira 없이 개발·테스트하기 위한 **결정적 가짜 데이터**와, 그것을 외부 오픈소스 mock
[`jira820`](https://pypi.org/project/jira820) 에 주입하는 브리지. **mock·local 이 같은 world·직렬화기를 공유**해
출력이 100% 일치한다(회귀 기준, `tests/jira/workload/test_local_parity.py`).

## 파일
- **world.py** — ★ 단일 결정적 데이터 세계. 이슈·설명·코멘트·활동·confluence + **UI 회귀 픽스처**(DL-9000 계열).
  랜덤이지만 seed 고정이라 재현 가능. 픽스처 규칙은 AGENTS.md §7 을 반드시 지킬 것.
- **worldcontent.py** — description/comment/activity/confluence 다양성 **콘텐츠 풀**(world 가 소비).
- **confluence_world.py** — Confluence UI 고정 문서: DL/PMO, 긴 본문과 중복 헤딩, 표·코드,
  3단계 계층·55개 형제 문서. `890100000001` 운영 가이드에서 시작한다. 서버 재생성 후에도 ID가 같다.
- **confluence.py / confluence_state.py** — jira820의 공통 Confluence REST·상태를 가져오는 호환 import.
  범용 CRUD·리비전·트리 구현을 앱에 복제하지 않는다. 앱은 주입할 업무 문서만 소유한다.
- **fakebridge.py** — world 를 jira820 서버에 주입(`build_store`/`build_injected_app`).
  mock=in-process(ASGI), local=:8080 실 HTTP — 둘 다 이 브리지를 탄다.
- **permissions.py** — 생성/보고자 권한 fixture와 세션 사용자 Reporter 기본값. `store.permissions`로 권한을 각각 재현한다.

## 규칙
- **prod 배포 config 에 TEST 모듈/픽스처를 넣지 마라**(테스트가 가드). dev 전용.
- world 시퀀스 불변식: 픽스처는 `_fx()`로 직접 생성(rng 미사용). rng 를 쓰면 전체 시퀀스가 바뀐다.
- 사내 워크플로 상태/타입 스킴은 fakebridge `_STATUSES` 가 world 직렬화기와 일치해야 한다.

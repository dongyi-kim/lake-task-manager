# app/mock — 결정적 데이터 세계 (dev mock / local)

실 Jira 없이 개발·테스트하기 위한 **결정적 가짜 데이터**와, 그것을 외부 오픈소스 mock
[`jira820`](https://pypi.org/project/jira820) 에 주입하는 브리지. **mock·local 이 같은 world·직렬화기를 공유**해
출력이 100% 일치한다(회귀 기준, `tests/test_local_parity.py`).

## 파일
- **world.py** — ★ 단일 결정적 데이터 세계. 이슈·설명·코멘트·활동·confluence + **UI 회귀 픽스처**(DL-9000 계열).
  랜덤이지만 seed 고정이라 재현 가능. 픽스처 규칙은 CLAUDE.md §7 을 반드시 지킬 것.
- **worldcontent.py** — description/comment/activity/confluence 다양성 **콘텐츠 풀**(world 가 소비).
- **fakebridge.py** — world 를 jira820 서버에 주입(`build_store`/`build_injected_app`).
  mock=in-process(ASGI), local=:8080 실 HTTP — 둘 다 이 브리지를 탄다.

## 규칙
- **prod 배포 config 에 TEST 모듈/픽스처를 넣지 마라**(테스트가 가드). dev 전용.
- world 시퀀스 불변식: 픽스처는 `_fx()`로 직접 생성(rng 미사용). rng 를 쓰면 전체 시퀀스가 바뀐다.
- 사내 워크플로 상태/타입 스킴은 fakebridge `_STATUSES` 가 world 직렬화기와 일치해야 한다.

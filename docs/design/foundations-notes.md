# 공통 기반 스타일 적용 기록

기준: 루트 `DESIGN.md`, `feature-preservation.md`. 담당 범위는 기존 `tokens.css`, `base.css`, `components.css`, `guide.css`, `agent.css`, `local-mcp.css`, `workspace-admin.css`, `devtools-hierarchy.css`다. 별도 override 파일, JavaScript·API·프롬프트 변경은 없다. `app/agent/AGENT.md`의 승인·근거·실행 경계를 유지한다.

## 적용

- 승인된 네 단계 작업면과 텍스트·보더 팔레트를 적용했다. 명시적 light는 기본 팔레트를 사용하고, 명시적 dark와 OS dark는 같은 값을 사용한다. `color-scheme`도 일치시켜 네이티브 폼의 테마를 맞춘다.
- 기존 토큰 이름을 보존했다. `--info`, `--focus`, `--selected`, `--info-surface`, `--on-accent`, `--control-border`, `--danger-action`, `--on-danger`를 추가했다. 행동은 청록, 진행·링크·미확인 업데이트는 파랑, 완료·경고·오류는 각 의미색을 쓴다.
- 문서 글꼴, 제목·본문·메타데이터 크기와 행간, 4px 간격, 30/26/32px 컨트롤, 5/6/8/10px 모서리, hover 120ms·overlay 180ms 토큰을 제공한다. 소비자용 이름은 `--font-document`, `--size-meta`, `--control-height`, `--radius-control`, `--radius-dialog`, `--shadow-overlay`, `--motion-hover`다.
- 공통 버튼·폼·설정·MCP·workspace·로그인/전이 창의 경계·포커스·그림자를 정리했다. 청록 채움 위 글자는 `--on-accent`, 위험 버튼은 고정된 어두운 빨강과 흰 글자로 분리한다. 공통/Agent 기본 disabled 버튼은 텍스트 대비를 보존하며 행동색을 제거한다.
- Home 카드의 과한 부상과 AI 탭·가이드의 상시 맥박을 제거했다. AI 대화는 중립 작업면과 선택면을 사용하며 진행 상태·참조 링크는 파랑으로 구분한다. 키보드로 대화 삭제 버튼에 접근할 수 있도록 `:focus-within` 가시성을 추가했다. reduced-motion에서는 로딩/진행의 애니메이션을 멈추고 상태 라벨은 유지한다.
- 모듈·Epic 해시 범주색과 차트 채움은 변경하지 않았다. 타입 칩의 작은 글자색은 같은 색 의미를 유지하며 대비를 높였다. 흰 글리프가 들어가는 타입 아이콘은 별도 `--icon-*` 채움 토큰을 사용한다.
- 중복 light 팔레트, 효과 없는 Agent 빈 화면 선언, 중복 전이 시간 필드 폭을 정리했다. 기존 레이아웃·조회·상태·쓰기·승인·복구 훅은 수정하지 않았다.

## 수행한 정적 확인

- sRGB 대비를 네 작업면에서 산술 계산했다. 최저 본문/보조/metadata: light 12.22/6.12/4.59, dark 10.94/7.55/6.48. 기본 의미색 글자는 light 4.87 이상, dark 6.79 이상이다.
- 포커스 최저 light 4.29, dark 6.30. 입력 경계는 light 3.47, dark 3.32 이상이다. 장식용 `--border`·`--border-hi`와 의미 있는 컨트롤 경계를 구분한다.
- primary 채움 대비 light 6.30, dark 8.69. 위험 버튼 6.05. 타입 칩의 14% tint를 세 작업면에 합성한 글자 대비는 light 4.74 이상, dark 5.08 이상이다.
- `git diff --check`를 담당 CSS에 실행했다. 동작 테스트나 실제 렌더 대비를 대신하는 결과는 아니다.

## 남은 검증

루트 에이전트가 기존 mock 서버와 회귀 도구를 일괄 관리한다. 이 담당 작업에서는 서버·브라우저를 시작하지 않았다.

- 실제 앱의 1280/1440/1720px light/dark/OS 테마, 200% 확대에서 문구·버튼 잘림과 오버레이 확인.
- 키보드 focus/복귀, 입력 IME, hover/selected/disabled, retained-data·partial failure·권한 오류, reduced-motion 확인.
- 실제 반투명 칩·선택면·중첩 표면의 최종 computed 색과 대비 확인. 다른 CSS의 `background:var(--accent)` 위 흰 글자 소비자는 각 담당자가 `--on-accent`를 적용해야 한다.
- 관련 static asset·Agent UI·shell·settings/MCP/workspace 회귀는 루트 실행 결과로 기록한다. 전체 기능 회귀 통과를 주장하지 않는다.

# 문서·티켓·검색·일괄 생성 스타일 적용 기록

기준: `DESIGN.md`, `feature-preservation.md`, 앱 baseline `7e8e9efc`. 기존 Vue/ESM 컴포넌트와 CSS 파일 안에서 수정했다. 새 override stylesheet, HTML/JS 변경, API·조회·쓰기·초안·receipt 변경은 없다.

## 변경 파일

| 파일 | 적용한 내용 | 보존한 경계 |
|---|---|---|
| `app/static/styles/ticket.css` | 중성 헤더·대화상자 표면, 공통 반경/그림자, 본문·댓글·리치 편집기 14/23 토큰, 제목/메타 위계, 도구막대·도크, focus, 정보색 unread, 정적 skeleton | 티켓 3열, 패널 폭·접힘·리사이저, 모달/단독/최대화, 스크롤 주체, 편집기·첨부·댓글·권한·충돌·복구 selector |
| `app/static/styles/confluence.css` | 읽기/작성 제목과 14/23 본문, 12/18 메타, 조용한 도구 표면, 문서/목차 위계, 선택과 검색일치의 구분, focus | tree/body/목차, 가변 패널 폭, 좁은 창의 기존 동작, 버전/읽기/분기 오류·재시도, 작성/초안 구조 |
| `app/static/styles/confluence-macros.css` | 매크로 구획·설정·폼·상태 표시를 공통 토큰으로 정리 | 매크로 유형·원문 구조·펼침·코드 줄번호·이미지·목차·편집 동작 |
| `app/static/styles/search.css` | 검색 창·입력·그룹·선택 행·메타 위계, 정적인 업데이트 표시, categorical Epic badge 가독성 | 3소스·scope·최근 목록·stale·부분 오류·키보드 선택 selector |
| `app/static/styles/bulk.css` | 창·안내 패널·표·오류·결과·하단 도구 표면과 서체 정리 | JSON/안내 분할·폭 조절, preview/result, success/failed/pending/notStarted, 선택·진행·복구 selector |
| `app/static/styles/jsoneditor.css` | 13px 단일 글자 격자 변수, 거터 표면, 컨테이너 focus | 강조본/textarea/줄번호의 같은 좌표·행 높이·padding·스크롤 소유권 |
| `app/static/styles/filebadge.css` | 공통 반경·12px 파일명·보조 메타·조용한 표면 | 파일 유형별 mask/color, 첨부/인라인/편집기의 동일 규칙, 파일명 축약·고정 폭·삭제/업로드 상태 |

Categorical Epic badge는 기존 `--ec`를 tint와 내부 경계에 남기고 글자는 `--text`로 표시한다. 밝은 다크 테마 범주색 위에 흰 글자를 얹던 조합을 제거했으며 범주 hue나 키→색 매핑은 바꾸지 않았다.

기존 `bulk.css` baseline에도 있던 선택자 없는 무효 선언 두 조각을 제거했다. 사용하지 않게 된 skeleton/update pulse keyframes도 정리했다. 서로 다른 상태·미디어 조건에서 의미가 있는 기존 cascade는 보존했다.

## 수행한 확인

- 7개 파일의 문자열·주석·중괄호 구조 검사를 통과했다. 이는 완전한 브라우저 CSS 해석 검증을 대체하지 않는다.
- baseline과 대조해 기존 유효 selector를 모두 유지했다. 새 selector는 JSON 편집기 컨테이너의 `.jed:focus-within` 한 개다.
- display/position/inset/width·height/overflow/flex/grid/z-index/resize/pointer-events/transform/white-space/content를 대조했다. 의도한 컨트롤 크기 변경을 제외하면 패널·리사이저·스크롤의 해당 속성은 유지됐다. 의도한 변경은 티켓 상단 28→30px, 버튼 최소높이 30px, 문서 트리 행 최소높이 26→30px, 헤더 검색 최소높이 30px이다.
- JSON의 글자 크기는 `--jed-fs` 한 곳에서 결정하고 강조본·입력·거터의 기존 상속/행 높이를 유지했다.
- 소유한 파일의 `git diff --check`를 통과했다. 저장소에 테스트 산출물이나 실행 프로필을 만들지 않았다.
- 관련 기존 검증 범위는 `frontend/ticket_dialog`, `frontend/editor`, `frontend/shell/test_confluence_*`, `frontend/shell/test_bulk_*` 및 정적 shell 계약에서 확인했다.

## 메인 에이전트가 이어서 확인할 항목

서버와 브라우저 실행은 메인 에이전트가 소유하므로 이 작업에서 실행하지 않았다. 따라서 기능 회귀·시각 가독성·접근성 통과를 주장하지 않는다.

1. 실제 앱 DOM에서 1280/1440/1720px, 라이트·다크, 200% 확대를 비교한다. 긴 한글 제목·표·파일명·도구막대·dock에 가림이나 예기치 않은 overflow가 없는지 확인한다.
2. 티켓 패널 폭/접힘/확대·단독 경로, 본문/내부 코드/댓글 스크롤 경계와 focus 복귀를 확인한다.
3. 리치 설명·댓글의 생성→게시→렌더→수정→재게시→최종 렌더와 표·이미지·멘션·배지·첨부·초안 복구를 기존 회귀로 검증한다.
4. Confluence 읽기/작성·버전 충돌·매크로·파일별 부분 업로드 복구, Bulk JSON 입력/선택/진행/4가지 결과·동일 요청 확인·실패분 재시도를 확인한다.
5. 실제 상태 조합의 텍스트/비텍스트/focus 대비, unread+선택+검색+지연 공존, 요청 수 불변을 확인한다. 정적 selector 검사만으로 동작 보존을 판정하지 않는다.

Confluence 최근 문서 탭은 별도의 신규 인터페이스 상태 기능이며 이 CSS 변경에는 포함하지 않았다. inactive 문서 metadata 복구와 기존 활성 문서 읽기·작성/초안 소유권을 분리하는 설계가 선행되어야 한다.

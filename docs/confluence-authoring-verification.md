# Confluence 작성·수정 왕복 검증 — 2026-11-06

기준: macro editor `9b874057` 위의 `feature/confluence-authoring-navigation`.
Jira/Confluence 공통 mock(jira820 `0216281`)과 실제 Chromium UI를 사용했다.
운영 서버는 변경하지 않았다. 일반 지연은 0ms이며 실패 사례에만 명시적 응답 오류를 주입했다.

| 원본·흐름 | 최초 표시 | 수정 화면 복원 | 재발행·최종 표시 |
|---|---|---|---|
| LTM 신규 작성 → 초안 닫기·복원 → 발행 | 통과 | 통과 | 두 차례 수정·재발행 통과 |
| 외부 작성 형식의 native `body.storage` → 읽기 | 통과 | 통과 | 두 차례 수정·재발행 통과 |
| Jira 본문·댓글의 기존 공용 에디터 | 통과 | 통과 | 양쪽의 두 차례 왕복·툴바·슬래시 회귀 통과 |

## 확인한 내용

- 신규 문서: 제목·부모·헤딩 자동 번호, 굵게/기울임/취소선/인라인 코드, 색/배경색,
  목록·인용·코드·콜아웃·표·링크·사용자 참조. 표 셀과 코드를 실제 수정하고 다시 발행했다.
- 파일 선택·이미지 붙여넣기·드래그로 넣은 이미지 3장과 일반 파일의 초안 복원·첨부 업로드,
  첫 열람·수정 진입·재발행 이후 실제 이미지 decode와 파일 다운로드. 에디터 내부의
  `ProseMirror-separator` 빈 이미지와 실제 첨부 이미지를 구분해 검사한다.
- 외부 형식: 18종 native 매크로, 설정 변경, nested Expand 헤딩, 표 셀 편집,
  링크의 rich label/anchor, 헤딩 ID, 색상, 첨부 이미지, 미지원 macro ID/원문,
  기존 task ID와 새 ID의 비충돌, CDATA 공백·특수문자 보존.
- `/` 메뉴의 18종 매크로 삽입·설정 재열기와 `/hr` 실행.
- 작성 목차: 빈 상태, 입력·이름 변경·단계 변경·헤딩 제거, 링크 헤딩, 실제 wheel 스크롤의
  현재 항목 표시, 클릭 이동·편집 포커스, 접기/너비 조절, 700px 서랍, 초안 복원. 로컬
  목차 조작 때문에 Confluence API가 추가로 호출되지 않는 것도 확인했다.
- 트리 우클릭/Shift+F10, Escape/바깥 클릭으로 닫기, 좁은 화면의 탐색 서랍 안 메뉴,
  클릭한 부모 아래 단 한 번 생성, 기존 일반 초안 보존.
- 생성 응답 유실·일부 첨부 실패 후 이어서 발행, IndexedDB 저장 실패 시 쓰기 차단,
  열려 있는 수정본 및 재접속 초안의 revision 충돌 차단.
- 기존 탐색·즐겨찾기·최근 문서·검색·포함 문서 갱신·트리 페이지네이션 회귀.

## 실행 결과

관련 실제 브라우저 테스트 24개: 최초 묶음에서 23개 통과, 아래 포커스 문제 수정 후
해당 테스트와 좁은 화면 회귀 4개 통과. 수정된 링크 헤딩/외부 원문 왕복도 별도 재검증했다.
동일 사례의 반복 실행은 개수에 중복 합산하지 않는다. 건너뛴 브라우저 사례는 없다.

범위: `tests/frontend/shell/test_confluence_{authoring_navigation,authoring,macros,browser}_ui.py`,
`test_editor_roundtrip_ui.py::test_description_and_comment_all_formats_survive_two_ui_publish_cycles`,
`test_editor_controls_ui.py::test_toolbar_and_all_slash_commands_survive_draft_and_publish_cycles`.
전체 Python suite는 PR의 GitHub Actions에서 별도로 수행한다.

## 발견·수정한 문제

1. `app/static/components/editor/editorOutline.js:35`: 클릭 이동 직후 scroll 이벤트가 선택한
   목차 강조를 덮어쓰던 문제. 실제 스크롤 위치가 달라질 때 스크롤 기준으로 전환한다.
2. `app/static/components/ui/ConfluencePanel.js:11`: 좁은 목차 모달이 닫히기 전에 편집기에
   포커스를 주어 키 입력이 유실되던 문제. 모달 닫기를 먼저 완료하고 편집기를 바로 포커스한다.
3. **리뷰 P2** — `app/static/components/editor/editorOutline.js:2`: 링크/멘션 atom의 표시
   이름이 `textContent`에서 빠져 링크 헤딩이 ‘제목 없음’이 되던 문제. 의미 있는 leaf label을
   포함하고 보이지 않는 anchor는 제외한다. 리뷰어 재검증 완료. 다른 리뷰 지적은 없었다.

## 검증 한계

외부 문서는 Confluence native storage를 직접 넣은 독립 fixture로 검증했다. 운영 9.2.4
Confluence UI/Synchrony에서 사람이 작성·공동편집한 실제 문서의 왕복 검증을 대신하지 않는다.
기존 운영 수정 검증 gate는 유지한다. 미지원 매크로, 기존 native 할 일 목록·사용자/파일
참조는 원문 보존 블록이며 모든 세부 요소를 LTM에서 자유롭게 수정할 수 있다는 뜻은 아니다.
사용자 참조는 현재 렌더러에서 Confluence user key로 표시될 수 있다.

# Task 최근 티켓 탭 통합

공용 `openItemTabs.js` / `OpenItemTabs`를 사용. AppRoot가 열기·닫기·전환과 guard 성공 후 상태 변경을 소유하며 TicketDialog는 본문·쓰기·초안의 기존 소유자 그대로 유지.

제출 준비도 보호 범위에 포함. CommentEditor는 Jira 주소 조회 전 `submitting`과 편집 잠금을 설정하고 전체 제출의 `finally`에서 해제. 준비 중 중복 제출·취소·초안 삭제·티켓/문서 전환 차단. Confluence 발행 기록 정리는 사용자 확인 직후부터 durable cleanup 전체에 `finishingSubmission`을 적용해 닫기와 재실행의 경합 방지.

추가 검증: `test_submission_navigation_guard.py`는 실제 submit/취소/finishRun 메서드를 지연된 Promise로 실행해 Jira 준비, 중복 호출, 취소 flush 경합, 오류·빈 본문·readOnly 복원, 단계별 기록 정리를 검사. 이 파일과 기존 ticket/confluence Node guard·정적 자산 검사는 379개 통과. 브라우저 회귀는 별도 통합 검증 대상.

- Task 상단에는 모달이 닫힌 동안 최근 티켓 strip, 모달 안에는 같은 목록을 slot으로 표시. 활성 모달은 `:key`로 한 개만 마운트. metadata 복원만으로 모달을 열거나 inactive 티켓 본문을 요청하지 않음.
- 생성 후 `lake-open-ticket`, 전역 ticket click/Enter/Space, 검색 선택, strip activate/close, 모달 X/Escape/backdrop 모두 AppRoot의 비동기 전환 경로 사용. 같은 key는 재마운트 없이 유지. 같은 티켓 검색 선택은 검색창을 닫음.
- 모달 닫기는 티켓 탭·최근 이력·초안을 삭제하지 않음. inactive 탭 닫기는 metadata만 제거. active 탭 닫기는 guard 후 공용 저장소의 오른쪽→왼쪽 이웃으로 전환, 마지막이면 보드/기존 trigger로 초점 복귀.
- health의 env/Jira base/project context로 metadata 분리. ID/title/scroll만 보관. 제목은 기존 활성 조회의 결과로 갱신, scroll은 debounce·전환·pagehide에서 기록. scroll 복원은 기존 활성 조회가 내용을 표시할 때만 진행하며 사용자 조작 후 중단.
- 전환 전 설명·신규 댓글·기존 댓글 수정 editor의 `flushDraft()`를 await. 기존 `draft.js`의 HTML/Blob·첨부 receipt·mutation ID·pending HTML을 그대로 사용. 저장 실패·충돌·에디터 revision 변경은 현재 화면에 머무름. pending receipt가 있는데 현재 본문이 비어 있으면 기존 flush의 삭제 경로에 들어가지 않도록 전환 차단.
- AI/에디터/직접 첨부·제목/본문/필드/링크 쓰기와 삭제·체크박스 변경 중 전환 차단. FieldEdit popup/미저장 제목·생성/전이 창은 기존 적용·보관/취소를 마친 뒤 이동하도록 안내. 탭 선택이 자동으로 서버 저장·초안 삭제·취소·제출을 실행하지 않음.
- 기존 댓글 수정과 FieldEdit에 guard 조회용 refs만 추가. 첫 Escape는 열린 FieldEdit/생성·전이 창이 처리. 본문/댓글에서 첫 Escape는 기존처럼 editor focus만 해제. `/browse` 단독 페이지는 기존 경로 유지하며 이번 새 strip은 Task 모달 범위.

## 검증 기록

`tests/frontend/ticket_dialog/test_ticket_leave_guard.py`는 실제 helper를 Node에서 실행해 busy/미저장/중첩 창, editor 3종 보관, 결과 미확정 receipt 보존, 저장 실패/충돌/전환 중 변경을 확인. 실제 브라우저·서버는 root가 관리하며 이 작업에서 시작하지 않음. root 작성 통합 테스트 `test_open_item_tabs_ui.py`는 별도 소유.

실행 결과: 위 Node 회귀 3개와 `frontend/static_assets` 합계 371개 통과, skip 없음. 모든 scratch/cache는 OS TemporaryDirectory에서 생성 후 정리. `git diff --check` 통과. 구버전 Node에서도 실행되도록 원본 독립 helper를 data-URL ES module로 로드하며 임의 런타임 설치나 제품 소스 복사본은 만들지 않음. guard 동안 disabled된 탭의 성공 후 초점은 활성화 DOM 반영 후 복귀하고, 실패·취소 시에는 기존 focus가 남아 있으면 그 요소로 복귀.

최종 통합 결과는 `implementation.md`에 기록. 실제 Vue 탭·키보드·복구·초안 실패와 description/comment rich HTML/Blob 두 번 게시, 생성/전이 초안, 기존 ticket/editor 회귀를 root가 검증했다. 운영 Jira/Confluence 쓰기·실 LLM 실행 검증 아님.

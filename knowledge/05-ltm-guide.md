# LTM 화면·Agent·MAXIS 사용 경계

LTM은 Jira를 업무 사실의 source of truth로 사용하는 로컬 웹앱이다.
화면 사용법은 실제 연결·활성화·품질 통과의 증명이 아니다.

## 화면과 저장

홈(#/home)에서 업무·현안·진척을 보고 티켓의 본문·댓글·계보·첨부·관련 문서를 연다.
담당자·우선순위·마감 등은 티켓 다이얼로그의 값을 클릭해 인라인 편집 팝업에서 바꾼다.
편집 가능 여부는 Jira editmeta의 권한·metadata를 따른다. Agent 입력은 Ctrl+Enter로 전송한다.
근거 번호·ticket badge·사람 mention·문서 link로 실제 출처에 이동한다.
Agent write는 exact payload 승인 카드 검토 후 실행한다.
Editor AI는 HTML 초안을 에디터에 삽입할 뿐이며 사용자가 저장·등록해야 Jira에 반영된다.
seed 사용·전체 교체 옵션을 확인하고 Ctrl+Z로 생성분을 되돌릴 수 있다.
강제 새로고침은 데이터 화면 좌하단의 ↻ 버튼이다. 인증을 확인한 뒤 서버 캐시를 비우고 다시 읽는다.

## 연결과 검증

개인 LLM 설정과 MAXIS/Dify Workflow profile은 별도다. 기능 route는 local/shadow/maxis다.
shadow의 사용자 출력은 로컬 결과이므로 MAXIS authoritative 출력으로 소개하지 않는다.
연결 검사·게시·Knowledge 검색·의미 품질·실제 UI 왕복은 각각 검증한다.
maxis 실패를 개인 LLM 성공으로 조용히 대체하지 않는다. DEV는 고정 합성 자료 전용이다.
실제 자료 전송과 PROD 활성화는 독립 자산·권한·검증을 요구한다.
캐시와 upstream 확인을 구분하고 재시작 후 revision·profile·게시 Workflow/Knowledge pin을 재확인한다.

출처: docs/MAXIS_DIFY_MIGRATION.md, app/agent/routes.py, app/static/components/ui/FieldEdit.js,
app/static/components/ui/FloatingRefresh.js, app/static/components/ui/CommentEditor.js.

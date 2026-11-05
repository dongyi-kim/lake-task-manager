# app/content — 표시 안전 계층 (HTML/wiki 정화·변환·구조화)

Jira 원문(HTML 또는 wiki markup)을 **화면에 안전하게 그릴 수 있는 형태**로 바꾸는 순수 텍스트 처리 계층.
네트워크·인증에 의존하지 않는다(입력=문자열, 출력=문자열/구조).

## 파일
- **htmlsafe.py** — HTML 새니타이저(`sanitize_html`) + task-list 평탄화(`flatten_task_lists`) +
  첨부/이미지 프록시 URL 재작성(`proxy_attachment_*`) + 이모티콘 처리. `_Sanitizer`/`_TaskFlatten`(HTMLParser).
- **wikihtml.py** — Jira wiki markup ↔ HTML **양방향** 변환(`html_to_wiki`/`wiki_to_html`). 자기완결·순수.
- **sections.py** — 렌더된 description HTML 을 `=== 제목 ===` 구분선 기준으로 **섹션 분할** +
  key/value 행 추출(`split_sections`). 소비처는 jira_client 의 티켓 뷰 조립.

## 규칙
- 순수 함수 유지 — 여기서 상류(Jira)를 호출하지 마라. 프록시 URL 은 '문자열 재작성'만 하고,
  실제 미디어 fetch 는 `app/jira` 가 한다.
- description/comment 포맷 규약(prod=JEDITOR HTML, mock/local=wiki)의 **변환 지점**이 여기다.

## JEditor 콜아웃 저장 호환

`callouts.py`는 LTM 6종과 JEditor native 패널의 대응을 관리한다. 읽기는 기존
`callout-*`, HTML 저장은 `serialize_jira_html`의 native class와 고정 inline style을 사용한다.
`success → idea`, `error → warning`은 가장 가까운 native 표현이며 기존 LTM class를 먼저
남겨 종류를 보존한다. 원래 Jira의 점선 패널은 검증된 `data-jira-panel="dashed"`로 보존한다.
위키 저장은 기존 매크로를 유지한다. 전체 문서나 기존 티켓을 일괄 변환하지 않는다.

Native 스타일 근거: [JEditor 3.13.4 (Jira 8 호환)](https://marketplace.atlassian.com/rest/2/addons/com.jiraeditor.jeditor/versions/name/3.13.4?hosting=server)의
`includes/ckeditor2/defaults.js`. 배경/아이콘은 Jira의 `includes/css/issueview.css`가 제공한다.

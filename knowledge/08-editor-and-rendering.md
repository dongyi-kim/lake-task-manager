# Editor HTML·reference·왕복 보존

Editor HTML, Jira 저장형, Agent chat Markdown은 별도 계약이다.
AI Editor는 HTML body만 반환하며 code fence·설명 wrapper·실행 script를 넣지 않는다.

## 구조와 정화

app/content/htmlsafe.py의 allowlist와 _DROP_SUBTREE가 태그·속성을 통제한다.
script/style/iframe, event handler, 위험한 URL scheme은 허용하지 않는다.
paragraph/heading/list/quote/code/table/callout 구조를 보존한다.
table/th/td에는 Jira에서도 보이는 불투명 border를 저장하며 앱 CSS만으로 통과시키지 않는다.
normalizeAiHtml, liftCheckboxes와 serializer가 TipTap node·Jira 표현을 변환한다.
callout은 지원된 info/note/tip/warning/success/error를 사용하며 임의 DOM/class를 만들지 않는다.

## reference와 검증

검증된 username/opaque reference를 resolver로 실제 mention node에 연결한다.
ticket/document는 실제 canonical URL의 badge·hover·편집 보존을 확인한다.
이미지는 실제 upload/attachment 결과에 연결하며 임의 파일명·URL·접근 가능성을 발명하지 않는다.
본문과 댓글 각각 작성 → 저장 → 읽기 → 재수정 → 재저장 → 최종 읽기를 확인한다.
mention/badge가 평문으로 퇴화하지 않고 표·callout·checkbox·이미지가 유지되어야 한다.
이미지는 complete와 nonzero naturalWidth를 확인한다.
댓글 접기·초안 복원·최대화·Ctrl+Z, 로딩·실패·질문 후 잠금 해제와 seed 보존도 검증한다.

출처: app/content/htmlsafe.py, app/static/components/editor/, tests/frontend/editor/test_editor_roundtrip_ui.py.

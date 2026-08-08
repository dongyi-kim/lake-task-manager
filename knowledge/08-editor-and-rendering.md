# 본문·코멘트가 실제로 렌더되는 방식

에이전트가 만든 글은 세 경로를 지난다. **경로마다 살아남는 표기가 다르다** — 여기 적힌
것만 쓰면 세 곳 모두에서 같게 보이고, 벗어나면 조용히 글자로 굳거나 통째로 사라진다.

```
① 초안 → 생성      HTML → desc_field_value(환경별 저장형) → Jira
② 자동완성 → 에디터  HTML → normalizeAiHtml → TipTap → (저장 시 ①과 같은 변환)
③ 채팅 답변         텍스트 → agentMd → 사람 칩·티켓 뱃지
```

## 쓸 수 있는 태그

`p br hr b strong i em u s del ins sub sup small mark code pre kbd blockquote q cite
ul ol li dl dt dd a img span div section article h1~h6 table thead tbody tr td th caption
colgroup col`

**통째로 사라지는 것**: `script style iframe object embed form button textarea select
link meta`. `on*` 속성은 어디서도 허용되지 않는다.

`a` 는 `href title download` 만, `img` 는 `src alt title width height` 만 남는다.
링크 스킴은 `http https mailto tel` 뿐이다.

> 근거: `app/content/htmlsafe.py` `_ALLOWED_TAGS` / `_ALLOWED_ATTRS` / `_DROP_SUBTREE`

## 표기별 규칙

| 쓰려는 것 | 이렇게 쓴다 | 이렇게 된다 |
|---|---|---|
| 체크리스트 | `<ul data-type="taskList"><li data-checked="false">할 일</li></ul>` | 저장 시 `<p><input type="checkbox">할 일</p>` 로 평탄화 → 뷰어는 읽기전용 체크박스, 편집기로 열면 다시 체크리스트 |
| 사람 | `[~skcc.x1042]` | 사용자 링크 + 알림. **이름만 쓰면 평문**이 된다 |
| 티켓 | `DL-123` (평문) | 뷰어가 상태·타입 뱃지로 |
| 문서·링크 | `<a href="URL">제목</a>` | 링크 뱃지 |
| 콜아웃 | `<div class="callout callout-info">…</div>` | info/note/tip/warning/success/error |
| 코드 | `<pre><code class="language-sql">…</code></pre>` | 하이라이트 |
| 영역 구분 | `=== 제목 ===` (한 줄짜리 문단) | 본문에서만. 코멘트에선 그냥 글자로 남는다 |
| 이미지 첨부 | `!파일명!` | 첨부에 그 이름이 있어야 한다 |

## 하지 말 것

- **마크다운을 쓰지 않는다.** `[제목](URL)`·`**굵게**`·`- 항목`은 변환되지 않고 **글자
  그대로** 남는다. 에디터도 뷰어도 마크다운을 해석하지 않는다.
- **코드펜스로 감싸지 않는다.** ` ```html ` 로 시작하면 백틱까지 본문이 된다.
- `<li>` 에 스타일·클래스를 붙이지 않는다 — 정화에서 떨어져 나간다.
- 체크리스트 항목 안에 다시 체크리스트를 넣지 않는다(저장 평탄화에서 계층이 사라진다).

## 왜 이렇게 나뉘어 있나 (실제로 겪은 것)

- **편집기가 요구하는 모양과 저장 모양이 다르다.** TipTap 은 `li[data-type="taskItem"]`
  에 `<div><p>` 래퍼까지 있어야 체크리스트로 파싱한다. 저장은 반대로 `<p><input>` 평문이다.
  그래서 코드가 양쪽을 오간다 — 불러올 때 `liftCheckboxes`, 끼워 넣을 때 `normalizeAiHtml`
  (`app/static/components/ui/CommentEditor.js`). **모델에게 DOM 세부를 외우게 하지 않는다**:
  단순한 형태로 내게 하고 변환은 코드가 한다.
- **멘션은 정화 전에 Jira 형태로 바꿔 둔다.** 안 그러면 `data-type` 이 떨어져 나가 그냥
  '@이름' 글자가 되고, 알림도 링크도 없이 재편집에서 굳는다(리포트된 버그).
- **콜아웃 class 는 벤더마다 다르다.** prod 는 `jePanel_info`, mock 은 `callout callout-info`
  — 정화 단계에서 표준형으로 정규화한다. 그래서 우리는 `callout callout-*` 하나만 쓴다.

> 출처: `app/content/htmlsafe.py` · `app/static/components/ui/CommentEditor.js` ·
> `jira_client.desc_field_value`

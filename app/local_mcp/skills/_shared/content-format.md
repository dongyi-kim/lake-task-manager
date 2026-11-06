# LTM 작성 형식

일반 생성의 `descriptionHtml`, `update_description.html`, `add_comment.html`, `edit_comment.html`, 전환의 `commentHtml`은 **HTML fragment**다. `<html>`, `<head>`, 전체 웹페이지나 TipTap JSON은 보내지 않는다. LTM이 UI와 같은 serializer로 실제 Jira 저장 형식에 맞춘다. Agent가 환경별 HTML/wiki 저장 형식을 선택하지 않는다.

기존 내용 수정은 읽어 온 편집용 HTML과 실제 식별자를 보존한다. 코드·SQL·사용자 텍스트의 `&`, `<`, `>`는 텍스트 위치에서 HTML escape하고, 속성값의 따옴표도 escape한다. JSON 인자에서는 JSON escaping을 별도로 적용한다.

## Jira Task 본문 작성 스타일

- Task·Epic·SubTask 본문은 **표·bullet 목록·개조식·간결한 문체를 적극 권장**한다. 비교·매핑·여러 항목의 속성은 표, 병렬 작업·요점은 bullet 목록으로 정리한다. 짧은 내용을 억지로 표로 늘리지는 않는다.
- 개조식은 문장 끝을 짧게 정리하는 방식이다. 예: “원천과 대상의 건수를 대조합니다.” → “원천과 대상의 건수 대조”. 조사·연결어까지 빼거나 명사를 길게 이어 붙이지 않는다. 대상·조건·수치·인과관계가 흐려지면 짧은 문장으로 쓴다.
- **점검사항·작업목표·완료 조건에는 체크박스를 적극 활용**한다. 수행·검증할 수 있는 항목별로 나누고, 배경 설명·참고 사실은 일반 문단이나 목록으로 구분한다. 실제 체크리스트 형식과 완료 상태는 아래 체크리스트·Bulk Markdown 규약을 따른다.
- 사용자 지정 양식·문체가 있으면 우선한다. 기존 본문 수정 시 요청하지 않은 영역의 문체나 체크 상태를 일괄 변경하지 않는다.

## 기본 서식과 목록

```html
<h2>작업 범위</h2>
<p><strong>필수</strong> 항목과 <em>검토</em> 내용을 구분합니다.<br>다음 줄입니다.</p>
<p><s>취소된 내용</s> <code>source_table</code></p>
<ul><li>첫 작업</li><li>둘째 작업</li></ul>
<ol><li>검증</li><li>결과 공유</li></ol>
<blockquote><p>확인한 원문 인용</p></blockquote>
<hr>
```

제목은 `h1`~`h6`, 기본 문단은 `p`다. 슬래시 메뉴(`/table`, `/code` 등)는 에디터 UI의 입력 동작이다. MCP에서는 슬래시 명령 문자열 대신 아래의 결과 HTML을 작성한다.

밑줄(`<u>`)과 아래 첨자(`<sub>`)는 HTML 저장 환경에서 유지된다. Jira Wiki 저장 환경에서는 이 서식이 사라질 수 있으므로 의미를 텍스트에도 담는다.

## 체크리스트

```html
<ul data-type="taskList">
  <li data-checked="false"><p>원천 건수 대조</p></li>
  <li data-checked="true"><p>확인된 테스트 완료</p></li>
</ul>
```

`true`는 실제 완료가 확인된 항목에만 쓴다. 일반 `input` 체크박스나 `☑` 문자로 체크리스트 상태를 대신하지 않는다.

## 표

```html
<table><tbody>
  <tr><th>항목</th><th>결과</th></tr>
  <tr><td>원천 건수</td><td>검증 결과</td></tr>
</tbody></table>
```

HTML 저장 경로는 Jira에서도 보이는 표 테두리를 보강한다. 임의 CSS나 복잡한 중첩 표에 의존하지 않는다. 저장 형식에 따라 병합 셀 등 세부 표현이 달라질 수 있으므로 보존이 필요한 기존 표는 구조를 유지한다.

## 코드블록

```html
<pre class="jecodeblock"><code class="language-sql">SELECT id
FROM source_table
WHERE amount &lt; 100;</code></pre>
```

`language-python`, `language-sql` 등 실제 코드 언어를 지정할 수 있다. 줄번호·문법 강조를 위한 화면 DOM이나 span을 직접 넣지 않는다. 들여쓰기와 줄바꿈을 보존한다.

## 콜아웃과 영역 구분

```html
<div class="callout callout-warning"><p>확인된 주의 사항</p></div>
```

`info`, `note`, `tip`, `warning`, `success`, `error` 종류를 지원한다. 이 스타일은 업무 상태를 변경하지 않는다.

영역 구분은 **티켓 본문 전용**이다. 댓글에서는 일반 제목을 사용한다.

```html
<div class="sec-title-node">완료 조건</div>
<p>합의된 완료 조건을 적습니다.</p>
```

이 노드는 저장할 때 `=== 완료 조건 ===` 표식으로 변환된다. 단순 가로줄은 `<hr>`이며 영역 구분과 다르다.

## 이미지와 파일

1. `add_attachment(key, filename, base64_data, client_mutation_id, content_type)`로 대상 티켓에 파일을 업로드한다. 파일 경로가 아닌 base64 바이트를 받으며 원본 크기는 최대 10MiB다.
2. 반환된 `path`를 이미지의 `src`와 파일 링크의 `href`에 쓴다. 파일 링크의 `data-file`에는 반환된 `filename`을 넣어 표시 제목과 실제 파일명을 구분한다. 속성값은 HTML escape한다.
3. 완성된 HTML로 본문/댓글을 별도 게시한다. 업로드만으로 본문이 바뀌지 않는다.

```html
<p><img src="__ATTACHMENT_PATH__" alt="검증 결과 화면" width="640"></p>
<p><a class="file-badge" href="__ATTACHMENT_PATH__" data-file="__ATTACHMENT_FILENAME__">검증 결과 파일</a></p>
```

`__ATTACHMENT_PATH__`와 `__ATTACHMENT_FILENAME__`은 실제 업로드 응답으로 치환한다. 로컬 경로, 임시 `blob:` URL, 브라우저 세션 전용 주소를 저장하지 않는다. 기존 `/api/img?...` 같은 LTM 프록시 URL은 서버가 원래 경로로 복원하므로 임의로 새 Jira 경로를 추측하지 않는다. 확인에는 `get_attachments`·`read_attachment`를 쓴다.

## Bulk Markdown

`bulk_validate`·`bulk_create`의 `items[].description`만 Markdown이다. 지원되는 범위를 사용한다.

| 기능 | 문법 |
| --- | --- |
| 제목·목록 | `#`~`####`, `- 항목`, `1. 항목` |
| 체크리스트 | `- [ ] 할 일`, `- [x] 완료된 일` |
| 인라인 | `**굵게**`, `*기울임*`, `~~취소선~~`, 백틱 코드 |
| 표 | 파이프 헤더 + `| --- | --- |` 구분행 |
| 코드·인용 | 세 백틱 코드펜스, `> 인용` |
| 링크·구분선 | `[제목](https://...)`, 단독 URL, `---` |

이 Markdown은 일반 CommonMark 전체 구현이 아니다. 코드펜스의 언어 표시는 저장 HTML에 전달되지 않는다. Raw HTML은 escape돼 글자로 남고, `@이름`은 평문이다. Jira `[~id]`를 넣으면 Wiki 저장 환경에서 실제 멘션으로 해석될 수 있으므로 bulk 본문에는 이 문법을 쓰지 않는다.

`![설명](https://...)`은 이미지를 첨부/삽입하지 않고 웹 링크로 바뀐다. 웹이 아닌 링크 경로는 텍스트로 남는다. 실제 이미지·사람 멘션·콜아웃이 필요하면 생성 후 반환 key에 첨부와 HTML 본문 수정으로 추가한다.

## 보존 범위

임의 HTML/CSS를 그대로 보존하는 API가 아니다. `script`, `iframe`, `form` 등의 요소와 이벤트 핸들러, 지원되지 않는 속성·클래스는 제거된다. 앱의 화면 스타일·아바타·뱃지 내부 DOM을 복사하지 말고 이 문서의 의미 있는 마크업과 확인된 ID/URL을 사용한다.

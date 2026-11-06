# 인물 검색·멘션·링크

## 사람 식별

`search_users(query, limit=8)`로 Jira가 반환한 후보를 확인한다. 사람이 부른 이름·호칭을 username으로 추측하지 않는다. `강수아 책임님`처럼 요청된 경우 전체 표현과 이름 부분을 구분해 검색하고, 실제 응답의 `id`와 `display`에 있는 이름·회사 표기를 비교한다. 성을 제외한 이름만 주어졌어도 같은 방식으로 후보를 확인한다.

- 결과가 많으면 `limit`을 최대 50까지 늘리거나 성명·회사·실제 username으로 좁힌다. `mayHaveMore=true`는 제한 도달, `null`은 전체 범위를 알 수 없다는 뜻이다. 후보가 하나여도 전사 유일 인물이라는 증거는 아니다.
- `matchContext.configured`, `sameModule`, `recentLookupAt`, `recentSelectionAt`, `recentMentionAt`은 각각 독립된 참고 근거다. 최근성·같은 모듈만으로 동명이인을 자동 확정하지 않는다.
- `contextAvailable`과 null 값으로 미확인 문맥을 구분한다. `contextChanged=true`면 계정이 바뀐 것이므로 다시 검색한다. 멘션 시각은 UI 삽입 시각이며 댓글 게시의 증거가 아니다.
- “이 티켓 담당자”처럼 맥락이 명확하면 `get_ticket`의 실제 담당자 ID와 교차 확인한다. 자료를 확인해도 후보를 구분할 수 없으면 회사·모듈 등 구체적인 차이로 질문하고 해당 인물의 지정/멘션은 보류한다.
- `get_user(user_id)`는 확인한 ID의 상세 조회다. 빈 `user_id`는 로그인한 사용자다. 이메일에서 앞부분을 잘라 ID를 만들지 않는다.

## 실제 사람 멘션

```html
<p><span data-type="mention" data-id="__USER_ID__">@__DISPLAY_NAME__</span> 검토 부탁드립니다.</p>
```

`__USER_ID__`와 `__DISPLAY_NAME__`을 조회로 확인한 `id`와 짧은 표시 이름 `name`으로 치환하고 HTML escape한다. `data-type`·`data-id`는 큰따옴표 속성으로 사용한다. UI가 아바타·표시 이름을 렌더링하므로 프로필 사진 HTML이나 CSS를 직접 붙이지 않는다.

평문 `@이름`이나 이름만 담긴 일반 하이퍼링크는 실제 멘션이 아니다. LTM은 위 canonical span을 실제 Jira 사용자 멘션으로 변환한다. Jira의 알림 전달 성공 여부까지 보장한다는 뜻은 아니다. 멘션은 담당자 지정과도 별개다.

## Task 작성 시 참조 연결 권장

Task·Epic·SubTask 작성에 실제로 참고한 Jira 이슈·Confluence 문서·외부 자료는 **본문 링크와 함께 티켓의 이슈 링크·문서 연결에도 적극 등록하는 것을 권장**한다. 단순 본문 링크만으로 Jira 관계나 remote link가 생기지는 않는다.

초안 공유 시 연결할 대상과 관계 유형을 함께 보여주고 같은 승인 범위에서 처리한다. 이미 허용된 연결을 건별로 다시 확인하지 않는다. 조회 중 발견한 모든 자료를 무차별로 연결하거나 사용자가 제외한 자료를 추가하지 않는다. `get_ticket_context`의 이슈 링크와 `get_documents`의 문서 링크를 확인해 기존 연결은 재사용하고 새 연결만 추가한다.

## Jira 티켓

key 또는 제목으로 `search_issues`/`search_workspace`에서 찾고 `get_ticket`으로 대상·내용을 확인한다. 링크는 응답의 사용자용 `url`을 사용한다. Jira REST `self`는 본문 탐색 링크가 아니다.

```html
<p>단순 번호: __ISSUE_KEY__</p>
<p>명시적 참조: <a href="__JIRA_URL__">__ISSUE_TITLE__</a></p>
```

LTM 열람 화면에서 단순 티켓 키는 short 뱃지로 자동 표시되고, 명시적 Jira 링크는 기본 detailed 뱃지로 표시된다. 번호 언급을 모두 링크로 감싸서 의도치 않게 detailed로 바꾸지 않는다. 뱃지의 상태·제목·아바타 DOM을 직접 만들지 않는다.

Jira 이슈 관계는 `list_options(kind="link_types")`에서 지원 타입을 확인하고 `add_issue_link(key, other_key, client_mutation_id, type_name, direction)`로 등록한다. 단순 참조에는 실제 제공되는 일반 관련 관계를 선택하며, 근거 없이 차단·의존·중복 관계를 만들지 않는다. `direction="outward"`는 key→other_key이며 parent/Epic 계층 설정과는 별개다. 추가 후 `get_ticket_context`로 확인한다.

## Confluence 문서와 외부 URL

- 통합 탐색은 `search_workspace(query, sources=["jira", "confluence"])`를 사용한다. 문서 목록을 더 조회하려면 `search_confluence_documents`의 `hasMore`·`nextStartAt`을 따른다.
- 문서를 근거로 내용을 쓰려면 `read_confluence_document(page=확인한ID또는URL)`을 읽는다. 긴 문서는 `hasMore`·`nextOffset`을 따르고, 후속 호출의 `version`에 첫 응답의 `version.number`를 전달해 같은 버전의 조각만 이어 읽는다. 검색 excerpt나 문서 제목만으로 문서 내용을 단정하지 않는다.
- `read_confluence_document`는 설정된 Confluence의 숫자 page ID 또는 지원되는 `/pages/{id}`·`?pageId=` URL을 받는다. 구형 `/display/SPACE/title` 링크는 검색에서 page ID를 확인한다. 임의 외부 URL을 가져오는 도구가 아니다.
- 본문에는 확인한 문서 URL과 제목으로 일반 `<a href="...">제목</a>`를 넣는다. LTM이 Confluence/웹 링크 뱃지로 렌더링한다. 별도의 Confluence 사람 멘션 문법이나 `@문서제목`을 만들지 않는다.

```html
<p><a href="__CONFLUENCE_URL__">__DOCUMENT_TITLE__</a></p>
<p><a href="https://example.com/reference">외부 참고 링크 예시</a></p>
```

예시 도메인은 사용자 자료가 아니다. 실제 작성에서는 사용자가 제공했거나 조회로 확인한 URL을 사용한다. 단순 URL 등록 요청은 문서 내용을 읽었다고 주장하지 않는 한 불필요한 내용 조회를 요구하지 않는다.

참조 문서·외부 URL은 `add_document(key, url, client_mutation_id, title)`로 티켓의 문서 연결 목록에 등록하고 `get_documents`로 확인한다. 이것은 Jira remote link 등록이며 Confluence 문서 생성/수정은 수행하지 않는다.

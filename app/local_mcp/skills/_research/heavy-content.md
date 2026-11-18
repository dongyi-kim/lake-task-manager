# 본문·댓글의 선택 수집과 재사용

## 필요한 필드만 묶어 읽기

| 필요한 내용 | 도구 |
|---|---|
| 후보 탐색·목록 | `search_issues(detail="summary")` |
| 정해진 1~100 key의 필드 | `get_tickets(keys=[...])` |
| 같은 대상의 본문·논의 | 위 묶음 도구에 `include_description=true`, `include_comments=true` 중 필요한 것만 지정 |
| 한 티켓의 표시·편집 HTML | `get_ticket(detail="read")` / `detail="edit"` |
| 단건 기본정보만 | `get_ticket(include_description=false, include_comments=false)` |

검색·묶음 조회의 두 포함 옵션은 기본값이 모두 false다. 두 옵션은 `detail`과 독립적이므로 summary에서도 필요한 원문을 받을 수 있다. 단건 `get_ticket`의 `include_description` 생략/null은 기존 detail 기본값을 유지한다(full/read/edit는 본문, summary는 기본정보). 명시적 false는 본문 다운로드·해석도 피하며, true+summary는 표시 HTML을 반환한다. 단건 댓글 기본값도 false다.

검색의 `fields.description`과 `fields.comment`는 Jira 원문이다. 본문은 HTML/wiki 등 저장 형식일 수 있고 댓글은 원래 배열/페이지 객체다. `descriptionEditHtml`·`get_comment_source(...).html`과 구별하며, 조회 원문을 HTML 쓰기 인자에 그대로 넣지 않는다. 개별 설명·댓글 캐시는 별도로 재사용되며, 둘 다 없으면 함께 받고 하나만 없으면 빠진 필드만 보충한다. 필요한 원문이 이미 있으면 key마다 단건 조회를 반복하지 않는다.

## 댓글 완전성

- `fields.comment`는 최대 20개의 `get_comments` 미리보기가 아니다. Jira가 제공한 내용을 보존하되, 반환됐다는 사실만으로 전체 댓글이라고 하지 않는다.
- `commentsCoverage.complete=true`이고 대상 key·허용 원본 나이가 질문과 맞으면 그 댓글을 전체 근거로 재사용한다. 월간 완전 집계라는 이유만으로 `get_comments`나 `get_evidence_page`를 다시 호출하지 않는다. 이 증명은 해당 key의 댓글에 한하며 자식·다른 티켓까지 포함하지 않는다.
- `complete=false`, 부분 페이지, total 미상, bare array는 전체의 증명이 아니다. `commentsMissingKeys`와 필드 누락/null/형식 오류는 미확인이며 댓글 0개가 아니다. 알려진 빈 설명(null/빈 문자열)과 댓글 완전성은 다른 계약이다. `get_tickets.complete`도 요청 티켓의 반환 여부일 뿐 논의의 완전성을 뜻하지 않는다.
- 전체 논의가 필요하면 `commentsCoverage.nextCall`의 name으로 지정된 도구에 arguments를 복사한다. embedded 댓글의 후속 조회는 순서를 보장할 수 없어 `start_at=0`에서 시작할 수 있다. 받은 25개를 보고 임의로 25부터 시작하지 않는다. ID로 중복을 제거하고 원본의 total·수정값 변화도 확인한다.
- 후속 호출은 처음 정한 `cache_mode`를 유지한다. nextCall에 없으면 원래 값을 추가하며 실패를 피하려고 hot→warm 등으로 완화하지 않는다. 현재 페이지 수집 이후에는 `get_evidence_page`의 실제 nextCall 전체를 따른다. 긴 댓글의 조각 복원은 [근거 수집](research-evidence.md)을 따른다.

## 페이지·크기·시점

본문/댓글 포함 검색은 경량 metadata를 최대 100개씩 찾고 필요한 heavy 필드만 최대 10티켓씩 받는다. `max_results`는 상한 요청이며 실제 returned·nextStartAt을 따른다. 출력 페이지와 `get_tickets` 전체 결과는 각각 16MiB 한도이며 내용을 조용히 자르지 않는다.

metadata 전용 검색은 30분 고정 snapshot을 같은 옵션으로 이어 읽는다. 본문/댓글 포함 검색은 `snapshotId=null`, `snapshotFrozen=false`다. 옛 snapshot_id를 보내지 않고 같은 JQL·포함 옵션·cache_mode·strategy·max_parts로 실제 nextStartAt을 따른다. 실시간 변경은 membership·순서를 바꿀 수 있어 고정 보고서로 취급하지 않는다.

크기 오류는 같은 큰 요청의 무한 재시도로 해결되지 않는다. 현재 그룹의 key를 더 작게 나누거나 `search_issues(max_results=1, ...)`로 줄인다. 한 티켓의 본문+댓글 합계도 초과하면 두 필드 요청을 나눈다. 단일 댓글/논의도 과대하면 `get_evidence_page(kind="comments")`의 손실 없는 레코드 조각을 읽는다. 큰 단일 본문은 지원되는 `get_ticket(detail="read")`를 사용하되 실제 오류 한도를 따른다. 성공 구간은 보존하고 나눈 현재 그룹의 미완료 구간부터 끝낸다. 연결 유실은 같은 도구·인자로 복구하며 ID/digest로 대조한다.

캐시 hit도 MCP 호출·큰 응답의 토큰·해석 비용을 없애지 않는다. 이미 받은 정상 원문은 로컬 근거에서 재사용한다. 성능은 MCP 횟수, 실제 upstream REST 횟수·바이트, 시간·토큰을 구별하고 cold/warm을 따로 관측한다. 캐시 hit나 execution 진단만으로 실제 REST 감소를 추정하지 않는다.

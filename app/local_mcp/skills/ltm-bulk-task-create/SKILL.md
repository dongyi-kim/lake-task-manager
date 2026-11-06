---
name: ltm-bulk-task-create
description: LTM MCP로 여러 Task 또는 SubTask를 검증하고 일괄 생성한다. 항목별 담당자·Epic/parent·Markdown 본문을 구성하고 부분 성공과 재시도를 처리할 때 사용한다.
---

# LTM 일괄 티켓 생성

요청된 여러 업무를 순서 있는 항목 목록으로 만든다. 중복 업무를 분할해서 개수를 늘리거나 제공되지 않은 담당자·기한을 만들어 넣지 않는다. 검증 후 항목별 초안을 공유하고 묶음 단위로 확인받는 것을 권장한다. 해당 묶음이 이미 승인됐거나 바로 제출하라는 지시가 있으면 재확인하지 않는다. 초안 요청이면 게시하지 않는다.

## 필요한 참고 문서

- [제출 전 초안 검토](references/draft-review.md): 항목별 내용·대상 제시와 묶음 단위 확인.
- 이름·링크 확인은 [사람과 링크](references/people-and-links.md)를 사용한다.
- 본문은 [작성 형식](references/content-format.md)의 **Jira Task 본문 작성 스타일**과 **Bulk Markdown** 절을 따른다. 비교는 표, 작업 목록은 간결한 개조식, 점검사항·작업목표는 체크박스를 권장한다. 배경·원인·질문은 조사와 연결어를 살린 짧은 문장으로 쓴다. 일괄 생성의 Markdown은 일반 티켓 생성의 HTML과 다르다.
- 실행·부분 실패는 [쓰기·검증 절차](references/write-workflow.md)를 따른다.
- [예제 데이터](references/examples.json)의 `bulkItems`는 구조를 보여주는 샘플이다.

## 항목 구성

한 batch는 1~100개이며 `mode`는 `task` 또는 `subtask` 하나다. 부모와 자식을 같은 batch에서 만들거나 아직 없는 key를 참조할 수 없다. 먼저 부모를 생성·확인하고 반환 key로 다음 batch를 구성한다.

| 항목 필드 | 규칙 |
| --- | --- |
| `summary`, `type` | 비어 있지 않은 제목과 실제 Jira 허용 type |
| `epic` | task 모드에 반드시 포함. 기존 Epic key 또는 명시적 `null` |
| `parent` | subtask 모드의 기존 Task-tier key. `null` 불가 |
| `assignee` | 사람 검색으로 확인한 Jira 사용자 ID |
| `priority`, `components` | 실제 선택지를 확인. `components`는 문자열 배열 |
| `labels`, `duedate` | 문자열 배열, `YYYY-MM-DD` |
| `description` | 선택적 **Markdown** 문자열. `descriptionHtml`이 아님 |

`epic`과 `parent`를 동시에 넣어 관계를 중복 지정하지 않는다. Reporter는 현재 Jira 사용자로 생성되며 항목에 넣지 않는다. 다른 사용자 정의 필드를 추가하면 무시될 수 있으므로 `bulk_validate`의 경고를 읽는다.

## 검증·실행·확인

1. 대상 계층, 타입, Epic/parent 존재 여부, 지정할 사람·선택 값을 확인한다. 자연어 이름을 바로 `assignee`에 쓰지 않는다.
2. `bulk_validate(mode, items)`를 호출한다. 이것은 쓰기를 수행하지 않는다. 오류의 `index`·`field`를 원래 항목에 대응시켜 수정하고, 무시되는 필드나 이미지 문법 경고도 사용자 의도와 대조한다.
3. 최종 항목과 순서를 확정한 뒤 `bulk_create(mode, items, client_mutation_id)`를 호출한다. 정규화된 `items`를 사용할 경우 실행 전에 확정하고, 재시도에서는 최초 실행한 원본 요청을 그대로 보존한다.
4. 결과의 `created`, `failed`, `errors`, `warnings`와 원래 인덱스를 확인한다. 전체 요청이 반환됐어도 모든 항목이 생성됐다는 뜻은 아니다. 일부 실패가 있어도 성공 항목은 롤백되지 않는다.
5. 재시도는 **동일한 전체 ordered items + 동일 mutation ID**로 한다. 같은 ID 아래 실패 행만 추리거나 순서를 바꾸지 않는다. 결과가 불명확한 항목에 새 ID를 발급해 중복 생성하지 않는다.
6. 생성된 key로 결과를 확인하고 성공·실패·미확인을 항목별로 보고한다. 이미지나 실제 사람 멘션 등 Markdown 밖의 요소는 생성 완료 key에 별도 첨부/HTML 수정이 필요하다. 후속 수정 실패를 티켓 생성 실패로 오인하지 않는다.

각 항목이 실제로 참고한 Jira 이슈·Confluence 문서·외부 자료는 티켓의 이슈 링크·문서 연결에도 적극 등록하는 것을 권장한다. [사람과 링크](references/people-and-links.md)에 따라 항목별 연결 계획을 초안에 포함하고, 생성 완료 key에 승인 범위의 연결을 추가·확인한다. bulk 입력에 임의의 링크 필드를 만들지 않으며 연결 실패 때문에 성공한 티켓을 다시 생성하지 않는다.

100개를 넘으면 순서를 유지해 여러 batch로 나누고 각 batch에 별도 mutation ID를 사용한다. 생성하지 못한 내용이나 후속 작업을 생략한 채 전체 완료라고 보고하지 않는다.

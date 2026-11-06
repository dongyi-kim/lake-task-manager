# Confluence 문서 작성과 호환 범위

## 새 문서와 첨부

Confluence 탭의 **새 문서**에서 Space, 현재 문서 아래/Space 최상위 위치, 제목을 선택한다.
본문은 Jira 본문·댓글과 같은 LTM 에디터를 사용한다. 헤딩 자동 번호는 기본으로 켜져 있으며
끄고 켤 수 있다. 편집 중에는 번호를 별도 속성으로 관리하고 발행 시 헤딩 텍스트에 한 번 넣는다.
사용자가 직접 입력한 번호는 임의로 지우지 않는다. 기존 Confluence 문서의 LTM 재편집 UI는
아직 제공하지 않으며, 운영 문서 수정의 공동편집 호환성 gate도 유지한다.

파일 선택·이미지 붙여넣기·드래그로 넣은 첨부는 초안과 함께 IndexedDB에 저장한다.
닫기·페이지 이동·재접속 후 복원하며, 일반 초안은 기존 7일 정책을 따른다. 파일당 최대 10MiB,
본문 이미지 형식은 PNG/JPEG/GIF/WebP/AVIF다. 지원하지 않는 본문 이미지는 문서 생성 전에 알린다.

발행은 다음 순서를 따른다.

1. 멘션 사용자를 Confluence에서 확인하고 본문을 native storage XHTML로 변환한다.
2. 고정 요청 ID와 본문·첨부 계획을 먼저 영구 저장한다. 저장 실패 시 외부 쓰기는 하지 않는다.
3. 첨부 참조를 포함한 문서를 한 번 생성하고 문서 ID를 보관한다.
4. 각 파일을 해당 문서의 첨부 API로 업로드하고 성공한 단계를 저장한다.
5. 모두 성공하면 초안을 정리하고 발행한 문서를 연다.

문서 생성과 파일 업로드는 하나의 원자적 API가 아니다. 업로드가 끝나기 전에는 Confluence에서
첨부 참조가 일시적으로 미완성일 수 있다. 실패하면 생성된 문서를 삭제하거나 다시 만들지 않고
동일 요청 ID로 남은 작업을 이어간다. 응답 유실 뒤 권한 오류가 나더라도 이전 생성 가능성을
보존한다. 자동 재시도 기간이 지난 요청은 원문에서 결과를 확인한 뒤 **결과 확인 완료 · 새 초안
시작**으로 종료할 수 있다. 이미 생성된 문서를 삭제하지 않으며 종료한 발행 기록을 남긴다.

## Confluence DC 9.2 매크로 호환성 조사

이 절은 공식 9.2 문서와 현재 코드에 대한 조사 결과다. 운영 9.2.4의 실제 응답 검증을 대신하지
않으며, 아래에서 추가 개발로 표시한 항목은 구현 완료를 의미하지 않는다.

| 항목 | 저장 형식 / 공식 근거 | 현재 구현과 추가 작업 |
|---|---|---|
| Include Page | [`include`](https://confluence.atlassian.com/display/CONF92/Include%2BPage%2BMacro) | 페이지 참조를 가진 전용 에디터 노드 필요. 같은 사이트 내 문서와 원본 접근 권한을 따른다. 포함 원문의 업데이트·첨부·중첩 참조를 별도 처리해야 한다. |
| Page Tree | [`pagetree`](https://confluence.atlassian.com/conf92/page-tree-macro-1477576336.html) | root, sort, startDepth 등의 매개변수를 보존하고 기존 LTM 트리 API/캐시로 표시하는 전용 노드 필요. root는 @self/@home/@parent/@none과 문서 참조를 구분한다. |
| 본문 목차 | [`toc`](https://confluence.atlassian.com/conf92/table-of-contents-macro-1477576394.html) | 우측 LTM 목차와 별개다. 본문에 삽입되는 노드와 헤딩 범위·번호·필터 설정, 본문 내 앵커 이동이 필요하다. |
| 코드 블록 | [`code`](https://confluence.atlassian.com/conf92/code-block-macro-1477576170.html) | 현재 신규 문서는 언어와 원문을 native code macro로 저장한다. 제목·접힘·줄 번호·시작 번호의 세부 설정 보존은 추가 개발이다. |
| 가로 구분선 | [`<hr />`](https://confluence.atlassian.com/conf92/confluence-storage-format-1477576006.html) | 매크로가 아닌 기본 storage 요소. 기존 에디터·변환기에서 지원한다. |

### 현재 코드에서 확인한 차이

- `document_html()`에 `<h2 id="original-heading">`과 해당 앵커 링크를 넣으면 ID가 제거되고
  링크는 Confluence base URL로 바뀐다. 본문 TOC를 그대로 표시하는 것만으로는 이동을 보장할 수 없다.
- 현재 문서가 111이고 Include Page의 이미지가 `/download/attachments/222/...`이면 이미지
  프록시가 표시하지 않는다. 포함된 페이지의 ID·권한을 확인하는 첨부 경로가 필요하다.
- `body.view`에는 Confluence의 JavaScript/CSS에 의존하는 매크로가 있을 수 있다. LTM의 정화된
  HTML만으로 Page Tree 등의 모든 상호작용이 동작한다고 가정하지 않는다.
- 신규 문서용 `confluenceStorage()`는 기존 native macro를 읽어 되돌려 쓰는 파서가 아니다.
  렌더링 HTML을 재편집 원본으로 삼으면 매개변수·참조·알 수 없는 매크로가 사라질 수 있다.

### 후속 구현 결정

LTM 에디터에 Confluence 전용 macro 노드를 추가한다. `/` 메뉴에서 삽입하고 문서 선택과 매개변수
폼으로 수정한다. 저장에는 `ac:structured-macro`, 매개변수, body, 참조를 사용하며 표시 결과로
대체하지 않는다. 알 수 없는 매크로는 원본 storage를 그대로 보존하는 잠긴 블록으로 표시한다.
원본을 보존할 수 없는 문서 전체 재저장은 계속 차단한다. 기존 Jira 에디터에는 이 확장을 켜지 않는다.

Include Page는 참조 페이지의 권한, 재귀·순환, 중복 헤딩 ID와 첨부를 검증한다. Page Tree는 기존
조회/캐시를 공유하되 매크로 정렬·깊이 의미를 따로 적용한다. 목차는 같은 문서의 앵커로 이동하며,
목차의 outline 번호와 본문 헤딩 번호가 이중 표시되지 않도록 분리한다.

캐시는 **원문 revision**과 **동적 매크로 결과**를 구분한다. 현재 `confbody` storage 원문은 기본
30일, `confview` 렌더 결과·트리·최신 버전 확인은 일반 갱신 정책(기본 10분)이다. 이 구분을
유지한다. Include Page 대상만 바뀌거나 자식 문서를 생성/이동하면 부모 revision이 같아도 표시
내용은 달라질 수 있으므로, 의존 문서의 변경은 해당 표시 결과를 무효화해야 한다. 외부 변경은
짧은 갱신 주기로 확인한다. 부모의 최신 버전 확인 결과는 포함 문서 전체의 최신성 보장이 아니다.

후속 회귀는 매크로별 작성→발행→Confluence 열람→LTM 재편집→재발행, 미지원 매크로 보존,
원본만 변경된 Include Page, 중첩/순환·권한 실패·이미지 포함, Page Tree 이동·정렬·대량 자식,
한글/중복 헤딩·TOC 이동·번호 중복을 포함한다. mock의 storage fixture와 실 9.2.4 응답을 비교한다.

## 이번 변경의 검증과 리뷰

- 공통 jira820: 전체 153개 테스트 통과. Python 3.9/3.10/3.11/3.12 GitHub CI 통과.
- LTM: 작성 UI·문서 탐색·API·에디터 계약 관련 51개 통과. 후속 번호 CSS·이미지 제한을 포함한
  작성 UI 6개도 통과했다. 전체 앱 suite는 PR GitHub CI에서 실행한다.
- 기존 Jira 본문·댓글의 전체 서식/슬래시 메뉴와 작성→발행→열람→수정→재발행→열람: 4개 통과.
- 일반 검증은 고의 지연 없이 수행했다. 이미지 파일 선택·붙여넣기·드래그, 파일 다운로드 bytes,
  초안 재접속, 동명 첨부, 일부 업로드 실패, 생성 응답 유실 뒤 권한 오류를 확인했다.

리뷰에서 발견한 사항은 다음과 같이 수정했다. 행 번호는 이 작성 UI 변경 기준이다.

1. **P1 — 저장 실패 뒤 미저장 요청 실행:** `app/static/components/ui/ConfluenceComposer.js:99`.
   메모리 run이 있어도 모든 시도에서 persist 성공 후에만 쓰기 시작. 두 번 저장 실패 시 쓰기 0회 확인.
2. **P1 — 응답 유실 뒤 403에서 요청 ID 손실:** `app/static/components/ui/ConfluenceComposer.js:122`.
   뒤의 오류가 앞의 생성 실패를 증명하지 않으므로 run 유지. 재접속 뒤에도 동일 payload/ID 확인.
3. **P2 — 복원 초안의 부모 표시 불일치:** `app/static/components/ui/ConfluenceComposer.js:20`.
   부모 ID별 선택지로 표시/전송 대상 일치. 다른 문서에서 복원 후 원래 부모를 표시·전송하는지 확인.
4. **P2 — 만료된 발행의 영구 잠금:** `app/static/components/ui/ConfluenceComposer.js:38`.
   원문 확인 후 종료 기록을 보존하고 새 본문 scope로 시작. 기존 내용을 새 발행으로 오인하지 않음.
5. **변경 크기 권고:** `app/static/components/ui/ConfluenceComposer.js:1`을 포함한 신규 문서 흐름.
   목록 탐색, 공통 작성 API, 신규 문서 에디터를 순차 PR로 분리했다. 에디터의 초안/발행/재개/첨부는
   같은 사용자 흐름이라 하나의 검증 단위로 유지한다.

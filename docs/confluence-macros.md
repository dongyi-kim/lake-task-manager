# Confluence 기본 매크로 — DC 9.2.4

Confluence 전용 편집기에서 아래 명령을 `/`로 삽입하거나 **매크로** 버튼에서 선택한다.
각 블록의 상단 설정 버튼으로 제목·참조 문서·옵션을 수정한다. Jira 본문·댓글의 메뉴는 유지한다.

| 명령 | 지원 범위 |
|---|---|
| `/expand` | 제목, 리치 본문, 열람 시 접기·펼치기 |
| `/panel` | 제목, 리치 본문 |
| `/info`, `/note`, `/tip`, `/warning` | 제목, 아이콘 표시, 리치 본문 |
| `/excerpt` | 발췌 본문, 원문에서 숨기기 |
| `/include`, `/excerpt-include` | 문서 검색·선택, 참조/첫 발췌 표시, 발췌 테두리 숨기기 |
| `/pagetree` | 시작 문서/특수 루트, 정렬·역순·초기 깊이, 검색·전체 접기/펼치기 |
| `/children` | 대상 문서, 깊이·정렬·역순 |
| `/toc`, `/toc-zone` | 헤딩 범위·목차 번호, 전체/영역 목차, 클릭 이동 |
| `/attachments` | 대상 문서, 파일명·날짜·크기 정렬, 다운로드 |
| `/status` | 문구, 5가지 색, 연한 배경 |
| `/anchor` | 이름 있는 이동 지점 |
| `/noformat` | 공백·줄바꿈 보존, 테두리 숨기기 |
| `/code` | 언어·제목·접기·줄 번호·시작 번호 |
| `/hr` | native XHTML 가로 구분선(매크로 아님) |

## 원문과 호환 경계

- 편집 원본은 `body.storage`다. 렌더링 결과를 재저장하지 않는다. 매크로는 native
  `ac:structured-macro`, `ac:parameter`, `ac:rich-text-body`/CDATA `ac:plain-text-body`로 발행한다.
- 알려진 매크로의 추가 매개변수·macro ID와 미지원 매크로는 원문을 보존한다. 미지원 블록은
  잠긴 블록으로 표시한다. 기존 native 할 일 목록도 task ID 충돌을 피하려고 원문 보존한다.
  새 할 일 ID는 보존된 최대 ID 다음에서 시작한다. 기존 이미지·파일·사용자 링크 원문도 유지한다.
- Panel의 개별 색상/테두리 옵션, TOC 정규식·개별 CSS, Children 발췌/표시 개수,
  Attachments 이전 버전·라벨/패턴 등 고급 옵션은 LTM에서 모두 재현하지 않는다.
  저장 시 보존하고 열람 시 미지원 옵션 안내를 표시한다. 원문 링크로 Confluence에서 확인할 수 있다.
- Include는 참조 문서 권한·허용 Space·순환을 확인한다. 포함 깊이는 6, 한 문서의 매크로 처리는
  100개, XML은 2MB/깊이 80/50,000개 노드로 제한한다. 실패한 블록만 안내하고 다른 본문은 표시한다.
  외부 XML 선언은 거부하되 코드 CDATA 안의 `&nbsp;`, `<!DOCTYPE html>`, `]]>`는 보존한다.
- 이미지 프록시는 접근을 확인한 참조 문서의 첨부만 허용한다. 상류 JavaScript/CSS를 실행하지 않는다.
- Page Tree 기본 루트는 `@home`. 검색은 펼친 항목만이 아닌 해당 계층의 CQL 검색을 사용한다.
  전체 펼치기는 순차 실행하며 중단·화면 이동을 처리한다. 페이지네이션 중복을 제거한다.
- 포함 문서 원문은 기존 revision 캐시, 최신 revision·트리·첨부 목록은 일반 갱신 정책을 공유한다.
  Include를 합친 결과는 부모 revision만으로 장기 캐싱하지 않는다. **최신 여부 확인**으로 부모가
  그대로인 경우에도 포함 문서를 갱신한다. UI와 MCP는 같은 서비스·캐시를 사용한다.
- 수정 초안은 최초 revision을 함께 저장한다. 닫았다가 다시 열어도 최신 revision으로 바꿔치기하지
  않는다. 충돌은 발행을 막고 초안을 보존한다. 쓰기는 기존 mutation receipt와 invalidation을 사용한다.
- 실제 운영 9.2.4/Synchrony와의 공동편집은 이번 로컬 검증으로 보장하지 않는다.
  기존 운영 수정 gate를 유지하며 해당 환경에서 `update_page` 검증 완료 후 UI 수정을 활성화한다.

## 검증 결과

- LTM 관련 회귀 76개: 최초 73개 통과, 안내 문구/URL 변경/비동기 화면 전환을 반영한 재검증에서
  나머지 3개 통과. 보강한 매크로/브라우저 13개, CDATA 보존 7개도 통과. 동일 사례 재실행을 중복 합산하지 않는다.
- jira820 전체 155개 통과(Confluence 관련 74개). native 원문·macro fixture와 `ancestor` CQL,
  Space 홈페이지 조회를 공통 mock에 추가하고 앱의 결정적 world에도 매크로 문서를 추가했다.
- 18종의 모든 slash 명령과 설정 재열기, `/hr`, native 문서의 두 번 수정·발행·열람,
  이미지 재편집/재발행, 미지원 매크로와 task ID·헤딩 ID·CDATA 보존을 실제 Chromium에서 확인했다.
- 표·스타일·멘션·뱃지·파일/이미지·슬래시 메뉴를 사용하는 기존 Jira 본문/댓글 왕복 회귀도 통과했다.
- 55개 하위 문서 페이지네이션, 일부 페이지 503/재시도, 아직 펼치지 않은 하위 문서 검색,
  탭 이동, 원문만 바뀐 Include 갱신, 캐시 hit/실패 후 재조회, 열려 있는 편집/재접속 초안의 충돌을 확인했다.
- 실제 dev UI에서 Expand 펼치기, 접힌 내부 헤딩으로 목차 이동, 설정 편집과 390px/1440px 레이아웃을 확인했다.
  일반 테스트의 고의 지연은 0ms다. 실패 시나리오에서만 지정한 응답을 주입했다.

## 최종 리뷰 발견 사항과 처리

행 번호는 이 변경 기준이며 모두 수정했다. 리뷰 중 중복 제보는 같은 번호에 모았다.

1. **P1 초안 재진입 후 최신 revision 덮어쓰기** — `app/static/components/ui/ConfluenceComposer.js:31`.
   최초 `sourceVersion` 영구 저장·충돌 차단, 닫기→외부 수정→복원 시나리오 추가.
2. **P1 plain text body의 CDATA 누락** — `app/static/lib/confluenceMacroStorage.js:39`.
   CDATA 직렬화와 `]]>` 분할, 원문 텍스트 동등성 검증.
3. **P2 문자열 Include 참조가 발행 시 거부됨** — `app/static/lib/confluenceMacroStorage.js:34`.
   native 문자열/`ri:page` 참조 둘 다 보존. 선택 해제한 옵션은 기존 참조도 제거.
4. **P2 기존 task ID 손실·재번호 부여** — `app/static/lib/confluenceMacroStorage.js:81`, `app/static/lib/confluenceStorage.js:21`.
   기존 할 일 목록 원문 보존, 신규 ID 충돌 방지.
5. **P2 native 링크 제목·앵커 손실** — `app/services/confluence_macros.py:145`.
   rich label을 안전하게 렌더링하고 페이지/앵커 이동을 보존.
6. **P2 Page Tree 기본 루트 오해석** — `app/services/confluence_macros.py:44`.
   기본 `@home`, Space 홈페이지 조회와 fixture 보강.
7. **P2 부모 revision이 같으면 Include를 갱신할 수 없음** — `app/static/components/views/ConfluenceView.js:157`.
   최신 확인과 매크로 재시도에서 참조 revision을 다시 확인.
8. **P2 전체 펼치기가 한 단계뿐이고 검색이 로드된 항목에 한정됨** — `app/static/components/ui/ConfluenceMacroTree.js:56`.
   중단 가능한 순차 계층 탐색과 ancestor CQL 검색.
9. **P2 Noformat 테두리 옵션 미적용** — `app/services/confluence_macros.py:225`.
   `nopanel`을 sanitizer/CSS까지 연결.
10. **P2 첨부 날짜·크기 정렬이 native와 반대** — `app/static/components/ui/ConfluenceMacroTree.js:23`.
    날짜 최신순·크기 큰순 기본값, `sortOrder` 역순 반영.
11. **리뷰 크기 권고** — `app/services/confluence_macros.py:85`, `app/static/components/editor/confluenceMacroNodes.js:1`.
    원문 처리/공통 API와 편집·리더 UI를 순차 PR로 분리한다. 새 문서와 기존 문서 왕복은 같은
    노드·직렬화·초안 흐름을 공유해 함께 검증한다. 추가 추상화는 만들지 않았다.
12. **추가 회귀: 발행 후 빈 초안이 다시 저장됨** — `app/static/components/editor/editorSubmission.js:13`.
    clear/setEditable 상태 변경의 update 이벤트를 억제. 재수정 두 번 검증.
13. **추가 리뷰: CDATA의 entity/DOCTYPE 리터럴 변형·거부** — `app/static/lib/confluenceMacroStorage.js:6`,
    `app/services/confluence_macros.py:20`. CDATA 밖에서만 정규화·선언 검사. 양쪽 파서와 UI 왕복 검증.

## 조사한 공식 자료

- [DC 9.2 storage format](https://confluence.atlassian.com/conf92/confluence-storage-format-1477576006.html)
- [Expand](https://confluence.atlassian.com/conf92/expand-macro-1477576220.html),
  [Include Page](https://confluence.atlassian.com/display/CONF92/Include%2BPage%2BMacro),
  [Excerpt](https://confluence.atlassian.com/conf92/excerpt-macro-1477576214.html)
- [Page Tree](https://confluence.atlassian.com/conf92/page-tree-macro-1477576336.html),
  [Children](https://confluence.atlassian.com/conf92/children-display-macro-1477576166.html),
  [TOC](https://confluence.atlassian.com/conf92/table-of-contents-macro-1477576394.html)
- [Code Block](https://confluence.atlassian.com/conf92/code-block-macro-1477576170.html),
  [Noformat](https://confluence.atlassian.com/conf92/noformat-macro-1477576290.html),
  [Attachments](https://confluence.atlassian.com/conf92/attachments-macro-1477576138.html)

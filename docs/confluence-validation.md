# Confluence 구현·검증 기록

대상 API는 Confluence Data Center 9.2.4다. 검증 환경은 공통 HTTP mock과 로컬 Chromium이며,
운영 Confluence·Synchrony에 쓰기를 수행하지 않았다. 사용자 앱의 4457 서버도 교체하지 않았다.

## PR 구성

| PR | 책임 | 검토 시 변경 규모 |
|---|---|---|
| [#155](https://github.com/dongyi-kim/lake-task-manager/pull/155) | 공통 조회·발행 리비전·버전별 원문 캐시 | 26개 파일, +1,435 / -143 |
| [#156](https://github.com/dongyi-kim/lake-task-manager/pull/156) | Confluence 탭·트리·즐겨찾기·검색·우측 목차 | 12개 파일, +927 / -6 |
| [#157](https://github.com/dongyi-kim/lake-task-manager/pull/157) | MCP 페이지·댓글·첨부·라벨 작성과 응답 유실 복구 | 19개 파일, +1,417 / -14 |
| `feature/confluence-document-organization` | 같은 Space의 구조 변경·제한·휴지통·일괄 정리 | 14개 파일, +1,200 / -10 |

각 PR은 앞 PR을 base로 하는 순차 검토 구조다. 전체 회귀는 각 PR의 **Code tests**에서 확인한다.
로컬에서는 관련 파일만 실행했다. 실제 LLM 평가나 운영 SSO 검증을 이 결과에 포함하지 않는다.

## 시나리오별 확인

| 범위 | 확인한 동작 | 근거 |
|---|---|---|
| 리비전·캐시 | 현재 head와 고정 버전 원문 분리, 동일 리비전 재사용, 강제 최신 확인, 권한 상실 시 원문 접근 차단, 동시 요청·mutation fence | `tests/jira/confluence/test_revision_cache.py` |
| MCP 조회 | Space 범위, CQL·pagination, 고정 버전 분할 읽기, UTF-8 8,000바이트 이내 댓글 미리보기, ASCII·이모지 장문 | `tests/mcp/test_confluence_read_integration.py` |
| 문서 탐색 | 트리만 열 때 전체 본문을 읽지 않음, 홈페이지 밖 루트, 펼치지 않은 페이지 검색·상위 경로, 즐겨찾기 재접속 보존 | `tests/frontend/shell/test_confluence_browser_ui.py` |
| 우측 목차 | 중복·한글 헤딩 ID, 클릭 시 문서 내부 이동·키보드 포커스, 직접 스크롤 시 현재 헤딩 변경, 새 버전의 목차 재구성, 좁은 화면 접기 | 같은 브라우저 테스트의 `test_confluence_tree_favorites_search_revision_and_responsive` |
| 버전 변경 UI | 최신 확인만으로 본문 교체하지 않음, 새 버전 보기에서 스크롤 유지, 확인 실패 때 기존 문서 유지 | 같은 브라우저 테스트 |
| 인증·설정 UI | Confluence 오류가 Jira 인증을 해제하지 않음, 통합검색에서 앱 내부 문서 열기, 원문 보관 설정 저장·기본값 복원 | 같은 브라우저 파일의 나머지 3개 시나리오 |
| 작성 | 실제 MCP SDK와 공통 HTTP mock으로 작성→읽기→수정, 출력 스키마, 충돌·권한, 첨부 중복·크기·버전 | `tests/mcp/test_confluence_commands.py`, `test_confluence_content_reads.py` |
| 응답 유실 | 지연된 HTTP 성공을 작업별 응답으로 정규화, 같은 mutation ID 재조회 시 중복 쓰기 없음 | `tests/mcp/test_confluence_command_recovery.py` |
| 문서 정리 | 실제 SDK의 dry-run→실행→재시도, 이동 후 트리 캐시, 제한 fingerprint 충돌, 휴지통 조회·복원 | `tests/mcp/test_confluence_organization_integration.py` |
| 부분 실패 | 권한 실패의 의존 작업 생략, 성공 행 보존, Confluence 인증만 복구 후 나머지 재시도 | 같은 SDK 통합 파일 |
| 안전한 구조 변경 | 사이클·다른 Space·불완전 자식 목록 거부, 순차 버전 투영, 후속 확인 실패 시 PUT 재실행 금지 | `tests/mcp/test_confluence_organization.py` |

로컬 실행 기록은 중복된 테스트 묶음이므로 숫자를 합산하지 않는다.

- 공통 조회·캐시 관련: 119개 통과. 이후 댓글 크기·ancestor projection 보완 관련 28개 통과.
- UI 관련: 266개 통과. 실제 Chromium 시나리오 4개 모두 통과.
- 조회·UI·작성의 통합 재검증: **129개 통과, 1개 생략**, 91.46초. 위 브라우저 4개 포함.
- 문서 정리·SDK·가이드·출력 스키마: **55개 통과, 1개 생략**, 21.30초.
- 최종 UTF-8 댓글 크기·pagination: 2개 통과. 공통 HTTP mock으로 교체한 작성 SDK 시나리오: 1개 통과.
- 최종 문서 정리 경계 사례 재검증: **34개 통과**, 7.86초. 서버의 자식 페이지 크기 제한, SQLite 재연결 후 순차 이동 재시도, 확인 실패 뒤 읽기만 재시도하는 SDK 사례를 포함한다.
- 생략 1개는 Windows에서 symlink 생성이 허용되지 않는 스킬 경로 테스트다. 브라우저 생략이 아니다.
- Windows pytest 캐시 디렉터리의 쓰기 경고 1회는 실행 결과 저장 경고이며 테스트 실패가 아니다.

## 리뷰 지적과 처리

아래는 context·breaking changes·testing·change size 리뷰에서 나온 모든 지적이다.
줄 번호는 해당 구현 파일의 위치이며, 이후 수정으로 이동할 수 있다. 중복된 breadcrumb 지적은 하나로 합쳤다.

1. **P0/context, 수정:** 첨부 base64가 기본 약 349K자, 최대 1.4M자로 반환되던 문제.
   `app/services/confluence_content_reads.py:57`에서 기본 1KiB·최대 4KiB 미리보기와 버전 고정 다운로드 URL로 분리했다.
2. **P0/context, 수정:** 댓글 목록에 긴 원문이 그대로 들어가던 문제.
   `app/local_mcp/confluence_comment_previews.py:10`에서 전체 data JSON을 UTF-8 8,000바이트로 제한하고 소비한 행만큼 cursor를 이동한다. 원문은 ID·버전을 지정해 별도로 읽는다.
3. **P2/context, 수정:** `attachment_exists` 오류 사유가 출력 스키마에 없어 쓰기 오류를 계약 오류로 바꾸던 문제.
   `app/local_mcp/schemas/common.py:40`에 사유를 추가하고 SDK 계약을 확인했다.
4. **P1/breaking, 수정:** 완료된 SSO HTTP handle의 원시 dict/int가 정규화 없이 mutation receipt에 저장되던 문제.
   `app/services/confluence_commands.py:150`에서 작업별 `recover_inflight`를 지정한다. 알려지지 않은 응답은 reconciliation으로 넘긴다.
5. **P2/breaking, 수정:** 직접 제한 집계 endpoint의 expand 경로가 operation prefix를 누락했다.
   `app/services/confluence_organization.py:208`의 `restrictions`에서 read/update별 경로를 사용한다. 불완전 목록은 writable fingerprint를 만들지 않는다.
6. **P2/breaking·testing, 수정:** `body.view` projection에 ancestors가 없어서 breadcrumb가 사라졌다.
   `app/routes/confluence.py:161`에서 현재 head의 ancestors를 합친다. mock도 요청된 expand만 반환하도록 바꾸어 재발을 검증한다.
7. **P2/breaking, 수정:** 일괄 이동 dry-run이 이동하지 않은 하위 문서를 거치는 사이클을 놓쳤다.
   `app/services/confluence_organization.py:27`의 가상 부모 경로 계산이 기존 경로와 앞선 변경을 함께 따라간다.
8. **P2/testing, 수정:** dry-run이 빈 제목·DTD 본문·페이지 아닌 대상을 통과시켰다.
   `app/services/confluence_organization.py:271`의 `bulk`에서 공통 storage 검증 및 타입·제목 검증 후 다음 버전을 투영한다.
9. **change size, 남은 검토 부담:** 4개 PR 모두 기능 규모가 크며 800줄/복잡한 변경 500줄 권고를 충족하지 못한다.
   `app/jira/confluence_service.py:1`, `app/static/components/views/ConfluenceView.js:1`, `app/services/confluence_commands.py:1`, `app/services/confluence_organization.py:1`을 각각 중심으로 검토한다.
   공통 fixture와 캐시→탐색 UI→작성/복구→정리의 의존 경계로 나눴다. 큰 변경을 권고 이내라고 주장하지 않는다.
10. **change size, 수정:** 리뷰 당시 untracked였던 목차 모듈이 PR에 빠질 위험.
    `app/static/lib/confluenceToc.js:1`을 UI PR에 포함했다.
11. **testing·change size, 수정:** 정상 SDK 시나리오가 별도 REST fake를 재구현해 실제 공통 경로 검증이 약했다.
    `tests/mcp/test_confluence_commands.py:268`을 공통 HTTP mock으로 전환했다. 특정 응답 유실 주입용 fake는 유지한다.
12. **P2/testing, 수정:** 모든 자식을 이동한 뒤 부모를 삭제하는 dry-run이 첫 자식 페이지만 보고 남은 자식이 있다고 오판했다.
    `app/services/confluence_organization.py:132`에서 전체 페이지를 확인한 뒤 가상 관계를 적용한다. 잘못된 cursor·반복·불완전 응답은 삭제를 허용하지 않는다.
13. **P2/testing, 수정:** 같은 페이지를 연속 이동한 배치를 재시도하면 이미 성공한 중간 위치를 다시 확인해 영구 대기가 됐다.
    `app/services/confluence_organization.py:124`에서 성공한 위치 확인을 별도 영구 receipt로 저장한다. 확인 실패는 다시 읽되 완료한 PUT을 반복하지 않는다. SQLite 재연결까지 검증했다.

리뷰 당시 미완성이었던 문서 정리 도구 등록은 runtime·output schema에 완료했고 실제 SDK 통합 시나리오로 확인했다.
설치·환경 구분을 사용자 계정 분리로 오해한 초기 의견은 철회됐으며, 단일 로컬 사용자 원칙을 변경하지 않았다.

## 운영에서 남은 확인

기존 문서 본문 수정과 이동은 실제 DC 9.2.4 공동편집 검증 전에는 운영에서 활성화하지 않는다.
절차와 기능별 활성화 설정은 [문서 정리 가이드](confluence-organization.md)에 있다.
발행 version이 같다는 것은 미발행 Synchrony 초안이 없다는 뜻이 아니며 `draftState=unknown`을 유지한다.
직접 제한 fingerprint와 삭제 직전 version 검사는 서버의 원자적인 CAS가 아니다.
복제·다른 Space로 이동·Space 관리·영구 삭제는 구현 범위에서 제외했다.

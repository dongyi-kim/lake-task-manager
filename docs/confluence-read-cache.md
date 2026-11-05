# Confluence 읽기와 최신 버전 확인

대상은 Confluence Data Center 9.2.4다. LTM 문서 화면, 통합검색과 MCP는 같은
Confluence 인증·조회·SQLite 캐시를 사용한다. 조회 범위는 기존
`search.confluence.spaces` 설정을 따른다.

## 문서 찾기와 읽기

- `search_confluence_documents`: 키워드·정확한 제목·Space·부모·조상·라벨·수정일 필터,
  또는 전체 CQL을 전달한다. CQL에도 설정된 Space 범위가 적용된다.
- `list_confluence_spaces`, `list_confluence_pages`: 접근 가능한 Space와 루트·직계 자식을
  조회한다. 홈페이지 밖 루트도 포함한다. 각 목록의 `nextStartAt`을 따라간다.
- `get_confluence_context`: 조상 경로, 댓글, 첨부 목록, 라벨, 페이지 제한을 조회한다.
- `read_confluence_document`: 숫자 ID 또는 설정된 서버의 페이지 URL로 본문을 읽는다.
  `text`는 읽기용 텍스트, `storage`는 매크로를 보존하는 Confluence 원문 XHTML이다.

본문이 여러 조각이면 첫 응답의 `version.number`를 이후 요청의 `version`에 전달한다.
`offset > 0`에는 버전이 필수다. 읽는 중 새 문서가 발행돼도 서로 다른 버전의 조각을
합치지 않는다. 기존 단일 조각 호출은 그대로 사용할 수 있다.

## 최신 여부 확인

`check_confluence_revision(page, known_version)`은 본문을 내려받지 않고 현재 발행 버전을
직접 확인한다. `latestPublishedVersion`, `lastModifiedAt`, `lastModifiedBy`, `checkedAt`,
`isLatest`를 반환한다. 비교 버전을 생략하면 `isLatest`는 `null`이다.

검색 인덱스는 발행보다 늦게 갱신될 수 있다. 최신 여부는 검색 결과로 판단하지 않는다.
발행 버전이 같아도 다른 사람이 미발행 공동편집 초안을 작성 중일 수 있으므로
`draftState`는 `unknown`이다. 댓글·첨부·권한·문서 위치도 본문 버전과 별도로 확인한다.

## 캐시 정책

현재 버전 정보는 일반 갱신 간격(기본 10분)과 화면 보존 기간(기본 24시간)을 따른다.
특정 버전의 `storage` 본문은 기본 30일 보관하며, **캐시 설정**에서 1~365일로 바꿀 수 있다.
동일한 버전이면 본문을 다시 다운로드하지 않는다. 명시적인 최신 확인은 fresh 캐시도 우회한다.
렌더링 HTML과 댓글·첨부·트리는 일반 정책을 유지한다. 매크로 출력은 페이지 버전이 같아도
다른 데이터의 변경에 따라 달라질 수 있기 때문이다.

`cacheInfo`는 `bodyVersion`, `bodyFetchedAt`, `latestPublishedVersion`, `headCheckedAt`,
`isLatest`를 구분한다. 조회만으로 원본의 수집 시각을 늘리지 않는다. 권한 회수·현재 문서
삭제가 확인되면 해당 문서의 모든 원본 버전 캐시를 제거한다. 네트워크 오류는 정상 캐시를
삭제하지 않지만 최신 확인 성공으로 표시하지 않는다.

본문 캐시의 총 한도는 256MiB이며, 8MiB를 초과하는 단일 응답은 저장을 생략한다.
캐시 한도 때문에 실제 조회 결과를 잘라 반환하지는 않는다.

설정 우선순위는 저장된 사용자 설정 → `CACHE_CONFLUENCE_BODY_RETENTION_SECONDS` →
YAML `cache.policy.confluenceBodyRetentionSeconds` → 기본값이다. 변경은 재시작 없이 적용된다.

## 검증 범위

로컬 fixture는 안정된 문서 ID, 실제 부모 관계, 버전 이력, XHTML, 댓글·첨부·라벨·제한을
지원한다. Synchrony 공동편집을 재현하는 서버는 아니며 실제 9.2.4 쓰기 호환성 검증을
대체하지 않는다. 읽기·캐시 회귀는 `tests/jira/confluence/`와
`tests/mcp/test_confluence_read_integration.py`에서 확인한다.

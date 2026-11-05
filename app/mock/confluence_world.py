"""Stable Confluence documents for the shared mock/local world; no random state."""

from datetime import timedelta
from html import escape


AUTHOR = "test.ui01"
DOCUMENT_IDS = {
    "guide": "890100000001",
    "quality": "890100000002",
    "releases": "890100000003",
    "rollback": "890100000004",
    "pagination": "890100000010",
    "long_title": "890100000011",
    "pmo": "890100000020",
    "pmo_child": "890100000021",
}
PAGINATION_CHILD_IDS = tuple(str(890100001000 + index) for index in range(1, 56))

GUIDE_TITLE = "[UI 검증] Confluence 운영 가이드"
PAGINATION_TITLE = "[UI 검증] 페이지네이션 · 운영 문서 55개"
PMO_TITLE = "[UI 검증] PMO 문서 탐색"


def _guide_body():
    return f"""<p>이 문서는 로컬 개발용 공통 월드의 고정 문서입니다. 서버를 다시 실행해도 같은 문서 ID와 계층으로 복원됩니다. 왼쪽 트리와 즐겨찾기, 오른쪽 목차를 함께 확인하세요.</p>
<h2>1. 개요 · 적재 운영 절차</h2>
<p>원천 데이터 확인부터 배포 후 모니터링까지, 데이터 적재 과정에서 확인할 기준을 정리합니다. 긴 문서를 스크롤하면 목차의 현재 항목도 함께 바뀌어야 합니다.</p>
<table><tbody><tr><th>단계</th><th>확인 항목</th><th>완료 기준</th></tr>
<tr><td>수집</td><td>원천 스키마 · 기준 시각</td><td>원천 준비 완료</td></tr>
<tr><td>검증</td><td>건수 · 중복 · NULL</td><td>검증 보고서 승인</td></tr>
<tr><td>배포</td><td>스케줄 · 서비스 조회</td><td>최초 실행 정상</td></tr></tbody></table>
<h2>2. 적재 전 준비</h2>
<p>변경 대상의 명세와 영향을 받는 시스템을 확인합니다. 신규 테이블과 기존 테이블의 변경을 구분하고 적용 일자를 기록합니다.</p>
<h3>확인 항목</h3><ul><li>유일 키와 증분 기준 컬럼</li><li>원천·목적지 데이터 타입</li><li>예상 건수와 보관 기간</li></ul>
<h3>2.1 권한 및 연결</h3><p>실행 계정의 조회·쓰기 권한과 네트워크 경로를 확인합니다. 비밀번호와 인증 토큰은 문서에 기록하지 않습니다.</p>
<h2>3. 품질 검증</h2>
<p>원천과 목적지의 같은 시각 데이터를 비교합니다. 증분 추출 중 원천이 변경될 수 있으므로 비교 기준 시각을 고정하고 실행 ID를 남깁니다.</p>
<h3>확인 항목</h3><ol><li>원천과 목적지의 총 건수 비교</li><li>주요 키 중복과 NULL 확인</li><li>날짜·금액 컬럼의 경곗값 확인</li><li>하위 서비스의 조회 결과 확인</li></ol>
<p>위와 같은 제목의 헤딩이 앞에도 있습니다. 목차 항목은 서로 다른 위치로 이동해야 하며, 한글과 숫자가 들어간 제목도 올바르게 처리해야 합니다.</p>
<h3>3.1 품질 확인 쿼리</h3><pre><code>SELECT load_date, COUNT(*) AS row_count
FROM lake.daily_orders
GROUP BY load_date
ORDER BY load_date DESC;</code></pre>
<h4>3.1.1 검증 결과 해석</h4><p>건수가 같더라도 값이 동일하다고 단정하지 않습니다. 키 단위 비교와 업무 기준 집계를 함께 확인합니다.</p>
<h5>3.1.1.1 빈 데이터</h5><p>정상적인 0건과 연결 실패를 구분합니다. 원천 조회 실패를 정상적인 빈 결과로 저장하지 않습니다.</p>
<h6>3.1.1.1.1 기록 원칙</h6><p>검증 시각, 입력 구간, 실행 ID와 판단 근거를 남깁니다.</p>
<h2>4. 배포와 모니터링</h2>
<p>검증이 완료된 뒤 스케줄을 활성화합니다. 최초 세 번의 실행에서 시작 시각과 처리 시간, 입력·출력 건수를 비교합니다.</p>
<p>원천 준비가 늦어지거나 처리 시간이 늘어나면 현재 실행의 상태를 먼저 확인합니다. 동일 구간을 중복 실행하지 않도록 이미 완료된 작업을 구분합니다.</p>
<p>관련 하위 문서: <a href="/pages/viewpage.action?pageId={DOCUMENT_IDS['quality']}">품질 점검 체크리스트</a>, <a href="/pages/viewpage.action?pageId={DOCUMENT_IDS['releases']}">운영 배포 기록</a>.</p>
<h2>5. 장애 대응과 롤백</h2>
<p>장애가 발생하면 실패 단계와 실행 ID를 기록합니다. 재시도 전에 완료 구간과 미완료 구간을 확인합니다.</p>
<blockquote><p>검증에 실패한 데이터는 공개하지 않습니다. 기존 정상 데이터는 새 검증이 완료될 때까지 유지합니다.</p></blockquote>
<ul><li>입력 지연: 원천 준비 상태 확인 후 재개</li><li>스키마 변경: 계약 확인 후 매핑 수정</li><li>품질 실패: 공개 중단 후 이전 검증 버전 복원</li></ul>
<p>자세한 순서는 손자 문서인 <a href="/pages/viewpage.action?pageId={DOCUMENT_IDS['rollback']}">롤백 실행 절차</a>에서 확인합니다.</p>
<h2>6. 변경 이력</h2><table><tbody><tr><th>구분</th><th>검증 내용</th></tr><tr><td>초기 버전</td><td>표·목차·코드·중복 헤딩·문서 링크</td></tr><tr><td>공통 월드</td><td>mock·local이 같은 ID와 계층을 사용</td></tr></tbody></table>"""


def build_confluence_documents(today):
    """Return fresh corpus rows without changing existing generated Jira/Confluence rows.

    Explicit IDs and ancestor IDs survive title changes; generic jira820 owns REST
    serialization, permissions and later mutations. Existing title-path corpus rows
    continue to coexist with these fixture records.
    """
    rows = []

    def add(key, title, body, *, parent=None, space="DL", days_ago=1):
        parent_row = next((row for row in rows if row["id"] == parent), None)
        ancestors = [*(parent_row["ancestors"] if parent_row else [])]
        if parent_row:
            ancestors.append({"id": parent_row["id"], "title": parent_row["title"]})
        row = {"id": key, "title": title, "space": space, "parentId": parent or "",
               "ancestors": ancestors, "author": AUTHOR, "body": body,
               "date": today - timedelta(days=days_ago), "time": "09:30", "action": "edited"}
        rows.append(row)
        return key

    guide = add(DOCUMENT_IDS["guide"], GUIDE_TITLE, _guide_body())
    add(DOCUMENT_IDS["quality"], "[UI 검증] 품질 점검 체크리스트",
        "<h2>필수 점검</h2><p>건수·중복·NULL·경곗값을 확인합니다.</p><h3>기준 시각</h3><p>같은 구간의 원천과 목적지를 비교합니다.</p>", parent=guide)
    releases = add(DOCUMENT_IDS["releases"], "[UI 검증] 운영 배포 기록",
        "<h2>배포 전</h2><p>검증 승인과 영향 범위를 확인합니다.</p><h2>배포 후</h2><p>실행 ID와 실제 처리 건수를 기록합니다.</p>", parent=guide)
    add(DOCUMENT_IDS["rollback"], "[UI 검증] 롤백 실행 절차 · 손자 문서",
        "<h2>공개 중단</h2><p>스케줄을 멈추고 실패 구간을 확인합니다.</p><h2>복원 확인</h2><p>이전 검증 버전을 복원하고 하위 서비스의 조회를 확인합니다.</p>", parent=releases)
    pagination = add(DOCUMENT_IDS["pagination"], PAGINATION_TITLE,
        "<h2>55개 하위 문서</h2><p>기본 한 페이지보다 많은 문서가 있습니다. 더 보기를 통해 마지막 문서까지 확인합니다.</p>")
    for index, cid in enumerate(PAGINATION_CHILD_IDS, 1):
        title = f"[UI 검증] 운영 문서 {index:02d} · 페이지네이션"
        add(cid, title, f"<h2>운영 문서 {index:02d}</h2><p>{escape(title)}의 고정 본문입니다.</p>",
            parent=pagination, days_ago=2)
    add(DOCUMENT_IDS["long_title"],
        "[UI 검증] 아주 긴 문서 제목 · 데이터 적재 원천 스키마 변경 영향 범위와 운영 배포 승인 및 복구 절차를 함께 확인하는 가이드",
        "<h2>긴 제목 표시</h2><p>트리·즐겨찾기·검색 결과에서 제목이 겹치지 않고 문서를 구분할 수 있어야 합니다.</p>", parent=guide)
    pmo = add(DOCUMENT_IDS["pmo"], PMO_TITLE,
        "<h2>프로젝트 문서</h2><p>DL과 다른 Space의 문서입니다. Space 전환 시 이전 검색 결과와 트리가 섞이지 않아야 합니다.</p>", space="PMO")
    add(DOCUMENT_IDS["pmo_child"], "[UI 검증] PMO 주간 점검",
        "<h2>진척 확인</h2><p>이번 주 완료 사항과 다음 주 계획을 확인합니다.</p>", parent=pmo, space="PMO")
    return rows

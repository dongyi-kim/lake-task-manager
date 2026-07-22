import threading

# 이 스레드에서 나가는 상류 호출의 우선순위(0=사용자, 1=백그라운드 갱신).
# 호출 지점마다 인자를 넘기지 않으려고 스레드 로컬에 둔다 — provider 가 읽는다.
_PRIO = threading.local()


def upstream_priority():
    return getattr(_PRIO, "value", 0)


class background_upstream:
    """with 블록 안의 상류 호출을 백그라운드 우선순위로."""

    def __enter__(self):
        _PRIO.value = 1

    def __exit__(self, *exc):
        _PRIO.value = 0
        return False


"""AuthProvider 인터페이스 — JiraClient 는 어떤 인증인지 몰라야 한다."""


# ── XSRF (Atlassian 공통) ────────────────────────────────────────────
# Jira/Confluence/Bitbucket DC 는 **쓰기 요청(POST/PUT/DELETE)** 에 XSRF 토큰이 없으면 403 이다.
# (GET/HEAD 는 면제.) 'no-check' 헤더로 우회한다 — Atlassian 공식 방식.
# 향후 편집 기능(티켓 코멘트·필드 수정, 상태 전이 등)이 전부 이 경로를 타므로 **한 곳에서 관리**한다.
# 이 헤더는 세 제품 모두 동일하게 통한다(제품별 분기 불필요).
XSRF_HEADER = {"X-Atlassian-Token": "no-check"}
WRITE_HEADERS = {**XSRF_HEADER, "Content-Type": "application/json", "Accept": "application/json"}
# 멀티파트 업로드(첨부)용 — Content-Type 은 멀티파트 인코더가 boundary 와 함께 직접 지정하므로
# **여기서 넣으면 안 된다**(boundary 누락 → 400). XSRF·Accept 만 유지.
MULTIPART_HEADERS = {**XSRF_HEADER, "Accept": "application/json"}


class AuthProvider:
    # 여러 스레드에서 동시 GET 가능한가 (basic/PAT=True, SSO=Playwright 단일 context=False)
    supports_parallel = False

    def post_json(self, path, json_body=None, params=None):
        """POST + JSON 응답. 쓰기(생성)·검색(Bitbucket code) 공용.
        기본 미지원 — 필요한 provider 만 구현한다. XSRF 헤더는 구현체가 WRITE_HEADERS 로 붙인다."""
        raise NotImplementedError("이 provider 는 POST 를 지원하지 않습니다")

    def put_json(self, path, json_body=None, params=None):
        """PUT + JSON 응답. 편집(필드·코멘트 수정 등)용. XSRF 필수."""
        raise NotImplementedError("이 provider 는 PUT 을 지원하지 않습니다")

    def delete(self, path, params=None):
        """DELETE. 삭제용. XSRF 필수. 응답 본문이 없을 수 있어 상태만 확인."""
        raise NotImplementedError("이 provider 는 DELETE 를 지원하지 않습니다")

    def post_multipart(self, path, filename, data, content_type=None, field="file", params=None):
        """multipart/form-data 단일 파일 업로드(첨부). XSRF 필수, Content-Type 은 인코더가 지정.
        여러 파일은 호출 측에서 파일당 한 번씩 부른다(Playwright multipart 가 같은 필드 반복 불가).
        return: 파싱된 JSON 응답(첨부 객체 리스트 등)."""
        raise NotImplementedError("이 provider 는 멀티파트 업로드를 지원하지 않습니다")

    def get_json(self, path, params=None):
        raise NotImplementedError

    def get_text(self, path, params=None):
        """ATOM/XML 응답용 (예: /activity 피드)."""
        raise NotImplementedError

    def get_bytes(self, path, params=None):
        """원본 바이트 + content-type 반환 (이미지/첨부 프록시용). path 가 http(s) 절대 URL 이면 그대로 사용.
        return: (bytes, content_type). 인증(쿠키/헤더)은 provider 세션을 그대로 상속."""
        raise NotImplementedError

    def close(self):
        pass


class SessionExpired(RuntimeError):
    """401/403/5xx — 세션 만료 가능. (prod SSO 는 login 재실행 필요)"""


class LoginRequired(SessionExpired):
    """세션 파일이 아직 없음 — 최초 SSO 로그인이 필요. (SessionExpired 로 함께 처리됨)"""


class UpstreamError(SessionExpired):
    """상류 4xx/5xx — 진단용으로 status·응답 본문 미리보기를 담는다.
    401 이면 정말 세션 만료지만, 403 은 XSRF·권한, 그 외는 서버 문제일 수 있다."""

    def __init__(self, status, path, body=""):
        self.status = status
        self.body = (body or "")[:400]
        super().__init__(f"HTTP {status} on {path}" + (f" — {self.body}" if self.body else ""))

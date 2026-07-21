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


class AuthProvider:
    # 여러 스레드에서 동시 GET 가능한가 (basic/PAT=True, SSO=Playwright 단일 context=False)
    supports_parallel = False

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

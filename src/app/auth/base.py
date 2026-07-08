"""AuthProvider 인터페이스 — JiraClient 는 어떤 인증인지 몰라야 한다."""


class AuthProvider:
    # 여러 스레드에서 동시 GET 가능한가 (basic/PAT=True, SSO=Playwright 단일 context=False)
    supports_parallel = False

    def get_json(self, path, params=None):
        raise NotImplementedError

    def get_text(self, path, params=None):
        """ATOM/XML 응답용 (예: /activity 피드)."""
        raise NotImplementedError

    def close(self):
        pass


class SessionExpired(RuntimeError):
    """401/403/5xx — 세션 만료 가능. (prod SSO 는 login 재실행 필요)"""


class LoginRequired(SessionExpired):
    """세션 파일이 아직 없음 — 최초 SSO 로그인이 필요. (SessionExpired 로 함께 처리됨)"""

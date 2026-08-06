"""agent/tools/_ctx.py — 도구가 LTM 본체에 닿는 **단 하나의 창구**.

도구는 HTTP 왕복 없이 **LTM 내부 함수를 직접 부른다**(같은 프로세스). 그래서 캐시·무효화·
회로차단기·인증(AuthProvider)이 전부 그대로 재사용되고, prod SSO 로 바꿔도 도구는 손댈 게 없다.
자기 HTTP API 를 자기가 다시 부르는 건 같은 일을 두 번 하면서 세션만 복잡하게 만든다.

`app.main` 을 **함수 안에서** import 하는 이유 — main 이 에이전트 라우트를 import 하므로 모듈
최상단에서 부르면 순환이 된다. 도구가 실제로 도는 시점엔 main 이 이미 다 떠 있다.
"""

from __future__ import annotations

_bound = None       # 테스트 주입용. None 이면 app.main 의 싱글턴을 쓴다.


def bind(client=None, settings=None):
    """테스트가 클라이언트를 갈아끼운다. `bind()` 로 되돌린다."""
    global _bound
    _bound = None if client is None else (client, settings)


def client():
    if _bound:
        return _bound[0]
    from app.main import _client
    return _client


def settings():
    if _bound and _bound[1] is not None:
        return _bound[1]
    from app.infra.settings import get_settings
    return get_settings()


def jira_base() -> str:
    try:
        return (getattr(settings(), "jira_base_url", "") or "").rstrip("/")
    except Exception:
        return ""


# ── 출력 다이어트 ──────────────────────────────────────────────────
# 도구 결과는 **그대로 LLM 컨텍스트에 실린다**. 원본 Jira 이슈 하나가 4~8KB 라
# 20건이면 그것만으로 수만 토큰이다. 도구는 화면이 아니라 모델에게 말하는 것이므로
# 판단에 필요한 필드만 남기고, 긴 텍스트는 자른다.

def trim(s, n=400) -> str:
    """긴 본문을 자른다. 잘렸다는 사실을 남겨야 모델이 '더 봐야 하나'를 판단할 수 있다."""
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n] + f"…(총 {len(s)}자)"


def compact(d: dict) -> dict:
    """None·빈 값 제거 — 빈 필드는 모델에게 아무 정보도 주지 않으면서 토큰만 먹는다."""
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}

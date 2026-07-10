"""
Fake Jira/Confluence 서버 런처 (dev 용).
    python run_fake.py                      # :8080
    FAKE_LATENCY_MS=800 python run_fake.py  # 지연 주입(캐시 실측)
그 다음 앱: JIRA_ENV=local python run.py  (local → 이 서버로 붙음)

fake 서버는 외부 오픈소스 패키지 **jira820** 로 서빙하되, 이 프로젝트의 world 를 주입한다
(app/fakebridge.py). → mock == local 불변식 유지. (레거시 tools/fake_jira 는 폴백으로 유지)
"""
import os

import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("FAKE_PORT", "8080"))
    try:
        from app.fakebridge import build_injected_app
        app = build_injected_app()
        print("Fake Jira DC 8.20.8 (jira820, world 주입) - http://localhost:%d  (latency=%sms)"
              % (port, os.getenv("FAKE_LATENCY_MS", "0")))
    except ImportError:
        # jira820 미설치 시 레거시 번들 서버로 폴백
        app = "tools.fake_jira.server:app"
        print("Fake Jira DC 8.20.8 (legacy tools/fake_jira) - http://localhost:%d" % port)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")

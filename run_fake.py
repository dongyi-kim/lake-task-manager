"""
Fake Jira/Confluence 서버 런처 (dev, local env 용).
    python run_fake.py                      # :8080
    FAKE_LATENCY_MS=800 python run_fake.py  # 지연 주입(캐시 실측)
그 다음 앱: JIRA_ENV=local python run.py  (local → 이 서버로 붙음)

fake 서버 = 외부 오픈소스 패키지 **jira820** 에 이 프로젝트 world 를 주입(app/fakebridge.py).
mock 은 같은 jira820 을 in-process 로 소비(run_fake 불필요) → mock == local.
"""
import os

import uvicorn

from app.fakebridge import build_injected_app

if __name__ == "__main__":
    port = int(os.getenv("FAKE_PORT", "8080"))
    print("Fake Jira DC 8.20.8 (jira820, world 주입) - http://127.0.0.1:%d  (latency=%sms)"
          % (port, os.getenv("FAKE_LATENCY_MS", "0")))
    uvicorn.run(build_injected_app(), host="127.0.0.1", port=port, log_level="info")

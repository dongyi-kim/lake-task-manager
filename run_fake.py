"""
Fake Jira/Confluence 서버 런처 (dev 용).
    python run_fake.py                      # :8080
    FAKE_LATENCY_MS=800 python run_fake.py  # 지연 주입(캐시 실측)
그 다음 앱: JIRA_ENV=local python run.py  (local → 이 서버로 붙음)
"""
import os

import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("FAKE_PORT", "8080"))
    print("Fake Jira DC 8.20.8 - http://localhost:%d  (latency=%sms)"
          % (port, os.getenv("FAKE_LATENCY_MS", "0")))
    uvicorn.run("tools.fake_jira.server:app", host="127.0.0.1", port=port, log_level="info")

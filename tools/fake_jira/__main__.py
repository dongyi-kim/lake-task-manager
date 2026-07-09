"""python -m tools.fake_jira  → fake Jira 서버 기동 (:8080)."""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("tools.fake_jira.server:app",
                host=os.getenv("FAKE_HOST", "127.0.0.1"),
                port=int(os.getenv("FAKE_PORT", "8080")),
                log_level="info")

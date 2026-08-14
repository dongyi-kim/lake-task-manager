"""
로컬 dev 원클릭 런처 (local env) — fake Jira + 앱을 한 프로세스로.

    python run_local.py            # fake(:8080) 백그라운드 + 앱(local) hot reload
    FAKE_LATENCY_MS=800 python run_local.py   # fake 에 지연 주입(캐시 실측)

기존엔 터미널 2개가 필요했다:
    터미널1:  python run_fake.py                 # :8080 fake Jira
    터미널2:  JIRA_ENV=local python run.py --reload  # 앱 → fake

이 스크립트는 fake 서버(jira820 + world 주입)를 **같은 프로세스의 백그라운드 스레드**로 띄운 뒤
JIRA_ENV=local 로 앱을 실행한다. 앱은 여전히 :8080 으로 **실 HTTP** 를 태우므로(전송·인증·캐시 경로
검증) local 검증 의미는 그대로다. **Ctrl+C로 hot reload 앱과 fake 서버를 함께 종료**한다.

※ 그냥 mock(Jira 불필요, in-process)이면 `python run.py --reload`로 충분. 이건 실 HTTP 경로까지 태우는 local 용.
"""

import os
import sys
import threading
import time
import urllib.error
import urllib.request


def _start_fake(port):
    """fake Jira 서버(jira820 + world 주입)를 백그라운드 데몬 스레드로 기동.
    실제 HTTP 응답(4xx 포함=살아있음)까지 확인 후 반환 → 앱이 붙을 때 레이스 없음."""
    import uvicorn

    from app.mock.fakebridge import build_injected_app

    config = uvicorn.Config(build_injected_app(), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()

    probe = f"http://127.0.0.1:{port}/rest/api/2/serverInfo"
    for _ in range(300):                                  # 최대 ~30s
        if getattr(server, "started", False):
            try:
                urllib.request.urlopen(probe, timeout=1).read()
                return server                             # 200 → 준비됨
            except urllib.error.HTTPError:
                return server                             # 4xx 도 서버가 응답한 것 → 준비됨
            except Exception:
                pass
        time.sleep(0.1)
    return server


def main():
    fake_port = int(os.getenv("FAKE_PORT", "8080"))
    # get_settings() 가 읽기 전에 local 로 강제(환경변수가 config 보다 우선).
    os.environ["JIRA_ENV"] = "local"

    # fake 서버를 띄우기 전에 named launcher로 교체한다. 뒤에서 교체하면 첫 프로세스와
    # 새 프로세스가 잠시 :8080을 동시에 열어 local 기동이 간헐적으로 실패한다.
    from app.infra.process_identity import reexec_with_process_name
    reexec_with_process_name("local")

    print(f"[local] fake Jira DC 8.20.8 (jira820, world 주입) 기동 중 - http://localhost:{fake_port}"
          f"  (latency={os.getenv('FAKE_LATENCY_MS', '0')}ms)")
    _start_fake(fake_port)
    print("[local] fake 준비 완료 → 앱 실행 (env=local, hot reload, Ctrl+C로 함께 종료)")

    import run
    if "--reload" not in sys.argv[1:]:
        sys.argv.append("--reload")
    run.main()


if __name__ == "__main__":
    main()

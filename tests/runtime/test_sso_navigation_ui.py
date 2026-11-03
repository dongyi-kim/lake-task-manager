"""Real Chromium round trip against two local fake SSO domains; never corporate Jira."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from app.auth.base import background_upstream
from app.auth.sso_navigation import login_state, observe_login_page
from app.auth.sso_session import SsoSessionProvider
from app.auth.sso_store import SsoStore


def test_idp_redirect_cookie_survives_restart_and_renews_without_login_ui(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    forms = []
    navigations = []

    class FakeSSO(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, body=b"", **headers):
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name.replace("_", "-"), value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/":
                navigations.append(1)
            cookies = self.headers.get("Cookie", "")
            if path == "/rest/api/2/myself":
                if "JIRA=valid" in cookies:
                    self.reply(200, json.dumps({"name": "fixture-user"}).encode(),
                               Content_Type="application/json")
                else:
                    self.reply(401)
            elif path == "/sign-in":
                self.reply(302, Location=base + "/callback", Set_Cookie="SSO=valid; Path=/; HttpOnly")
            elif path == "/callback":
                self.reply(302, Location=base, Set_Cookie="JIRA=valid; Path=/; HttpOnly")
            elif path == "/login":
                if "SSO=valid" in cookies:
                    self.reply(302, Location=base + "/callback")
                else:
                    forms.append(1)
                    self.reply(200, b'<a href="/sign-in">Sign in</a>', Content_Type="text/html")
            elif "JIRA=valid" in cookies:
                self.reply(200, b"<title>Jira fixture</title>", Content_Type="text/html")
            else:
                self.reply(302, Location=idp + "/login")

    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeSSO)
    base = f"http://127.0.0.1:{server.server_port}"
    idp = f"http://localhost:{server.server_port}"
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    store = SsoStore(str(tmp_path / "session.json"), {"jira": base})
    provider = None
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except playwright.Error as exc:
                if "Executable doesn't exist" in str(exc):
                    pytest.skip("Optional Chromium binary is not installed in this environment")
                raise
            try:
                context = browser.new_context()
                page = context.new_page()
                observe_login_page(page)
                page.goto(base)
                page.get_by_role("link", name="Sign in").click()
                page.wait_for_url(base + "/")
                assert page.title() == "Jira fixture"
                store.save("jira", login_state(context))
            finally:
                browser.close()
        assert {c["name"] for c in store.merged()["cookies"]} == {"JIRA", "SSO"}
        assert idp in json.loads(store.path("identity").read_text())["authOrigins"]
        # Expire only Jira, retaining the separate IdP session across a provider/browser restart.
        saved = json.loads(store.path("jira").read_text())
        saved["cookies"][0]["value"] = "expired"
        store.save("jira", saved)
        provider = SsoSessionProvider(base, store)
        with background_upstream():
            assert provider.renew_silent([(base, ["/rest/api/2/myself"])],
                                         save_cb=lambda state: store.save("jira", state))
        assert provider.get_json("/rest/api/2/myself")["name"] == "fixture-user"
        navigation_count = len(navigations)
        with background_upstream():
            assert provider.renew_silent([(base, ["/rest/api/2/myself"])])
        assert len(navigations) == navigation_count  # A live provider needs no new login navigation.
        assert forms == [1]  # Only the first manual login; restart recovery never displayed a form.
        assert json.loads(store.path("jira").read_text())["cookies"][0]["value"] == "valid"
    finally:
        if provider is not None:
            provider.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)

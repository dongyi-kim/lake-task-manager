"""Exercise the native Playwright response store, without Chromium or external APIs."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from app.auth.base import UpstreamUnavailable
from app.auth.sso_session import SsoSessionProvider


def test_repeated_sso_reads_release_native_response_bodies():
    api = pytest.importorskip("playwright.sync_api")
    payload = json.dumps({"issues": [], "padding": "x" * (128 * 1024)}).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(503 if self.path == "/unavailable" else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with api.sync_playwright() as playwright:
            request = playwright.request.new_context()
            responses = []

            def get(*args, **kwargs):
                response = request.get(*args, **kwargs)
                responses.append(response)
                return response

            provider = object.__new__(SsoSessionProvider)
            provider.base = "http://127.0.0.1:%s" % server.server_port
            provider._context = SimpleNamespace(request=SimpleNamespace(get=get))
            try:
                for index in range(64):
                    if index % 8 == 0:
                        with pytest.raises(UpstreamUnavailable):
                            provider._fetch("/unavailable", None, False)
                    elif index % 3 == 0:
                        assert provider._fetch_bytes("/attachment", None)[0] == payload
                    elif index % 3 == 1:
                        assert provider._fetch("/search", None, False)["issues"] == []
                    else:
                        assert provider._fetch("/document", None, True) == payload.decode()
                # A Python response going out of scope does NOT release the native body.
                # Verify release before closing the long-lived authenticated context.
                retained = 0
                for response in responses:
                    try:
                        retained += len(response.body())
                    except api.Error as error:
                        assert "disposed" in str(error).lower()
                assert retained == 0, f"Native response bodies retained {retained} bytes"
                assert provider._fetch("/still-alive", None, False)["issues"] == []
            finally:
                request.dispose()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

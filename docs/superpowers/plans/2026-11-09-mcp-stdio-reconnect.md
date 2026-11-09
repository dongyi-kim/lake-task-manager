# Claude Desktop stdio resilience plan

**Goal:** A lost LTM HTTP response must fail that request explicitly without killing
Claude Desktop's stdio server or automatically replaying a business operation.

**Evidence:** Current stdio.py lets an HTTP transport exception terminate the SDK
transport task group, then exits its process. Restarting LTM cannot resurrect the
already-exited child that Claude Desktop owns. The user confirms Desktop and a
persistent server-disconnected error, but the actual production logs are unavailable.

**Approach:** Retain the pinned SDK's protocol, streaming, cancellation, parallel
request and profile handling. Contain expected HTTP/network failures at its individual
POST boundary and return a sanitized JSON-RPC request error with the original ID.
The HTTP client can open a new connection on the next request. No hidden POST retry,
extra Jira request, app auto-start or mock fallback. A lost write response remains
unknown, even if the backend is healthy again.

- [x] Real stdio regression: commit one synthetic Jira mutation, drop its HTTP response,
  then verify the same stdio session can list/read tools and the mutation ran once.
- [x] Regression for backend unavailable then returning at the same URL/token, using
  the same Desktop-owned stdio process and session.
- [x] Minimal request-failure adapter around the existing SDK transport; preserve
  EOF/cancellation shutdown and never print exception repr/headers/body/token.
- [x] Focused checks: stdio11 passed36.53s; authentication/profiles39 passed46.15s.
  After adding concurrent pending-read coverage and conservative error metadata,
  resilience3 passed26.15s. Unknown execution has retryable=false, retryReadOnly=true.
- [x] Independent Sol/high review: conservative retry metadata finding fixed;
  no remaining important/critical finding. Reviewer did not independently run tests.
- [ ] Full offline CI, separate from backend recovery.

An older proxy that has already exited still requires one full Claude Desktop
restart after installing the fix. This plan does not claim the user's current
production connection has been directly repaired or inspected.

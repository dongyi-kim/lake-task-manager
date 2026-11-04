"""Coalesce explicit interactive logins without making a request wait for human input."""

import threading
import time


class LoginRequests:
    """Track login attempts, not authentication truth; callers must probe after completion."""

    def __init__(self):
        self._lock = threading.Lock()
        self._attempts = {}
        self._next_id = 0
        self.requested = threading.Event()

    def status(self, service):
        with self._lock:
            state = dict(self._attempts.get(service) or {"state": "idle"})
        return {"service": service, "pending": state["state"] in ("queued", "running"), **state}

    def request(self, service, dispatch):
        with self._lock:
            previous = self._attempts.get(service) or {}
            if previous.get("state") in ("queued", "running"):
                return {"service": service, "pending": True, "started": False, **previous}
            self._next_id += 1
            self._attempts[service] = {"state": "queued", "startedAt": time.time(),
                                       "attemptId": self._next_id}
            attempt_id = self._next_id
            self.requested.set()
        try:
            dispatch(service)
        except Exception:
            self.complete(service, False, attempt_id)
            raise
        return {**self.status(service), "started": True}

    def claim(self, service=None):
        """Claim one queued attempt on its browser-owning thread (or fallback worker)."""
        with self._lock:
            for name, state in self._attempts.items():
                if state["state"] == "queued" and (service is None or name == service):
                    state["state"] = "running"
                    if not any(item["state"] == "queued" for item in self._attempts.values()):
                        self.requested.clear()
                    return name
        return None

    def complete(self, service, succeeded, attempt_id=None):
        with self._lock:
            state = self._attempts.get(service)
            if state and attempt_id is not None and state["attemptId"] != attempt_id:
                return
            if state and state["state"] in ("queued", "running"):
                state.update(state="completed", succeeded=bool(succeeded), completedAt=time.time())
                if not any(item["state"] == "queued" for item in self._attempts.values()):
                    self.requested.clear()

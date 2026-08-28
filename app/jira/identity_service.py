"""User directory and Jira session-state behavior for JiraClient."""

import threading
import time

from app.auth.base import SessionExpired, UpstreamUnavailable
from app.domain.names import real_name


class JiraIdentityMixin:
    """Identity-facing JiraClient methods; the owner supplies cache and provider state."""

    USER_TTL = 6 * 3600
    UPSTREAM_DOWN_FOR = 20
    SESSION_RECHECK_EVERY = 8

    def _display_name(self, user_id):
        """Return Jira's full display name, caching successful directory lookups only."""
        cache_key = f"user:{self.env}:{user_id}"
        hit = self.cache.get(cache_key)
        if hit is not None:
            return hit
        try:
            raw = self.provider.get_json("/rest/api/2/user", params={"username": user_id})
            display_name = raw.get("displayName")
        except Exception:
            display_name = None
        if display_name:
            self.cache.set(cache_key, display_name, self.USER_TTL)
            return display_name
        return user_id

    def display_name_cached(self, user_id):
        """Return a cached display name without initiating an upstream request."""
        return self.cache.get(f"user:{self.env}:{user_id}")

    def _mention_name(self, user_id):
        return real_name(self._display_name(user_id)) or user_id

    def user_badge(self, user_id):
        """Resolve one exact Jira user for mention hover content."""
        user_id = str(user_id or "").strip()
        if not user_id:
            return None
        cache_key = f"userbadge:{self.env}:{user_id.lower()}"
        hit = self.cache.get(cache_key)
        if hit is not None:
            return hit
        try:
            raw = self.provider.get_json("/rest/api/2/user", params={"username": user_id}) or {}
        except Exception:
            return None
        actual = raw.get("name") or raw.get("key")
        if not actual or str(actual).lower() != user_id.lower():
            return None
        display_name = raw.get("displayName") or actual
        result = {
            "id": actual,
            "username": actual,
            "name": real_name(display_name) or actual,
            "displayName": display_name,
            "avatar": "/api/avatar/" + actual,
        }
        self.cache.set(cache_key, result, self.USER_TTL)
        return result

    def upstream_down(self):
        return time.time() < getattr(self, "_upstream_down_until", 0)

    def mark_upstream_down(self, reason=""):
        self._upstream_down_until = time.time() + self.UPSTREAM_DOWN_FOR
        self._upstream_reason = reason or "상류 응답 없음"

    def mark_session_dead(self, reason=""):
        self._session_dead = True
        self._session_recheck_at = 0.0
        self.mark_upstream_down(reason)

    def mark_upstream_ok(self):
        self._upstream_down_until = 0
        self._upstream_reason = ""
        self._session_dead = False

    def session_recheck_async(self):
        """Probe a dead session in the background without blocking status polling."""
        needs_recheck = getattr(self, "_session_dead", False) or self.upstream_down()
        if self.env != "prod" or not needs_recheck:
            return
        now = time.time()
        if now < getattr(self, "_session_recheck_at", 0) or getattr(self, "_session_rechecking", False):
            return
        self._session_recheck_at = now + self.SESSION_RECHECK_EVERY
        self._session_rechecking = True

        def run():
            try:
                if self.session_alive():
                    self.mark_upstream_ok()
            except Exception:
                pass
            finally:
                self._session_rechecking = False

        threading.Thread(target=run, name="session-recheck", daemon=True).start()

    def upstream_state(self):
        return {
            "down": self.upstream_down(),
            "reason": getattr(self, "_upstream_reason", ""),
            "lastSyncAt": self.cache.last_upstream_ok or None,
            "servedStaleAt": self.cache.served_stale_at or None,
            "hasCache": self.cache.has_any(),
        }

    def current_user(self):
        """Return the authenticated Jira user, caching successful responses only."""

        def produce():
            raw = self.provider.get_json("/rest/api/2/myself")
            return {
                "id": raw.get("name") or raw.get("key") or "",
                "name": real_name(raw.get("displayName") or raw.get("name")) or "",
                "display": raw.get("displayName") or raw.get("name") or "",
                "timezone": raw.get("timeZone") or "Asia/Seoul",
            }

        try:
            return self.cache.get_or_set(
                f"myself:{self.env}", self.USER_TTL, produce,
            )[0]
        except UpstreamUnavailable as exc:
            self.mark_upstream_down(str(exc))
            return {}
        except SessionExpired as exc:
            self.mark_session_dead(str(exc))
            return {}
        except Exception:
            return {}

    def session_alive(self):
        """Verify the session directly; Jira may return an anonymous object with HTTP 200."""
        try:
            raw = self.provider.get_json("/rest/api/2/myself")
            if not isinstance(raw, dict):
                return False
            name = raw.get("name") or raw.get("key") or ""
            return bool(name and "anonymous" not in str(name).lower())
        except Exception:
            return False

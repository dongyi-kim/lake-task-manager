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
    PROACTIVE_PROBE_CACHE_SEC = 20
    SESSION_STATE_PERSIST_EVERY = 15 * 60

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
        # A recent proactive "alive" result must not mask a later confirmed Jira 401.
        self._auth_probe_result = None
        self._auth_probe_at = 0.0
        self.mark_upstream_down(reason)

    def mark_transport_ok(self):
        """Close only the transient upstream circuit breaker.

        Cache producers are shared by Jira, Confluence and best-effort aggregate builders.  A
        successful producer therefore proves that *some* upstream work completed, not that the
        Jira login is alive.  Clearing ``_session_dead`` here caused an unrelated cache refresh to
        turn a confirmed Jira 401 into a false ``auth-ok``.
        """
        self._upstream_down_until = 0
        self._upstream_reason = ""

    def mark_session_alive(self):
        """Record a successful direct Jira identity check or completed login."""
        self.mark_transport_ok()
        self._session_dead = False
        # Do not replay a briefly memoized needLogin result after another direct /myself (for
        # example the status rechecker) has already proved the session recovered.
        self._auth_probe_result = None
        self._auth_probe_at = 0.0

    def mark_upstream_ok(self):
        """Backward-compatible transport-success name; it must not assert Jira identity."""
        self.mark_transport_ok()

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
                    self.mark_session_alive()
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
            user_id = raw.get("name") or raw.get("key") or raw.get("accountId") or ""
            # Some Jira/SSO front doors answer 200 with an anonymous identity.  Treating that as
            # a successful producer would cache a false login for six hours and clear auth-dead.
            if not user_id or "anonymous" in str(user_id).lower():
                raise SessionExpired("익명 응답 — Jira 세션이 만료되었습니다.")
            aliases = sorted({
                str(raw.get(field) or "").strip().casefold()
                for field in ("name", "key", "accountId")
                if str(raw.get(field) or "").strip()
            })
            return {
                "id": user_id,
                "name": real_name(raw.get("displayName") or raw.get("name")) or "",
                "display": raw.get("displayName") or raw.get("name") or "",
                "timezone": raw.get("timeZone") or "Asia/Seoul",
                # Recovery compares Jira's author/uploader/creator object. Different Jira DC
                # endpoints expose different identifiers (name, key, or accountId), so retain all
                # authenticated aliases rather than guessing that one field is universal.
                "actorAliases": aliases,
            }

        try:
            result, hit = self.cache.get_or_set(
                f"myself:{self.env}", self.USER_TTL, produce,
            )
            # Only a freshly completed /myself call proves that Jira authentication recovered;
            # a cached identity does not.
            if not hit:
                self.mark_session_alive()
            return result
        except UpstreamUnavailable as exc:
            self.mark_upstream_down(str(exc))
            return {}
        except SessionExpired as exc:
            self.mark_session_dead(str(exc))
            return {}
        except Exception:
            return {}

    def mutation_actor_identity(self):
        """Return a stable authenticated actor fingerprint for idempotent Jira creates.

        This must run before a pending receipt is written. If identity cannot be established, the
        mutation is not sent: without an actor, reconciliation could mistake another person's
        simultaneous same-body comment, same-name attachment, or same-fields issue for ours.
        """
        current = self.current_user() or {}
        actor_id = str(current.get("id") or "").strip()
        aliases = sorted({
            str(value or "").strip().casefold()
            for value in (current.get("actorAliases") or [actor_id])
            if str(value or "").strip()
        })
        if actor_id and aliases:
            return {"id": actor_id, "aliases": aliases}
        if self.needs_login():
            raise SessionExpired("Jira 사용자 확인이 필요합니다. 로그인 후 다시 시도해 주세요.")
        raise UpstreamUnavailable(
            "Jira 사용자 정보를 확인하지 못해 중복 방지를 보장할 수 없습니다. 잠시 후 다시 시도해 주세요.")

    def assert_mutation_actor_identity(self, expected):
        """Freshly prove a response-loss retry still runs as the receipt's original actor."""
        raw = self.provider.get_json("/rest/api/2/myself") or {}
        aliases = {
            str(raw.get(field) or "").strip().casefold()
            for field in ("name", "key", "accountId")
            if str(raw.get(field) or "").strip()
        }
        wanted = {
            str(value or "").strip().casefold()
            for value in ((expected or {}).get("aliases") or [])
            if str(value or "").strip()
        }
        if not aliases or any("anonymous" in value for value in aliases):
            raise SessionExpired("Jira 사용자 확인이 필요합니다. 로그인 후 다시 시도해 주세요.")
        if not wanted or not (wanted & aliases):
            raise ValueError(
                "Jira 로그인 사용자가 이전 요청과 다릅니다. 원래 사용자로 로그인해 결과를 확인해 주세요.")
        self.mark_session_alive()
        return True

    def direct_session_state(self, *, background=False):
        """Return ``alive``, ``expired`` or ``unknown`` from a direct Jira probe.

        A transport stall is not proof that the SSO session expired.  The old boolean helper
        collapsed both cases to ``False``; callers could consequently open a login window after
        an ordinary network hiccup.  Keep the tri-state result until the UI-facing boundary.
        """
        try:
            if background:
                from app.auth.base import PRIO_BACKGROUND, _prio_scope
                with _prio_scope(PRIO_BACKGROUND):
                    raw = self.provider.get_json("/rest/api/2/myself")
            else:
                raw = self.provider.get_json("/rest/api/2/myself")
            if not isinstance(raw, dict):
                return "unknown"
            name = raw.get("name") or raw.get("key") or ""
            if name and "anonymous" not in str(name).lower():
                self.mark_session_alive()
                if background:
                    # The activity probe is already off the render path. Persist rolling/sliding
                    # cookies here so a later browser relaunch or app restart does not fall back
                    # to the older login-time state. Persistence failure never changes auth truth.
                    try:
                        self._persist_jira_session_state()
                    except Exception:
                        pass
                return "alive"
            return "expired"
        except SessionExpired:
            return "expired"
        except Exception:
            return "unknown"

    def session_alive(self):
        """Backward-compatible boolean session check."""
        return self.direct_session_state() == "alive"

    def _persist_jira_session_state(self):
        """Persist current Jira cookies at most once per bounded interval.

        The provider captures its BrowserContext on its owner thread.  Two fences prevent an old
        snapshot from overwriting a newer visible/silent login: provider object+generation under
        ``_provider_lock``, and SsoStore's atomic disk revision compare-and-save.
        """
        now = time.monotonic()
        last = getattr(self, "_session_state_persist_at", 0.0)
        if last and now - last < self.SESSION_STATE_PERSIST_EVERY:
            return False
        lock = self._session_state_persist_lock
        if not lock.acquire(blocking=False):
            return False
        try:
            now = time.monotonic()
            last = getattr(self, "_session_state_persist_at", 0.0)
            if last and now - last < self.SESSION_STATE_PERSIST_EVERY:
                return False
            provider = self.provider
            capture = getattr(provider, "storage_state_snapshot", None)
            if not capture:
                return False
            generation = getattr(self, "_provider_generation", 0)
            store = self.sso_store()
            revision = store.revision("jira")
            state = capture()
            if not isinstance(state, dict) or "cookies" not in state:
                return False
            with self._provider_lock:
                if (provider is not self._provider
                        or generation != getattr(self, "_provider_generation", 0)
                        or not self._provider_built
                        or getattr(provider, "broken", False)):
                    return False
                saved = store.save_if_unchanged("jira", state, revision)
            if saved:
                self._session_state_persist_at = time.monotonic()
            return bool(saved)
        finally:
            lock.release()

    def proactive_auth_probe(self):
        """Check Jira on user return and silently renew SSO when possible.

        This method is called by a background browser request, never by the critical render path.
        It is prod-only, single-flight and briefly memoized so focus/visibility/pointer events (or
        two open LTM tabs) cannot flood the serial Playwright provider or open repeated logins.
        """
        if self.env != "prod":
            return {"ok": True, "needLogin": False, "skipped": True, "mode": "local"}

        now = time.monotonic()
        recent = getattr(self, "_auth_probe_result", None)
        if recent is not None and now - getattr(self, "_auth_probe_at", 0.0) < self.PROACTIVE_PROBE_CACHE_SEC:
            return {**recent, "cached": True}

        lock = self._auth_probe_lock
        # A second browser request waits for, and then reuses, the first probe.  It never starts a
        # second Jira call.  The HTTP fetch is background work, so this does not block the UI.
        if not lock.acquire(timeout=35):
            # The earlier confirmed failure already started the existing auth watcher.  A probe
            # that merely timed out must not re-emit need-login or open another visible flow.
            return {"ok": False, "needLogin": False, "pending": True,
                    "mode": "checking"}
        try:
            now = time.monotonic()
            recent = getattr(self, "_auth_probe_result", None)
            if recent is not None and now - getattr(self, "_auth_probe_at", 0.0) < self.PROACTIVE_PROBE_CACHE_SEC:
                return {**recent, "cached": True}

            state = self.direct_session_state(background=True)
            if state == "alive":
                self.mark_session_alive()
                result = {"ok": True, "needLogin": False, "mode": "ok"}
            elif state == "unknown":
                # Network/provider trouble must not become an authentication popup.
                result = {"ok": False, "needLogin": False, "mode": "degraded"}
            else:
                self.mark_session_dead("유휴 후 Jira 세션 만료")
                try:
                    renewed = self._renew_service("Jira")
                except Exception:
                    renewed = False
                recovered = renewed and self.direct_session_state(background=True) == "alive"
                if recovered:
                    self.mark_session_alive()
                    try:
                        self.cache.invalidate(f"myself:{self.env}")
                    except Exception:
                        pass
                    result = {"ok": True, "needLogin": False, "recovered": True,
                              "mode": "ok"}
                else:
                    result = {"ok": False, "needLogin": True, "mode": "authenticating"}

            self._auth_probe_result = result
            self._auth_probe_at = time.monotonic()
            return dict(result)
        finally:
            lock.release()

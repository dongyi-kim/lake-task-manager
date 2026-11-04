"""User directory and Jira session-state behavior for JiraClient."""

import threading
import time

from app.auth.base import SessionExpired, UpstreamBusy, UpstreamError, UpstreamUnavailable
from app.domain.names import real_name


class JiraIdentityMixin:
    """Identity-facing JiraClient methods; the owner supplies cache and provider state."""

    USER_TTL = 6 * 3600
    UPSTREAM_DOWN_FOR = 20
    SESSION_RECHECK_EVERY = 8
    PROACTIVE_PROBE_CACHE_SEC = 20
    AUTH_PROBE_WAIT_SECONDS = 30
    SESSION_STATE_PERSIST_EVERY = 4 * 60

    def _display_name(self, user_id):
        """Keep useful names visible until display expiry; refresh once when stale."""
        cache_key = f"user:{self.env}:{user_id}"
        def fetch():
            raw = self.provider.get_json("/rest/api/2/user", params={"username": user_id})
            name = raw.get("displayName")
            if not name:
                raise ValueError("Incomplete Jira user response")
            return name
        try:
            return self.cache.get_or_set(cache_key, self.USER_TTL, fetch)[0]
        except Exception:
            return user_id

    def display_name_cached(self, user_id):
        got = self.cache.get_alive(f"user:{self.env}:{user_id}")
        return got[0] if got else None

    def _mention_name(self, user_id):
        return real_name(self._display_name(user_id)) or user_id

    def user_badge(self, user_id, *, strict_auth=False):
        """Keep UI lookups best-effort; expose authentication/transport failures to MCP."""
        user_id = str(user_id or "").strip()
        if not user_id:
            return None
        cache_key = f"userbadge:{self.env}:{user_id.lower()}"
        def fetch():
            raw = self.provider.get_json("/rest/api/2/user", params={"username": user_id}) or {}
            actual = raw.get("name") or raw.get("key")
            if not actual or str(actual).lower() != user_id.lower():
                raise ValueError("Incomplete Jira user response")
            display_name = raw.get("displayName") or actual
            return {"id": actual, "username": actual, "name": real_name(display_name) or actual,
                    "displayName": display_name, "avatar": "/api/avatar/" + actual}
        try:
            return self.cache.get_or_set(cache_key, self.USER_TTL, fetch)[0]
        except Exception as exc:
            if strict_auth and isinstance(exc, UpstreamUnavailable):
                raise
            if strict_auth and isinstance(exc, SessionExpired) and (
                    not isinstance(exc, UpstreamError) or exc.status == 401):
                raise
            return None

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
        self._auth_probe_completed_result = None
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
                self.proactive_auth_probe()
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
            "authMaintenance": dict(getattr(self, "_auth_maintenance", {})),
        }

    def _fetch_current_user(self):
        raw = self.provider.get_json("/rest/api/2/myself")
        user_id = raw.get("name") or raw.get("key") or raw.get("accountId") or ""
        # Anonymous 200 responses cannot prove login or become a six-hour identity cache entry.
        if not user_id or "anonymous" in str(user_id).lower():
            raise SessionExpired("익명 응답 — Jira 세션이 만료되었습니다.")
        aliases = sorted({str(raw.get(field) or "").strip().casefold()
                          for field in ("name", "key", "accountId") if str(raw.get(field) or "").strip()})
        return {"id": user_id, "name": real_name(raw.get("displayName") or raw.get("name")) or "",
                "display": raw.get("displayName") or raw.get("name") or "",
                "timezone": raw.get("timeZone") or "Asia/Seoul", "actorAliases": aliases}

    def current_user_for_authorization(self):
        """Authoritative identity only: display-cache fallback must never grant permissions."""
        if getattr(self, "_session_dead", False) or self.needs_login():
            raise SessionExpired("Jira 인증을 확인한 후 설정에 접근해 주세요.")
        try:
            result, hit = self.cache.get_or_set_strict(
                f"myself:{self.env}", self.USER_TTL, self._fetch_current_user)
        except SessionExpired as exc:
            self.mark_session_dead(str(exc))
            raise
        except UpstreamBusy:
            raise
        except UpstreamUnavailable as exc:
            self.mark_upstream_down(str(exc))
            raise
        if not result or not result.get("id"):
            raise SessionExpired("Jira 사용자를 확인할 수 없습니다.")
        if not hit:
            self.mark_session_alive()
        return result

    def current_user(self):
        """Return the authenticated Jira user, caching successful responses only."""

        try:
            result, hit = self.cache.get_or_set(
                f"myself:{self.env}", self.USER_TTL, self._fetch_current_user,
            )
            # Only a freshly completed /myself call proves that Jira authentication recovered;
            # a cached identity does not.
            if not hit:
                self.mark_session_alive()
            return result
        except UpstreamBusy:
            # Capacity says nothing about the authenticated identity. Propagate it rather than
            # feeding an empty identity to a parent cache producer or opening the transport gate.
            raise
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

        Local admission pressure raises ``UpstreamBusy``: no upstream observation was made.
        A transport stall is not proof that the SSO session expired.  The old boolean helper
        collapsed both cases to ``False``; callers could consequently open a login window after
        an ordinary network hiccup.  Keep the tri-state result until the UI-facing boundary.
        """
        # Provider construction itself can raise LoginRequired before there is an instance.
        # Capture its fence first so an unchanged missing session is expired, while a login
        # that replaces the provider during construction still makes this result obsolete.
        provider, generation = self._provider, self._provider_generation
        try:
            provider = self.provider
            generation = self._provider_generation
            if background:
                from app.auth.base import PRIO_BACKGROUND, _prio_scope
                with _prio_scope(PRIO_BACKGROUND):
                    raw = provider.get_json("/rest/api/2/myself")
            else:
                raw = provider.get_json("/rest/api/2/myself")
            with self._provider_lock:
                if provider is not self._provider or generation != self._provider_generation:
                    return "unknown"
                if not isinstance(raw, dict):
                    return "unknown"
                name = raw.get("name") or raw.get("key") or ""
                if not name or "anonymous" in str(name).lower():
                    return "expired"
                self.mark_session_alive()
            if background:
                # Persist rolling cookies off the render path. A failed disk write never changes
                # authentication truth; the store separately fences replacement login snapshots.
                try:
                    self._persist_jira_session_state()
                except Exception:
                    pass
            return "alive"
        except SessionExpired:
            if provider is not self._provider or generation != self._provider_generation:
                return "unknown"
            return "expired"
        except UpstreamBusy:
            raise
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

    def proactive_auth_probe(self, *, force=False, wait=False):
        """Check Jira on user return and silently renew SSO when possible.

        Default UI/timer calls reuse recent results and return pending without waiting for an
        active check. MCP recovery may force a new check after an authentication error and wait
        up to AUTH_PROBE_WAIT_SECONDS for an already-running check. Overlapping forced callers
        share that completed result; a wait timeout stays pending, never login-required. No
        path opens a login window. A later independent forced call bypasses the recent memo.
        """
        if self.env != "prod":
            return {"ok": True, "needLogin": False, "skipped": True, "mode": "local"}

        completed_before = getattr(self, "_auth_probe_completed_result", None)
        now = time.monotonic()
        recent = getattr(self, "_auth_probe_result", None)
        if not force and recent is not None and now - getattr(self, "_auth_probe_at", 0.0) < self.PROACTIVE_PROBE_CACHE_SEC:
            return {**recent, "cached": True}

        lock = self._auth_probe_lock
        # Foreground recovery may join this gate, while UI/timer work remains nonblocking.
        if not lock.acquire(timeout=self.AUTH_PROBE_WAIT_SECONDS if wait else 0):
            # The earlier confirmed failure already started the existing auth watcher.  A probe
            # that merely timed out must not re-emit need-login or open another visible flow.
            return {"ok": False, "needLogin": False, "pending": True,
                    "mode": "checking", **({"timedOut": True} if wait else {})}
        try:
            completed = getattr(self, "_auth_probe_completed_result", None)
            if completed is not None and completed is not completed_before:
                # Do not run N serial forced probes after N callers waited for one renewal.
                # Keep this completion separate from the short-lived memo cleared by successful
                # identity reads. A later confirmed auth failure clears both forms of reuse.
                return {**completed, "shared": True}
            now = time.monotonic()
            recent = getattr(self, "_auth_probe_result", None)
            if not force and recent is not None and now - getattr(self, "_auth_probe_at", 0.0) < self.PROACTIVE_PROBE_CACHE_SEC:
                return {**recent, "cached": True}

            from app.auth.base import background_upstream
            self._auth_maintenance.update(state="checking", lastAttemptAt=time.time())
            with background_upstream():
                state = self.direct_session_state(background=True)
            if state == "alive":
                self.mark_session_alive()
                result = {"ok": True, "needLogin": False, "mode": "ok"}
            elif state == "unknown":
                # Network/provider trouble must not become an authentication popup.
                result = {"ok": False, "needLogin": False, "mode": "degraded"}
            else:
                self._auth_maintenance["state"] = "renewing"
                try:
                    with background_upstream():
                        self._renew_service("Jira")
                except UpstreamBusy:
                    raise
                except Exception:
                    pass
                # A failed navigation may be a network loss, or another login may have completed.
                # Confirm again before announcing that user interaction is required.
                after = self.direct_session_state(background=True)
                recovered = after == "alive"
                if recovered:
                    self.mark_session_alive()
                    try:
                        self.cache.invalidate(f"myself:{self.env}")
                    except Exception:
                        pass
                    result = {"ok": True, "needLogin": False, "recovered": True,
                              "mode": "ok"}
                    self._auth_maintenance["lastRenewedAt"] = time.time()
                elif after == "unknown":
                    result = {"ok": False, "needLogin": False, "mode": "degraded"}
                else:
                    self.mark_session_dead("Jira 세션 만료 — 무음 인증 복구 필요")
                    result = {"ok": False, "needLogin": True, "mode": "authenticating"}

            self._auth_maintenance.update(
                state=("alive" if result["ok"] else "login_required" if result["needLogin"] else "degraded"),
                lastCheckedAt=time.time(), nextCheckAt=time.time() + self.KEEPALIVE_EVERY,
                failure=None if result["ok"] else "authentication" if result["needLogin"] else "transport",
            )
            self._auth_probe_result = result
            self._auth_probe_at = time.monotonic()
            self._auth_probe_completed_result = dict(result)
            return dict(result)
        except UpstreamBusy:
            # Admission refusal is not a completed auth/transport observation. In particular,
            # do not memoize it (or replay an older degraded memo) once capacity becomes available.
            self._auth_probe_result = None
            self._auth_probe_at = 0.0
            self._auth_probe_completed_result = None
            self._auth_maintenance.update(state="checking", failure=None)
            return {"ok": False, "needLogin": False, "pending": True,
                    "retryable": True, "mode": "checking"}
        finally:
            lock.release()

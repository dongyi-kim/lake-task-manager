"""Fresh execution context for browser bulk-create drafts and their POST fence."""

from app.auth.base import SessionExpired
from app.services.ticket_models import BulkBody


class BulkCreateBody(BulkBody):
    # Optional for existing HTTP clients; the browser always sends its immutable context.
    expectedScope: dict[str, str] | None = None


class BulkCreateScopeChanged(ValueError):
    pass


class BulkCreateFence:
    """A batch never adopts a replacement provider/actor during validation or dispatch."""

    def __init__(self, client, expected):
        self.client, self.expected = client, dict(expected)
        self.provider = client.provider
        self.generation = client._provider_generation
        if bulk_create_context(client) != self.expected:
            self.reject()
        self.assert_current()

    def reject(self):
        raise BulkCreateScopeChanged(
            "작성 중 Jira 연결·프로젝트·사용자가 변경됐습니다. 원래 연결로 돌아온 뒤 결과를 확인하세요.")

    def assert_current(self):
        client = self.client
        if (client._provider is not self.provider or client._provider_generation != self.generation
                or client.env != self.expected["environment"]
                or (client.s.jira_base or "").rstrip("/") != self.expected["jiraBase"]
                or client.s.project_key != self.expected["projectKey"]):
            self.reject()

    def provider_for_write(self):
        self.assert_current()
        raw = self.provider.get_json("/rest/api/2/myself") or {}
        actor = raw.get("name") or raw.get("key") or raw.get("accountId")
        if not actor:
            raise SessionExpired("Jira 사용자를 확인한 뒤 다시 시도해 주세요.")
        if str(actor) != self.expected["userId"]:
            self.reject()
        self.assert_current()
        # The caller posts through this captured provider, never self.client.provider again.
        # A concurrent reset can close it, but cannot redirect this write to the new login.
        return self.provider


def bulk_create_context(client):
    # /api/me is a display cache. A write must identify the current upstream actor.
    me = client._fetch_current_user()
    if not me or not me.get("id"):
        raise SessionExpired("Jira 사용자를 확인한 뒤 다시 시도해 주세요.")
    client.cache.set(f"myself:{client.env}", me, client.USER_TTL)
    return {"environment": client.env, "jiraBase": (client.s.jira_base or "").rstrip("/"),
            "projectKey": client.s.project_key, "userId": str(me["id"])}

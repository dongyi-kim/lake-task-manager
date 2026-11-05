"""Fresh execution context for browser bulk-create drafts and their POST fence."""

from app.services.ticket_models import BulkBody


class BulkCreateBody(BulkBody):
    # Optional for existing HTTP clients; the browser always sends its immutable context.
    expectedScope: dict[str, str] | None = None


class BulkCreateScopeChanged(ValueError):
    pass


class BulkCreateFence:
    """A batch never adopts a replacement provider during validation or dispatch."""

    def __init__(self, client, expected):
        self.client = client
        self.expected = {name: expected.get(name) for name in ("environment", "jiraBase", "projectKey")}
        self.provider = client.provider
        self.generation = client._provider_generation
        if bulk_create_context(client) != self.expected:
            self.reject()
        self.assert_current()

    def reject(self):
        raise BulkCreateScopeChanged(
            "작성 중 Jira 연결·프로젝트가 변경됐습니다. 원래 연결로 돌아온 뒤 결과를 확인하세요.")

    def assert_current(self):
        client = self.client
        if (client._provider is not self.provider or client._provider_generation != self.generation
                or client.env != self.expected["environment"]
                or (client.s.jira_base or "").rstrip("/") != self.expected["jiraBase"]
                or client.s.project_key != self.expected["projectKey"]):
            self.reject()

    def provider_for_write(self):
        self.assert_current()
        # The caller posts through this captured provider, never self.client.provider again.
        # A concurrent reset can close it, but cannot redirect this write to the new login.
        return self.provider


def bulk_create_context(client):
    """Local draft scope; write authentication and receipt reconciliation remain in ticket commands."""
    return {"environment": client.env, "jiraBase": (client.s.jira_base or "").rstrip("/"),
            "projectKey": client.s.project_key}

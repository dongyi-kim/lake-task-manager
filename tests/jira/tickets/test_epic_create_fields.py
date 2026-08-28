"""Epic 생성 field는 instance의 createmeta와 일치해야 한다.

custom field numeric id는 Jira마다 다르다. prod에서 설정의 customfield_10011은
`issueFunction`이었고 실제 Epic Name은 customfield_10018이라, 하드코딩된 payload가 Epic
생성 전체를 실패시켰다. 전역 `/field`가 아니라 project·issue type의 create screen을 검증한다.
"""
from types import SimpleNamespace

from app.infra.cache import Cache
from app.jira.jira_client import JiraClient


class _Provider:
    def __init__(self, fields):
        self.fields = fields
        self.posted = None

    def get_json(self, path, params=None, **_kwargs):
        if path == "/rest/api/2/issuetype":
            return [{"name": "Epic", "subtask": False}]
        if path == "/rest/api/2/issue/createmeta":
            assert params["projectKeys"] == "DL"
            assert params["issuetypeNames"] == "Epic"
            assert params["expand"] == "projects.issuetypes.fields"
            return {"projects": [{"key": "DL", "issuetypes": [
                {"name": "Epic", "fields": self.fields}
            ]}]}
        raise AssertionError(path)

    def post_json(self, path, payload):
        assert path == "/rest/api/2/issue"
        self.posted = payload
        return {"key": "DL-1"}


def _client(fields, configured="customfield_10011"):
    settings = SimpleNamespace(jira_env="prod", project_key="DL",
                               epic_name_field_id=configured)
    client = JiraClient(settings, Cache(":memory:"))
    provider = _Provider(fields)
    client._provider = provider
    client._provider_built = True
    return client, provider


def test_create_epic_replaces_wrong_configured_field_with_createmeta_field():
    client, provider = _client({
        "summary": {"name": "Summary", "schema": {"type": "string"}},
        "customfield_10011": {
            "name": "issueFunction",
            "schema": {"type": "string", "custom": "example:issue-function"},
        },
        "customfield_10018": {
            "name": "Epic Name",
            "schema": {"type": "string",
                       "custom": "com.pyxis.greenhopper.jira:gh-epic-label"},
        },
    })

    client.create_epic("검색 플랫폼 개선", epic_name="검색 개선")

    fields = provider.posted["fields"]
    assert fields["customfield_10018"] == "검색 개선"
    assert "customfield_10011" not in fields


def test_create_epic_omits_unverified_custom_field_in_prod():
    client, provider = _client({
        "summary": {"name": "Summary", "schema": {"type": "string"}},
        "customfield_10011": {
            "name": "issueFunction",
            "schema": {"type": "string", "custom": "example:issue-function"},
        },
    })

    client.create_epic("검색 플랫폼 개선", epic_name="검색 개선")

    assert not any(k.startswith("customfield_") for k in provider.posted["fields"])

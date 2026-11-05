"""Confluence command boundaries: CAS, receipts, service scope and attachment ownership."""

from copy import deepcopy
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from app.auth.base import MutationOutcomeUnknown, MutationPreflightUnavailable, UpstreamError, UpstreamUnavailable
from app.infra.cache import Cache
from app.jira import confluence_service as reads
from app.services.confluence_commands import ConfluenceCommands
from app.services.ticket_commands import CommandError


class ConfluenceFixture:
    """Small REST contract fixture, deliberately not a Synchrony emulator."""

    def __init__(self):
        self.items = {"10": {"id": "10", "type": "page", "status": "current", "title": "Parent",
                     "space": {"key": "DL"}, "version": {"number": 1}, "ancestors": [],
                     "body": {"storage": {"value": "<p>before</p>", "representation": "storage"}}}}
        self.versions, self.files = {}, {}
        self.gets, self.writes = [], []
        self.lose_response = self.fail_read = self.race = False

    def get_json(self, url, params=None):
        self.gets.append((url, params))
        if self.fail_read:
            self.fail_read = False
            raise UpstreamUnavailable("offline")
        path, params = urlsplit(url).path.removeprefix("/wiki/rest/api/"), params or {}
        if path == "content":
            rows = [item for item in self.items.values() if item.get("title") == params.get("title")]
            return {"results": deepcopy(rows)}
        parts = path.split("/")
        if len(parts) == 4 and parts[2] == "child":
            rows = [item for item in self.items.values()
                    if item.get("type") == parts[3] and (item.get("container") or {}).get("id") == parts[1]]
            return {"results": deepcopy(rows), "_links": {}}
        item = self.items.get(parts[1])
        if not item:
            raise UpstreamError(404, url)
        if params.get("version"):
            version = params["version"]
            item = self.versions.get((parts[1], version), item)
        return deepcopy(item)

    def _response(self, item):
        self.versions[(item["id"], item["version"]["number"])] = deepcopy(item)
        if self.lose_response:
            self.lose_response = False
            raise UpstreamUnavailable("response lost after commit")
        return deepcopy(item)

    def post_json(self, url, json_body=None, params=None):
        self.writes.append(("POST", url, json_body))
        if url.endswith("/label"):
            return {"results": deepcopy(json_body)}
        item = deepcopy(json_body)
        item["id"] = str(max(map(int, self.items)) + 1)
        item.setdefault("space", {"key": "DL"})
        item.setdefault("title", "")
        self.items[item["id"]] = item
        return self._response(item)

    def put_json(self, url, json_body=None, params=None):
        self.writes.append(("PUT", url, deepcopy(json_body)))
        current = self.items[url.rsplit("/", 1)[-1]]
        if self.race:
            self.race = False
            current["version"]["number"] += 1
        if json_body["version"]["number"] != current["version"]["number"] + 1:
            raise UpstreamError(409, url)
        current.update(deepcopy(json_body))
        return self._response(current)

    def delete(self, url, params=None):
        self.writes.append(("DELETE", url, params))
        if not url.endswith("/label"):
            self.items.pop(url.rsplit("/", 1)[-1], None)

    def post_multipart(self, url, filename, data, content_type=None, params=None):
        self.writes.append(("POST_FILE", url, filename))
        self.upload_params = params
        item = {"id": str(max(map(int, self.items)) + 1), "type": "attachment", "title": filename,
                "space": {"key": "DL"}, "container": {"id": "10"}, "version": {"number": 1},
                "extensions": {"fileSize": len(data)},
                "_links": {"download": "/download/attachments/10/" + filename}}
        self.items[item["id"]], self.files[filename] = item, data
        self._response(item)
        return {"results": [deepcopy(item)]}

    def get_bytes(self, url):
        self.gets.append((url, None))
        return self.files[urlsplit(url).path.rsplit("/", 1)[-1]], "image/png"


@pytest.fixture
def commands():
    provider, cache = ConfluenceFixture(), Cache(":memory:")
    settings = SimpleNamespace(confluence_base="https://docs.test/wiki", search_confluence_spaces=["DL"])
    client = SimpleNamespace(env="mock", s=settings, cache=cache, provider=provider,
                             _conf_get_json=provider.get_json)
    yield ConfluenceCommands(lambda: client, settings), provider
    cache.close()


def test_create_receipt_survives_lost_response_without_duplicate(commands):
    command, upstream = commands
    upstream.lose_response = True
    args = dict(space="DL", title="New", body="<p>data</p>", parent_id="10", mutation_id="create-0001")
    with pytest.raises(MutationOutcomeUnknown) as failed:
        command.create_page(**args)
    assert failed.value.service == "confluence"
    result = command.create_page(**args)
    assert result["id"] == "11"
    assert len(upstream.writes) == 1
    assert command.create_page(**args) == result
    assert len(upstream.writes) == 1


def test_update_cas_and_replay_do_not_recheck_old_precondition(commands):
    command, upstream = commands
    source = '<ac:structured-macro ac:name="info"><ac:rich-text-body><p>kept</p></ac:rich-text-body></ac:structured-macro>'
    args = dict(page_id="10", expected_version=1, body=source, mutation_id="update-0001")
    result = command.update_page(**args)
    assert result["version"] == 2 and result["draftState"] == "unknown"
    assert upstream.items["10"]["body"]["storage"]["value"] == source
    assert command.update_page(**args) == result
    assert len(upstream.writes) == 1
    with pytest.raises(ValueError, match="재사용"):
        command.update_page(**{**args, "body": "different"})


def test_mutation_invalidates_head_but_preserves_immutable_source(commands):
    command, upstream = commands
    assert reads.body(command.client, "10", 1)["body"]["storage"]["value"] == "<p>before</p>"
    before = deepcopy(upstream.items["10"])
    upstream.versions[("10", 1)] = before
    command.update_page(page_id="10", expected_version=1, body="<p>new</p>", mutation_id="update-0002")
    assert reads.head(command.client, "10")["version"]["number"] == 2
    count = len(upstream.gets)
    assert reads.body(command.client, "10", 1)["body"]["storage"]["value"] == "<p>before</p>"
    assert len(upstream.gets) == count


def test_stale_expected_version_and_upstream_race_are_conflicts(commands):
    command, upstream = commands
    with pytest.raises(CommandError) as conflict:
        command.update_page(page_id="10", expected_version=3, title="bad", mutation_id="conflict-001")
    assert conflict.value.reason == "version_conflict" and upstream.writes == []
    upstream.race = True
    with pytest.raises(UpstreamError) as race:
        command.update_page(page_id="10", expected_version=1, title="raced", mutation_id="conflict-002")
    assert race.value.status == 409
    assert upstream.items["10"]["title"] == "Parent"


def test_preflight_network_failure_is_known_not_written_and_retryable(commands):
    command, upstream = commands
    upstream.fail_read = True
    args = dict(page_id="10", expected_version=1, title="after retry", mutation_id="preflight-001")
    with pytest.raises(MutationPreflightUnavailable):
        command.update_page(**args)
    assert upstream.writes == []
    assert command._receipts().receipt("confluence.update_page", args["mutation_id"]) is None
    assert command.update_page(**args)["version"] == 2


def test_update_response_loss_reconciles_marker_and_never_repeats_put(commands):
    command, upstream = commands
    upstream.lose_response = True
    args = dict(page_id="10", expected_version=1, title="renamed", mutation_id="lost-update-001")
    with pytest.raises(MutationOutcomeUnknown):
        command.update_page(**args)
    assert command.update_page(**args)["version"] == 2
    assert len(upstream.writes) == 1


def test_cache_cleanup_failure_keeps_committed_receipt(commands, monkeypatch):
    command, upstream = commands
    original = command.invalidate
    monkeypatch.setattr(command, "invalidate", lambda ids: (_ for _ in ()).throw(RuntimeError("disk")))
    args = dict(space="DL", title="Committed", body="text", format="text", mutation_id="cleanup-001")
    with pytest.raises(MutationOutcomeUnknown):
        command.create_page(**args)
    monkeypatch.setattr(command, "invalidate", original)
    assert command.create_page(**args)["id"] == "11"
    assert len(upstream.writes) == 1


def test_comment_ownership_version_and_plain_text_roundtrip(commands):
    command, upstream = commands
    created = command.create_comment(page_id="10", body="<not markup>\nline", format="text", mutation_id="comment-001")
    assert upstream.items[created["id"]]["body"]["storage"]["value"] == "<p>&lt;not markup&gt;<br />line</p>"
    updated = command.update_comment(page_id="10", comment_id=created["id"], expected_version=1,
        body="<p>edited</p>", mutation_id="comment-002")
    assert updated["version"] == 2
    with pytest.raises(CommandError):
        command.delete_child(page_id="10", child_id="10", kind="comment", expected_version=1, mutation_id="comment-003")
    command.delete_child(page_id="10", child_id=created["id"], kind="comment", expected_version=2, mutation_id="comment-004")
    assert created["id"] not in upstream.items


def test_attachment_upload_read_pins_version_and_rejects_replacement(commands):
    command, upstream = commands
    created = command.upload_attachment(page_id="10", filename="image.png", data=b"abcdef", mutation_id="attach-001")
    assert parse_qs(urlsplit(upstream.writes[-1][1]).query)["allowDuplicated"] == ["false"]
    first = command.read_attachment(page_id="10", attachment_id=created["id"], max_bytes=3)
    assert first["base64"] == "YWJj" and first["nextOffset"] == 3
    with pytest.raises(CommandError, match="version"):
        command.read_attachment(page_id="10", attachment_id=created["id"], offset=3)
    second = command.read_attachment(page_id="10", attachment_id=created["id"], offset=3, version=first["version"])
    assert second["base64"] == "ZGVm" and not second["hasMore"]
    assert upstream.gets[-1][0] == "https://docs.test/wiki/download/attachments/10/image.png?version=1"
    with pytest.raises(CommandError) as duplicate:
        command.upload_attachment(page_id="10", filename="image.png", data=b"new", mutation_id="attach-002")
    assert duplicate.value.status == 409
    upstream.items[created["id"]]["_links"]["download"] = "https://evil.test/download/attachments/10/image.png"
    with pytest.raises(CommandError, match="다운로드"):
        command.read_attachment(page_id="10", attachment_id=created["id"])


def test_upload_response_loss_stays_unknown_instead_of_guessing_by_name(commands):
    command, upstream = commands
    upstream.lose_response = True
    args = dict(page_id="10", filename="image.png", data=b"bytes", mutation_id="attach-lost-01")
    with pytest.raises(MutationOutcomeUnknown):
        command.upload_attachment(**args)
    with pytest.raises(MutationOutcomeUnknown):
        command.upload_attachment(**args)
    assert len(upstream.writes) == 1


def test_production_existing_page_edit_requires_verified_capability(commands):
    command, upstream = commands
    command.client.env = "prod"
    with pytest.raises(CommandError) as gated:
        command.update_page(page_id="10", title="gated", expected_version=1, mutation_id="gate-0001")
    assert gated.value.reason == "confluence_compatibility_unverified"
    assert not upstream.writes
    command.settings.confluence_verified_features = ["update_page"]
    command.settings.confluence_verification_revision = "9.2.4"
    assert command.update_page(page_id="10", title="verified", expected_version=1, mutation_id="gate-0001")["version"] == 2


def test_labels_are_additive_and_removal_is_single_name(commands):
    command, upstream = commands
    command.update_labels(page_id="10", labels=["etl", "etl", "review"], mutation_id="labels-0001")
    assert upstream.writes[-1][2] == [{"prefix": "global", "name": "etl"}, {"prefix": "global", "name": "review"}]
    command.update_labels(page_id="10", labels=["etl"], remove=True, mutation_id="labels-0002")
    assert upstream.writes[-1][2] == {"name": "etl"}
    with pytest.raises(CommandError):
        command.update_labels(page_id="10", labels=["etl", "review"], remove=True, mutation_id="labels-0003")


def test_same_mutation_id_is_scoped_to_confluence_instance(commands):
    command, upstream = commands
    args = dict(space="DL", title="One", body="", mutation_id="scoped-0001")
    first = command.create_page(**args)
    command.settings.confluence_base = "https://other.test/wiki"
    second = command.create_page(**args)
    assert first["id"] != second["id"]
    assert len(upstream.writes) == 2


def test_real_mcp_sdk_command_schemas_and_publish_read_edit_roundtrip(build_env):
    import anyio
    from jsonschema import Draft202012Validator
    from mcp import Client
    from app.local_mcp.schemas.confluence_writes import DATA_MODELS

    env, upstream = build_env(), ConfluenceFixture()
    env.runtime.settings.search_confluence_spaces = ["DL"]
    env.runtime.settings.confluence_base = "https://docs.test/wiki"
    env.client._provider = upstream
    assert env.runtime.server._tool_manager.get_tool("create_confluence_page")

    async def scenario():
        async with Client(env.runtime.server) as sdk:
            tools = {tool.name: tool for tool in (await sdk.list_tools()).tools}
            for name in DATA_MODELS:
                assert tools[name].output_schema
                assert tools[name].annotations.read_only_hint == name.startswith("read_")
            assert "expected_version" in tools["update_confluence_page"].input_schema["required"]
            assert "client_mutation_id" in tools["create_confluence_page"].input_schema["required"]

            async def call(name, arguments):
                result = await sdk.call_tool(name, arguments)
                Draft202012Validator(tools[name].output_schema).validate(result.structured_content)
                assert not result.is_error, result.structured_content
                return result.structured_content["data"]

            page = await call("create_confluence_page", {"space": "DL", "title": "SDK document",
                "body": "<p>first</p>", "parent_id": "10", "client_mutation_id": "sdk-create-001"})
            read = await call("read_confluence_document", {"page": page["id"], "format": "storage"})
            assert read["content"] == "<p>first</p>"
            updated = await call("update_confluence_page", {"page_id": page["id"], "expected_version": 1,
                "body": "<p>second</p>", "client_mutation_id": "sdk-update-001"})
            assert updated["version"] == 2
            read = await call("read_confluence_document", {"page": page["id"], "format": "storage"})
            assert read["content"] == "<p>second</p>"
            conflict = await sdk.call_tool("update_confluence_page", {"page_id": page["id"],
                "expected_version": 1, "title": "stale", "client_mutation_id": "sdk-conflict-01"})
            assert conflict.is_error
            Draft202012Validator(tools["update_confluence_page"].output_schema).validate(conflict.structured_content)
            assert conflict.structured_content["error"]["service"] == "confluence"
            assert conflict.structured_content["error"]["code"] == "conflict"

            comment = await call("create_confluence_comment", {"page_id": page["id"], "body": "comment",
                "format": "text", "client_mutation_id": "sdk-comment-01"})
            edited = await call("update_confluence_comment", {"page_id": page["id"], "comment_id": comment["id"],
                "expected_version": 1, "body": "<p>edited comment</p>", "client_mutation_id": "sdk-comment-02"})
            assert edited["version"] == 2
            await call("delete_confluence_comment", {"page_id": page["id"], "comment_id": comment["id"],
                "expected_version": 2, "client_mutation_id": "sdk-comment-03"})
            await call("add_confluence_labels", {"page_id": page["id"], "labels": ["mcp"],
                "client_mutation_id": "sdk-labels-001"})
            guide = await call("get_content_guide", {"skill": "ltm-confluence-authoring"})
            assert "storage XHTML" in guide["text"]

    anyio.run(scenario)

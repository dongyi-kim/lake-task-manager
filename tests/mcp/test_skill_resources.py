"""Skill discovery/resources use the real protected HTTP transport without Jira."""

from hashlib import sha256

import anyio
import httpx2
import pytest
import yaml

from app.local_mcp.skill_catalog import MAX_RESOURCE_BYTES, SKILL_NAMES, SkillCatalog


def _catalog_files(tmp_path):
    for name in SKILL_NAMES:
        directory = tmp_path / name
        directory.mkdir()
        (directory / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: 테스트 작성 안내\nmetadata:\n  version: '1'\n---\n안내\n",
            encoding="utf-8")
    shared = tmp_path / "_shared"
    shared.mkdir()
    (shared / "content-format.md").write_text("# 공통 서식\n", encoding="utf-8")
    (shared / "draft-review.md").write_text("# 제출 전 초안 검토\n", encoding="utf-8")
    return SkillCatalog(tmp_path)


def test_catalog_snapshots_bytes_and_projects_shared_files_within_each_manifest(tmp_path):
    catalog = _catalog_files(tmp_path)
    entries = catalog.list_skills()
    draft_review = (tmp_path / "_shared" / "draft-review.md").read_bytes().decode("utf-8")
    assert {entry["frontmatter"]["name"] for entry in entries} == {
        "ltm-task-authoring", "ltm-comment-authoring", "ltm-bulk-task-create", "ltm-confluence-authoring"}
    for entry in entries:
        root = entry["uri"].removesuffix("SKILL.md")
        # Jira editor markup must not be silently projected into Confluence's guide.
        is_confluence = entry["frontmatter"]["name"] == "ltm-confluence-authoring"
        assert len(entry["resources"]) == (2 if is_confluence else 3)
        paths = {resource["uri"].removeprefix(root) for resource in entry["resources"]}
        assert "references/draft-review.md" in paths
        assert ("references/content-format.md" in paths) == (not is_confluence)
        assert catalog.read(root + "references/draft-review.md")["text"] == draft_review
        for resource in entry["resources"]:
            assert resource["uri"].startswith(root)
            content = catalog.read(resource["uri"])
            raw = content["text"].encode("utf-8")
            assert resource["size"] == len(raw)
            assert resource["digest"] == "sha256:" + sha256(raw).hexdigest()
        source = catalog.read(entry["uri"])["text"]
        assert entry["frontmatter"] == yaml.safe_load(source.split("---", 2)[1])
    old = catalog.guide(SKILL_NAMES[0], "references/content-format.md")
    (tmp_path / "_shared" / "content-format.md").write_text("Changed", encoding="utf-8")
    entries[0]["frontmatter"]["description"] = "Mutated caller copy"
    assert catalog.guide(SKILL_NAMES[0], "references/content-format.md") == old
    assert catalog.list_skills()[0]["frontmatter"]["description"] == "테스트 작성 안내"
    assert SkillCatalog(tmp_path).guide(SKILL_NAMES[0], "references/content-format.md") != old
    assert catalog.guide() == {"skills": catalog.list_skills()}


@pytest.mark.parametrize("path", ["../_shared/content-format.md", "../secret.json", "/SKILL.md",
    "references/%2e%2e/SKILL.md", r"..\secret.json", "SKILL.md?token=x", "SKILL.md#part"])
def test_guide_never_resolves_request_paths_on_disk(tmp_path, path):
    catalog = _catalog_files(tmp_path)
    with pytest.raises(ValueError, match="Unknown skill resource"):
        catalog.guide(SKILL_NAMES[0], path)


def test_catalog_rejects_invalid_frontmatter(tmp_path):
    _catalog_files(tmp_path)
    skill = tmp_path / SKILL_NAMES[0] / "SKILL.md"
    skill.write_text("---\nname: different\ndescription: guide\n---\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid skill frontmatter"):
        SkillCatalog(tmp_path)


def test_catalog_requires_progressive_disclosure_for_large_documents(tmp_path):
    _catalog_files(tmp_path)
    (tmp_path / "_shared" / "large.md").write_text("a" * (MAX_RESOURCE_BYTES + 1), encoding="utf-8")
    with pytest.raises(ValueError, match="Split large skill resources"):
        SkillCatalog(tmp_path)


def test_catalog_rejects_outside_symlink(tmp_path):
    _catalog_files(tmp_path)
    target = tmp_path / "outside.md"
    target.write_text("not a skill asset", encoding="utf-8")
    try:
        (tmp_path / SKILL_NAMES[0] / "linked.md").symlink_to(target)
    except OSError:
        pytest.skip("OS does not allow creating symbolic links")
    with pytest.raises(ValueError, match="symbolic links"):
        SkillCatalog(tmp_path)


@pytest.mark.parametrize("version", ["2025-11-25", "2026-07-28"])
def test_http_skill_catalog_and_resources_are_discoverable_without_jira(build_env, monkeypatch, version):
    env = build_env()
    def unavailable(*_args, **_kwargs):
        raise AssertionError("Packaged guides must not invoke Jira or its authentication")
    monkeypatch.setattr(env.runtime, "get_client", unavailable)
    headers = {"Authorization": "Bearer " + env.runtime.credentials.token(),
               "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": version}

    async def scenario():
        async with env.app.router.lifespan_context(env.app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env.app),
                    base_url="http://127.0.0.1:4457", headers=headers) as http:
                async def call(method, params=None):
                    params = dict(params or {})
                    request_headers = {"MCP-Method": method}
                    if version == "2026-07-28":
                        params["_meta"] = {"io.modelcontextprotocol/protocolVersion": version,
                            "io.modelcontextprotocol/clientCapabilities": {}}
                        if "uri" in params:
                            request_headers["MCP-Name"] = params["uri"]
                    response = await http.post("/mcp", json={"jsonrpc": "2.0", "id": 1,
                        "method": method, "params": params}, headers=request_headers)
                    assert response.status_code in (200, 400), response.text
                    return response.json()

                if version == "2025-11-25":
                    discovery = await call("initialize", {"protocolVersion": version, "capabilities": {},
                        "clientInfo": {"name": "skill-resource-test", "version": "1"}})
                else:
                    discovery = await call("server/discover")
                capabilities = discovery["result"]["capabilities"]
                if version == "2026-07-28":
                    assert capabilities["extensions"]["io.modelcontextprotocol/skills"] == {}
                assert "resources" in capabilities
                listed = (await call("skills/list"))["result"]
                assert {row["frontmatter"]["name"] for row in listed["skills"]} == set(SKILL_NAMES)
                assert ("ttlMs" in listed) == (version == "2026-07-28")
                resource_metadata = {row["uri"]: row for row in (await call("resources/list"))["result"]["resources"]}
                for entry in listed["skills"]:
                    fetched = (await call("skills/get", {"uri": entry["uri"]}))["result"]
                    assert fetched["skill"] == entry
                    assert ("ttlMs" in fetched) == (version == "2026-07-28")
                    if version == "2026-07-28":
                        assert listed["resultType"] == fetched["resultType"] == "complete"
                        assert listed["cacheScope"] == fetched["cacheScope"] == "private"
                    metadata = resource_metadata[entry["uri"]]
                    assert metadata["name"] == entry["frontmatter"]["name"]
                    assert metadata["description"] == entry["frontmatter"]["description"]
                    assert metadata["mimeType"] == "text/markdown"
                    for item in entry["resources"]:
                        content = (await call("resources/read", {"uri": item["uri"]}))["result"]["contents"][0]
                        raw = content["text"].encode("utf-8")
                        assert item["digest"] == "sha256:" + sha256(raw).hexdigest()
                        assert item["size"] == len(raw)
                for method, params in (
                    ("skills/list", {"cursor": "invalid"}),
                    ("skills/get", {"uri": "skill://missing/SKILL.md"}),
                    ("skills/get", {"uri": listed["skills"][0]["resources"][-1]["uri"]}),
                    ("resources/read", {"uri": "skill://ltm-task-authoring/../secret.json"}),
                    ("resources/read", {"uri": "file:///etc/passwd"}),
                ):
                    assert (await call(method, params))["error"]["code"] == -32602
                assert env.calls == []
    anyio.run(scenario)


def test_skill_discovery_still_requires_local_mcp_credentials(build_env):
    env = build_env()
    body = {"jsonrpc": "2.0", "id": 1, "method": "skills/list", "params": {}}
    async def scenario():
        async with env.app.router.lifespan_context(env.app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env.app),
                    base_url="http://127.0.0.1:4457") as http:
                assert (await http.post("/mcp", json=body)).status_code == 401
                token = env.runtime.credentials.token()
                headers = {"Authorization": "Bearer " + token}
                assert (await http.post("/mcp", json=body, headers={**headers, "Origin": "https://elsewhere.test"})).status_code == 403
                env.runtime.credentials.rotate()
                assert (await http.post("/mcp", json=body, headers=headers)).status_code == 401
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env.app, client=("10.0.0.2", 123)),
                    base_url="http://127.0.0.1:4457") as http:
                assert (await http.post("/mcp", json=body, headers={"Authorization": "Bearer " + env.runtime.credentials.token()})).status_code == 403
    anyio.run(scenario)

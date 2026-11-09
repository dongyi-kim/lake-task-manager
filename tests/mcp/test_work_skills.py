"""Investigation guides share real MCP transport and preserve an explicit authoring subset."""
from hashlib import sha256
import re

import anyio
from jsonschema import Draft202012Validator
from mcp import Client
import pytest

from app.local_mcp.skill_catalog import MAX_RESOURCE_BYTES, SKILL_NAMES, SkillCatalog

RESEARCH = {"ltm-work-research", "ltm-work-reporting"}


def test_candidates_are_opt_in_and_do_not_change_authoring_assets():
    default = SkillCatalog()
    candidate = SkillCatalog(include_research=True)
    names = lambda catalog: {e["frontmatter"]["name"] for e in catalog.list_skills()}
    assert names(default) == set(SKILL_NAMES)
    assert names(candidate) == set(SKILL_NAMES) | RESEARCH
    for file in default.files():
        assert candidate.read(file["uri"]) == file
    for name in RESEARCH:
        with pytest.raises(ValueError):
            default.guide(name)
        with pytest.raises(ValueError):
            default.read(f"skill://{name}/SKILL.md")


def test_candidate_resources_are_complete_small_and_jira_markup_is_not_projected():
    catalog = SkillCatalog(include_research=True)
    for name in RESEARCH:
        root = f"skill://{name}/"
        entry = catalog.get_skill(root + "SKILL.md")
        uris = {f["uri"] for f in entry["resources"]}
        assert root + "references/checkpoint-resume.md" in uris
        assert root + "references/checkpoint-example.json" in uris
        assert root + "references/content-format.md" not in uris
        for uri in uris:
            item = catalog.read(uri)
            raw = item["text"].encode("utf-8")
            assert item["size"] == len(raw) <= MAX_RESOURCE_BYTES
            assert item["digest"] == "sha256:" + sha256(raw).hexdigest()
            if uri.endswith(".md"):
                for link in re.findall(r"\]\((references/[^)#]+)", item["text"]):
                    assert root + link in uris, (uri, link)


def test_real_sdk_reads_research_without_jira_and_explicit_opt_out_rejects_them(build_env, monkeypatch):
    env = build_env(include_research_skills=True)
    monkeypatch.setattr(env.runtime, "get_client", lambda: pytest.fail("guides must not initialize Jira"))

    async def scenario():
        async with Client(env.runtime.server) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            tool = tools["get_content_guide"]
            assert RESEARCH <= set(tool.input_schema["properties"]["skill"]["enum"])
            validator = Draft202012Validator(tool.output_schema)
            listing = await client.call_tool("get_content_guide", {})
            names = {e["frontmatter"]["name"] for e in listing.structured_content["data"]["skills"]}
            assert RESEARCH <= names
            for name in RESEARCH:
                entry = env.runtime.skill_catalog.get_skill(f"skill://{name}/SKILL.md")
                for resource in entry["resources"]:
                    path = resource["uri"].removeprefix(f"skill://{name}/")
                    result = await client.call_tool("get_content_guide", {"skill": name, "path": path})
                    assert not result.is_error
                    validator.validate(result.structured_content)
                    body = result.structured_content["data"]
                    read = await client.read_resource(resource["uri"])
                    assert body["text"] == read.contents[0].text
                    assert body["digest"] == resource["digest"]
            assert env.calls == []
        default = build_env(port=4458, include_research_skills=False)
        assert "ltm-work-research" not in default.runtime.server.instructions
        async with Client(default.runtime.server) as client:
            result = await client.call_tool("get_content_guide", {"skill": "ltm-work-research"})
            assert result.is_error
    anyio.run(scenario)


def test_production_runtime_exposes_both_research_guides_by_default(build_env, monkeypatch):
    env = build_env()
    monkeypatch.setattr(env.runtime, "get_client", lambda: pytest.fail("guides must not initialize Jira"))
    async def scenario():
        async with Client(env.runtime.server) as client:
            listed = await client.call_tool("get_content_guide", {})
            names = {row["frontmatter"]["name"] for row in listed.structured_content["data"]["skills"]}
            assert names == set(SKILL_NAMES) | RESEARCH
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert RESEARCH <= set(tools["get_content_guide"].input_schema["properties"]["skill"]["enum"])
            assert not {"list_files", "read_file", "write_file"} & tools.keys()
            for name in RESEARCH:
                fallback = await client.call_tool("get_content_guide", {"skill": name})
                resource = await client.read_resource(f"skill://{name}/SKILL.md")
                assert not fallback.is_error
                assert fallback.structured_content["data"]["text"] == resource.contents[0].text
        assert env.calls == []
    anyio.run(scenario)

"""Issue 190 oracle/transport contracts; no external model is run."""
import copy
import json

import anyio
from mcp import Client
import pytest
from support.evaluation_tmp import external_tmp_path as tmp_path

from tools.codex_mcp_eval import work_cases
from tools.codex_mcp_eval import runner
from tools.codex_mcp_eval.work_fixture import WorkFixture, build_work_data


def test_work_matrix_has_two_cases_per_family_and_date_bound_oracles():
    cases = work_cases.materialize_cases("2026-11-09")
    assert len(cases) == 22 and len({c["id"] for c in cases}) == 22
    for family in range(1, 12):
        assert {c["variant"] for c in cases if c["family"] == f"R{family:02d}"} == {"normal", "boundary"}
    _, specs = work_cases.review_contract("2026-11-09")
    assert set(specs) == {c["id"] for c in cases}
    data = build_work_data("2026-11-09")
    assert data["oracle"]["period"] == {"start": "2026-10-01", "end": "2026-11-01"}
    assert data["oracle"]["operations"] == [
        {"category": "new", "tickets": 3, "voc": 1, "uniqueTables": 2, "workEvents": 2},
        {"category": "improve", "tickets": 2, "voc": 0, "uniqueTables": 1, "workEvents": 2},
        {"category": "stop", "tickets": 1, "voc": 0, "uniqueTables": 1, "workEvents": 1},
    ]
    assert data["oracle"]["unionTables"] == 2
    shifted = build_work_data("2026-12-09")
    assert shifted["oracle"]["period"]["start"] == "2026-11-01"
    assert data["oracle"] != shifted["oracle"]


def test_work_checker_requires_successful_subject_evidence_and_does_not_trust_arguments():
    case = next(c for c in work_cases.materialize_cases() if c["id"] == "WORK-R02-normal")
    truth = build_work_data("2026-11-09")["oracle"]
    expected = truth["cases"][case["id"]]
    answer = {"reply": "prod.dw.customers prod.dw.orders 신규 2개 DL-94001 DL-94002 DL-94007",
              "resultKeys": expected["keys"], "reportedTotal": expected["total"], "sources": [],
              "progress": None, "operations": []}
    output = {"ok": True, "reply": answer["reply"], "structuredAnswer": answer}
    fake = [{"name": "get_ticket", "arguments": {"key": "DL-94001", "text": answer["reply"]},
             "result": {"ok": False, "data": {"key": "DL-94001", "description": answer["reply"]}}}]
    result = work_cases.check(case, output, fake, truth)
    assert not result["pass"]
    assert "no_mcp_evidence" in result["failures"]
    assert "required_evidence_not_observed" in result["failures"]
    assert result["humanReviewRequired"]


@pytest.mark.parametrize("condition,transport", [("baseline", "default"), ("candidate", "default"), ("candidate", "fallback")])
def test_work_fixture_exposes_real_sources_and_only_requested_guides(tmp_path, condition, transport):
    with WorkFixture(tmp_path / (condition + transport), port=0, today="2026-11-09",
                     skill_condition=condition, skill_transport=transport) as fixture:
        before = fixture.fingerprint()
        assert fixture.assert_conformance()["cacheWarmed"] is False
        assert fixture.upstream_calls == []
        truth = fixture.ground_truth()
        assert len(truth["cases"]) == 22
        assert fixture.settings.search_jira_projects == ["DL"]
        assert fixture.settings.search_confluence_spaces == ["DL"]
        async def scenario():
            async with Client(fixture.runtime.server) as client:
                listing = await client.call_tool("get_content_guide", {})
                names = {s["frontmatter"]["name"] for s in listing.structured_content["data"]["skills"]}
                assert ("ltm-work-research" in names) == (condition == "candidate")
                ticket = await client.call_tool("get_ticket", {"key": "DL-94001", "detail": "read"})
                assert not ticket.is_error and "prod.dw.customers" in json.dumps(ticket.structured_content)
                assert fixture.upstream_calls and all(type(row["responseBytes"]) is int for row in fixture.upstream_calls)
                doc = await client.call_tool("read_confluence_document", {"page": truth["documentIds"]["주간보고"], "version": 1})
                assert not doc.is_error
                assert "진행" in doc.structured_content["data"]["content"]
                denied = await client.call_tool("create_task", {"body": {"summary": "forbidden"}})
                assert denied.is_error
                if transport == "default":
                    assert (await client.read_resource("skill://ltm-task-authoring/SKILL.md")).contents
                else:
                    with pytest.raises(Exception):
                        await client.read_resource("skill://ltm-task-authoring/SKILL.md")
        anyio.run(scenario)
        if transport == "default":
            assert any(r["kind"] == "resource" for r in fixture.skill_reads)
        assert fixture.fingerprint() == before


@pytest.mark.parametrize("read_guide", [True, False])
def test_work_runner_selects_condition_and_retains_manifest_without_model(tmp_path, read_guide):
    from tools.codex_mcp_eval import cli
    from mcp.client.streamable_http import streamable_http_client
    import httpx2
    def fixture(path, **kwargs):
        return WorkFixture(path, **{**kwargs, "port": 0})
    def fake_cli(binary, prompt, workspace, **kwargs):
        if not kwargs.get("mcp_url"):
            return {"ok": True, "reply": "LTM_PREFLIGHT_OK"}
        async def scenario():
            async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + kwargs["token"]}) as http:
                async with Client(streamable_http_client(kwargs["mcp_url"], http_client=http)) as client:
                    if read_guide:
                        await client.call_tool("get_content_guide", {"skill": "ltm-work-reporting"})
                    for key in ("DL-94001", "DL-94002", "DL-94007"):
                        response = await client.call_tool("get_ticket", {"key": key, "detail": "read"})
                        assert not response.is_error
        anyio.run(scenario)
        answer = {"reply": "prod.dw.customers와 prod.dw.orders, 신규 2개. DL-94001 DL-94002 DL-94007",
            "resultKeys": ["DL-94001", "DL-94002", "DL-94007"], "reportedTotal": 2, "progress": None,
            "operations": [], "sources": [{"kind": "ticket", "id": "DL-94001",
                                            "url": "http://127.0.0.1:8080/browse/DL-94001"}]}
        return {"ok": True, "reply": answer["reply"], "structuredAnswer": answer,
                "durationSeconds": 1, "usage": {}, "events": []}
    args = ["--battery", "work", "--date", "2026-11-09", "--skill-condition", "candidate",
            "--skill-transport", "fallback", "--output-root", str(tmp_path / "raw"),
            "--run-group", "offline-work", "--cases", "WORK-R02-normal", "--codex", "stub"]
    result = runner.launch(args, cli_run=fake_cli, fixture_factory=fixture,
                           identity_fn=lambda *a, **k: {"runtime": "test-no-model", "version": "test"})
    payload = json.loads(next((tmp_path / "raw" / "offline-work").glob("*.json")).read_text(encoding="utf-8"))
    assert result == 0, payload
    context = payload["evaluation"]["run"]["externalRuntime"]
    assert context["skillCondition"] == "candidate" and context["skillTransport"] == "fallback"
    assert context["skillManifestSha256"]
    application = payload["cases"][0]["skillApplication"]
    assert application["entrypointRead"] is read_guide
    assert application["attribution"] == ("observed-resource-reads" if read_guide else "exposure-only")
    assert payload["humanReview"]["status"] == "pending"
    assert not list((tmp_path / "raw").rglob("mcp-secret.json"))
    assert not list((tmp_path / "raw").rglob("jira-cache.sqlite3"))
    with pytest.raises(FileExistsError):
        runner.launch(args, cli_run=fake_cli, fixture_factory=fixture,
                      identity_fn=lambda *a, **k: {"runtime": "test-no-model", "version": "test"})


def test_ab_conditions_have_identical_source_world_and_document_revision_clock(tmp_path):
    with WorkFixture(tmp_path / "a", port=0, today="2026-11-09", skill_condition="baseline") as baseline:
        original = baseline.fingerprint()
    with WorkFixture(tmp_path / "b", port=0, today="2026-11-09", skill_condition="candidate") as candidate:
        assert candidate.fingerprint() == original

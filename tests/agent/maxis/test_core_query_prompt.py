"""Exact semantic delivery; these tests do not claim model planning quality."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
LOCAL = ROOT / "app/agent/prompts/roles/query_specialist.md"
WIRE = ROOT / "deploy/maxis-dify/prompts/core_query.md"
SELECTED = ("Query Design", "Scope and Tool Contract", "Preflight Check")


@pytest.fixture
def compiler():
    namespace = {"__name__": "query_prompt_test"}
    path = ROOT / "deploy/maxis-dify/core_query_contract.py"
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)
    return namespace["build_query_system_prompt"]


def section(text, title):
    return ("## " + title + "\n" + text.split("## " + title + "\n", 1)[1].split("\n## ", 1)[0]).rstrip("\n")


def test_exact_sections_once_in_order_without_other_local_contracts(compiler):
    local, wire = LOCAL.read_text(encoding="utf-8"), WIRE.read_text(encoding="utf-8")
    prompt = compiler(local, wire)
    blocks = [section(local, title) for title in SELECTED]
    assert all(prompt.count(block) == 1 for block in blocks)
    assert [prompt.index(block) for block in blocks] == sorted(prompt.index(block) for block in blocks)
    assert prompt.index(blocks[-1]) < prompt.index("## Output: `query-draft.v2` only")
    assert all("## " + title not in prompt for title in ("Inputs", "Output Contract", "Stop and Escalate"))
    assert "Set `exhaustive=true`" not in "\n\n".join(blocks)
    assert "query-draft.v2" in prompt and "coverage" in prompt
    assert "## Required response" not in prompt
    assert "query-input.v2" in prompt and "`task.text`" in prompt
    assert "query-input.v1" not in prompt and "`context.query`" not in prompt


def test_external_research_never_overrides_runtime_source_capabilities(compiler):
    prompt = compiler(LOCAL.read_text(encoding="utf-8"), WIRE.read_text(encoding="utf-8"))
    assert "include a `web` query only if available" in prompt
    assert "Runtime source restrictions take precedence" in prompt
    assert "retain external verification as unresolved, never as a read" in prompt
    assert "When external official research is explicitly required, include at least one" not in prompt


@pytest.mark.parametrize("title", SELECTED)
@pytest.mark.parametrize("mutation", ["missing", "duplicate", "empty", "rename", "heading_space"])
def test_ambiguous_or_missing_selected_sections_fail_closed(compiler, title, mutation):
    local, wire = LOCAL.read_text(encoding="utf-8"), WIRE.read_text(encoding="utf-8")
    block = section(local, title)
    if mutation == "missing": local = local.replace(block, "", 1)
    elif mutation == "duplicate": local += "\n\n" + block
    elif mutation == "empty": local = local.replace(block, "## " + title + "\n\n", 1)
    elif mutation == "rename": local = local.replace("## " + title, "## Renamed " + title, 1)
    else: local = local.replace("## " + title + "\n", "## " + title + " \n", 1)
    with pytest.raises(ValueError, match="Query system prompt requires review"):
        compiler(local, wire)


def test_selected_text_is_not_rewritten_and_unselected_text_is_not_delivered(compiler):
    local, wire = LOCAL.read_text(encoding="utf-8"), WIRE.read_text(encoding="utf-8")
    original = compiler(local, wire)
    changed = local.replace("1. Preserve every user filter", "1. Preserve every  user filter", 1)
    assert section(changed, "Query Design") in compiler(changed, wire)
    assert compiler(changed, wire) != original
    changed = local.replace("## Inputs\n", "## Inputs\n\nnot-delivered-marker\n", 1)
    assert compiler(changed, wire) == original
    assert compiler(local.replace("\n", "\r\n"), wire.replace("\n", "\r\n")) == original


@pytest.mark.parametrize("mutation", ["marker_missing", "marker_duplicate", "marker_first", "marker_last", "selected_in_wire", "unknown_heading", "indented_heading", "reordered"])
def test_composition_boundary_and_heading_order_require_review(compiler, mutation):
    local, wire = LOCAL.read_text(encoding="utf-8"), WIRE.read_text(encoding="utf-8")
    marker = "## Output: `query-draft.v2` only"
    if mutation == "marker_missing": wire = wire.replace(marker, "## Output", 1)
    elif mutation == "marker_duplicate": wire += "\n" + marker + "\nextra"
    elif mutation == "marker_first": wire = marker + "\nextra"
    elif mutation == "marker_last": wire = "prefix\n" + marker
    elif mutation == "selected_in_wire": wire += "\n## Query Design\nextra"
    elif mutation == "unknown_heading": local += "\n## Extra Section\nextra"
    elif mutation == "indented_heading": local += "\n ## Query Design\nextra"
    else:
        local = local.replace("## Query Design", "## Temporary", 1).replace(
            "## Preflight Check", "## Query Design", 1).replace("## Temporary", "## Preflight Check", 1)
    with pytest.raises(ValueError, match="Query system prompt requires review"):
        compiler(local, wire)


def test_utf8_budget_is_strict_without_truncation(compiler):
    local, wire = LOCAL.read_text(encoding="utf-8"), WIRE.read_text(encoding="utf-8")
    remaining = 8192 - len(compiler(local, wire).encode("utf-8"))
    assert remaining > 0
    exact = "x" * remaining + wire
    assert len(compiler(local, exact).encode("utf-8")) == 8192
    with pytest.raises(ValueError, match="Query system prompt requires review"):
        compiler(local, "x" + exact)
    with pytest.raises(ValueError, match="Query system prompt requires review"):
        compiler(local, "가" * (remaining // 3 + 1) + wire)


def test_query_prompt_explains_projected_authority_without_granting_facts(compiler):
    prompt = compiler(LOCAL.read_text(encoding="utf-8"), WIRE.read_text(encoding="utf-8"))
    for marker in ("source_authority", "allowed_claim_kinds", "user_intent", "evidence_source"):
        assert prompt.count(marker) == 1
    assert "not fact verification or execution authority" in prompt
    assert "preserving your `kind` and selected sources" in prompt
    assert "do not repeat prior request_questions" in prompt
    assert "Never invent a subject to avoid a genuinely required question." in prompt


def test_search_literal_is_a_proposal_and_user_cannot_enable_unavailable_source(compiler):
    prompt = compiler(LOCAL.read_text(encoding="utf-8"), WIRE.read_text(encoding="utf-8"))
    assert "including literal identifiers copied from user intent" in prompt
    assert "A copied name is not an external fact" in prompt
    assert "cannot enable a missing runtime source" in prompt
    assert "record uncertainty and plan supported reads" in prompt

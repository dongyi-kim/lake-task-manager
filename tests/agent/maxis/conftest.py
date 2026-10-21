from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.agent.maxis import ContextGuard, ContextPolicy


PAYLOAD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}


@pytest.fixture
def now():
    return datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def context_guard():
    return ContextGuard(
        ContextPolicy(
            allowed_scopes={
                "jira": ("DL",),
                "confluence": ("DATALAKE",),
                "bitbucket": ("lake/ltm",),
            },
            allowed_source_hosts={
                "jira": ("jira.example.local",),
                "confluence": ("wiki.example.local",),
                "bitbucket": ("git.example.local",),
            },
        ),
        b"test-pseudonym-key-32-bytes-long",
    )


@pytest.fixture
def evidence(context_guard, now):
    return context_guard.build(
        request_id="req-001",
        use_case="pmo_brief",
        query="DL 프로젝트 진척을 설명해 줘",
        payload_schema=PAYLOAD_SCHEMA,
        user_identity="skcc.x1042",
        requested_at=now,
        sources=[{
            "source_id": "src-1",
            "kind": "jira",
            "canonical_id": "DL-9001",
            "title": "진척 현황",
            "scope": "DL",
            "url": "https://jira.example.local/browse/DL-9001",
            "content": "DL-9001의 진척률은 50%이며 미완료 업무는 2건이다.",
            "retrieved_at": now,
        }],
        facts=[{
            "fact_id": "fact-1",
            "text": "DL-9001 진척률은 50%다.",
            "source_ids": ["src-1"],
            "observed_at": now,
        }],
        metrics=[{
            "metric_id": "progress",
            "label": "진척률",
            "value": 50,
            "unit": "%",
            "formula_version": "epic-sp.v1",
            "source_ids": ["src-1"],
        }],
    )

"""Work-skills candidate over the real private Jira/Confluence MCP path."""
from __future__ import annotations

import copy
import shutil
import tempfile
from uuid import uuid4
from pathlib import Path
from unittest.mock import patch

from app.mock.confluence import install_confluence
from tools.codex_mcp_eval.fixture import _hash
from tools.codex_mcp_eval.reporting_fixture import ReportingFixture, _utc
from tools.codex_mcp_eval.work_data import CHILDREN, PLAN, build_work_data


class WorkFixture(ReportingFixture):
    def __init__(self, run_dir, *args, skill_condition="baseline", skill_transport="default", source_revision=1, **kwargs):
        if skill_condition not in {"baseline", "candidate"} or skill_transport not in {"default", "fallback"}:
            raise ValueError("Invalid work evaluation skill condition/transport")
        self.skill_condition, self.skill_transport = skill_condition, skill_transport
        self.source_revision, self.skill_reads = source_revision, []
        self._owns_run_dir = False
        self.requested_run_dir = Path(run_dir).resolve()
        repository = Path(__file__).resolve().parents[2]
        if self.requested_run_dir.is_relative_to(repository):
            raise ValueError("Work evaluation artifacts must stay outside the repository")
        scratch = Path(tempfile.gettempdir()).resolve() / uuid4().hex
        if scratch.is_relative_to(repository):
            raise ValueError("Work fixture scratch must stay outside the repository")
        super().__init__(scratch, *args, **kwargs)

    def runtime_options(self):
        return {"include_research_skills": self.skill_condition == "candidate",
                "enable_skills_extension": self.skill_transport == "default"}

    def configure_scope(self, settings):
        settings.search_jira_projects, settings.search_confluence_spaces = ["DL"], ["DL"]

    def __enter__(self):
        try:
            self.run_dir.mkdir(parents=True, exist_ok=False)
            self._owns_run_dir = True
            from app.infra import settings
            from tools.codex_mcp_eval import fixture
            data = build_work_data(self.today, source_revision=self.source_revision)
            self._patches.enter_context(patch.object(settings, "_PEOPLE_CACHE", {"data": None, "dir": None}))
            for module in (settings, fixture):
                self._patches.enter_context(patch.object(module, "load_plan", lambda: copy.deepcopy(PLAN)))
                self._patches.enter_context(patch.object(module, "load_people", lambda: {"TEST": list(data["users"])}))
            super().__enter__()
            self._work_truth = data["oracle"]
            for owner in (self.world, self.store):
                owner.users = copy.deepcopy(data["users"])
                owner.issues = copy.deepcopy(data["issues"])
                owner.confluence = copy.deepcopy(data["documents"])
            self.world._index()
            self.store.denied_issue_keys = {CHILDREN[1]} if self.fault == "partial" else set()
            self.store.reindex()
            state = install_confluence(self.provider._client.app)
            self._patches.enter_context(patch.object(state, "now", lambda: _utc(self.today, 9)))
            state.seed()
            self.document_ids = {row["title"]: row["id"] for row in state.pages.values()}
            page = self.document_ids["주간보고"]
            updated = self.provider._client.put("/rest/api/content/" + page, json={"version": {"number": 2},
                "body": {"storage": {"value": "<p>DL-94010 전체 완료라는 보고 주장. 실제 Jira와 대조 필요.</p>"}}})
            if updated.status_code != 200:
                raise RuntimeError("Work fixture failed to seed document revision")
            if self.source_revision == 2:
                updated = self.provider._client.put("/rest/api/content/" + self.document_ids["운영 결정"], json={
                    "version": {"number": 2}, "body": {"storage": {"value":
                    "<p>정정: prod.dw.orders 실행 전 취소. DL-94002 실적 제외. DL-94018은 LOAD-1 동일 사건 인수인계.</p>"}}})
                if updated.status_code != 200:
                    raise RuntimeError("Work fixture source-change revision failed")
            self._work_truth["documentIds"] = self.document_ids
            self._before = self.fingerprint()
            return self
        except BaseException:
            self.close()
            raise

    def close(self):
        try:
            super().close()
        finally:
            if self._owns_run_dir and self.run_dir.exists() and not (self._thread and self._thread.is_alive()):
                shutil.rmtree(self.run_dir)
                self._owns_run_dir = False

    def _observe_tools(self):
        super()._observe_tools()
        original = self.runtime.server.call_tool
        async def observed(name, arguments, context=None):
            result = await original(name, arguments, context)
            body = result.structured_content or {}
            if name == "get_content_guide" and body.get("ok") is True:
                data = body.get("data") or {}
                if data.get("uri"):
                    self.skill_reads.append({"kind": "guide", "uri": data["uri"], "digest": data["digest"]})
            self.observe_read(name, arguments or {}, body)
            return result
        self.runtime.server.call_tool = observed
        read = self.runtime.server.read_resource
        async def resource(uri, *args, **kwargs):
            result = await read(uri, *args, **kwargs)
            doc = self.runtime.skill_catalog.read(str(uri))
            self.skill_reads.append({"kind": "resource", "uri": str(uri), "digest": doc["digest"]})
            return result
        self.runtime.server.read_resource = resource

    def observe_read(self, name, arguments, body):
        pass

    def fingerprint(self):
        result = super().fingerprint()
        result["worldSha256"] = _hash([result["worldSha256"], self.world.users])
        state = self.provider._client.app.state.confluence
        result["providerStoreSha256"] = _hash([result["providerStoreSha256"], self.store.users, state.snapshot()])
        return result

    def ground_truth(self):
        return {**copy.deepcopy(self._work_truth), "issues": copy.deepcopy(self.store.issues),
                "users": copy.deepcopy(self.store.users)}

    def assert_conformance(self):
        raw = self.provider._client
        query = 'labels = work_research_fixture ORDER BY key ASC'
        wanted = sorted(key for key, row in self.store.issues.items()
                        if "work_research_fixture" in row["labels"] and key not in self.store.denied_issue_keys)
        actual, offset = [], 0
        while True:
            response = raw.get("/rest/api/2/search", params={"jql": query, "startAt": offset, "maxResults": 3})
            assert response.status_code == 200
            page = response.json()
            actual.extend(row["key"] for row in page["issues"])
            offset += len(page["issues"])
            if offset >= page["total"]:
                break
            assert page["issues"], "Work fixture search made no progress"
        assert actual == wanted
        for title, identifier in self.document_ids.items():
            response = raw.get("/rest/api/content/" + identifier, params={"expand": "body.storage,version"})
            assert response.status_code == 200 and response.json()["title"] == title
        assert not self.upstream_calls
        return {"cacheWarmed": False, "workKeys": actual, "documents": dict(self.document_ids),
                "skillCondition": self.skill_condition, "skillTransport": self.skill_transport}

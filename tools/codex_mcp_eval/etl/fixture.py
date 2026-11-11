"""Year corpus through the actual Jira820 → JiraClient → MCP transport."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil
import tempfile
from unittest.mock import patch
from uuid import uuid4

from fastapi import Request

from app.mock.confluence import install_confluence
from tools.codex_mcp_eval.fixture import EvaluationFixture, _hash
from .checks import build_expectations
from .world import build_etl_world


class EtlFixture(EvaluationFixture):
    def __init__(self, run_dir, *args, bundle=None, skill_condition="baseline", skill_transport="default",
                 controller=None, **kwargs):
        if skill_condition not in {"baseline", "candidate"} or skill_transport not in {"default", "fallback"}:
            raise ValueError("Invalid ETL skill condition/transport")
        self.bundle = bundle or build_etl_world()
        self.skill_condition, self.skill_transport = skill_condition, skill_transport
        self.controller, self.skill_reads = controller, []
        self.requested_run_dir = Path(run_dir).resolve()
        repository = Path(__file__).resolve().parents[3]
        if self.requested_run_dir.is_relative_to(repository):
            raise ValueError("ETL evaluation artifacts must stay outside the repository")
        today = kwargs.pop("today", self.bundle.spec.observed_until.date().isoformat())
        if today != self.bundle.spec.observed_until.date().isoformat():
            raise ValueError("The ETL fixture freezes its observation date")
        self._scratch = Path(tempfile.gettempdir()).resolve() / uuid4().hex
        super().__init__(self._scratch, *args, today=today, **kwargs)
        if controller:
            controller.files = self.files

    def configure_scope(self, settings):
        settings.search_jira_projects, settings.search_confluence_spaces = ["DL"], ["DL"]

    def runtime_options(self):
        return {"include_research_skills": True,
                "enable_skills_extension": self.skill_transport == "default"}

    def __enter__(self):
        from tools.codex_mcp_eval import fixture
        from app.infra import settings
        from app.local_mcp import runtime as runtime_module
        from .skills import research_catalog
        from .status_history_search import install_status_history_search
        base = fixture.World
        public = self.bundle.world

        class PublicWorld(base):
            ME = public.ME

            def __init__(world, *args, **kwargs):
                for name, value in vars(public).items():
                    setattr(world, name, deepcopy(value))
                config = settings.get_settings()
                world.sp_field, world.epic_link_field = config.sp_field_id, config.epic_link_field_id
                world._counter = 60000
                world.gen_modules = list(world.modules)
                world._comp_ids = {name: str(100 + index) for index, name in enumerate(world.modules)}
                world._index()

        try:
            self._patches.enter_context(patch.object(runtime_module, "SkillCatalog",
                lambda *args, **kwargs: research_catalog(self.skill_condition, *args, **kwargs)))
            self._patches.enter_context(patch.object(fixture, "World", PublicWorld))
            for module in (settings, fixture):
                self._patches.enter_context(patch.object(module, "load_plan", lambda: deepcopy(public.plan)))
                self._patches.enter_context(patch.object(module, "load_people", lambda: deepcopy(public.people)))
            self._patches.enter_context(patch.object(settings, "_PEOPLE_CACHE", {"data": None, "dir": None}))
            super().__enter__()
            for name in ("issues", "users", "confluence", "attachments", "activity", "sprints"):
                setattr(self.store, name, deepcopy(getattr(public, name)))
            self.store.reindex()
            self.store.config.current_user = public.ME
            self.store.denied_issue_keys = {"DL-21001"} if self.fault == "partial" else set()
            # Preserve the same timestamp and ordered-comment adaptations used
            # by ReportingFixture, without installing that older fixture world.
            original = self.store.serializer.issue_fields

            def timestamp_fields(issue, base_url=""):
                fields = original(issue, base_url)
                fields.update(issue.get("_timestamps") or {})
                return fields

            self.store.serializer.issue_fields = timestamp_fields

            def ordered_comments(key: str, request: Request, maxResults: int = 50, startAt: int = 0, orderBy: str = ""):
                rows = self.store.jira_comments(key)
                if orderBy:
                    rows.sort(key=lambda row: row["created"], reverse=orderBy.startswith("-"))
                return {"startAt": startAt, "maxResults": maxResults, "total": len(rows),
                        "comments": rows[startAt:startAt + maxResults]}

            upstream = self.provider._client.app
            self.status_history_search = install_status_history_search(upstream,
                observed_until=self.bundle.spec.observed_until, search_timezone=self.bundle.manifest["timezone"])
            upstream.add_api_route("/rest/api/2/issue/{key}/comment", ordered_comments, methods=["GET"])
            upstream.router.routes.insert(0, upstream.router.routes.pop())
            state = install_confluence(upstream)
            state.seed()
            self.document_ids = self.bundle.install_confluence_revisions(state)
            self._before = self.fingerprint()
            return self
        except BaseException:
            self.close()
            raise

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
            if self.controller:
                self.controller.observe_ltm(name, arguments or {}, body)
            return result

        self.runtime.server.call_tool = observed
        read = self.runtime.server.read_resource

        async def resource(uri, *args, **kwargs):
            result = await read(uri, *args, **kwargs)
            doc = self.runtime.skill_catalog.read(str(uri))
            self.skill_reads.append({"kind": "resource", "uri": str(uri), "digest": doc["digest"]})
            return result

        self.runtime.server.read_resource = resource

    def fingerprint(self):
        result = super().fingerprint()
        result["worldSha256"] = _hash([result["worldSha256"], self.world.users, self.world.plan,
                                      getattr(self.world, "ME", None)])
        state = getattr(self.provider._client.app.state, "confluence", None)
        result["providerStoreSha256"] = _hash([result["providerStoreSha256"], self.store.users,
                                              state.snapshot() if state else None,
                                              getattr(getattr(self.store, "config", None), "current_user", None)])
        return result

    def ground_truth(self):
        return {"cases": build_expectations(self.bundle), "publicSources": deepcopy(self.bundle.public_sources),
                "documentIds": dict(self.document_ids), "dataManifest": deepcopy(self.bundle.manifest),
                "ticketFieldIds": {"sp": self.settings.sp_field_id, "epic": self.settings.epic_link_field_id}}

    def assert_conformance(self):
        raw = self.provider._client
        query = 'project = DL AND component = ETL ORDER BY key ASC'
        actual, offset = [], 0
        while True:
            response = raw.get("/rest/api/2/search", params={"jql": query, "startAt": offset, "maxResults": 500})
            assert response.status_code == 200, response.text
            page = response.json()
            actual.extend(row["key"] for row in page["issues"])
            offset += len(page["issues"])
            if offset >= page["total"]:
                break
            assert page["issues"], "ETL source search made no progress"
        assert len(actual) == 2245 - len(self.store.denied_issue_keys) and len(set(actual)) == len(actual)
        hot = raw.get("/rest/api/2/issue/DL-31000", params={"expand": "changelog"}).json()
        assert hot["changelog"]["total"] > 100
        comments = raw.get("/rest/api/2/issue/DL-31000/comment", params={"maxResults": 100, "startAt": 200,
            "orderBy": "created"}).json()
        assert comments["total"] > 200 and any("RUN-CUSTOMERS" in row["body"] for row in comments["comments"])
        # Probe the public long-dormant Task on a day before its first status
        # transition. A silently ignored WAS predicate would pass the positive
        # search but incorrectly return the same key for the negative search.
        from datetime import datetime, timedelta
        import json
        from zoneinfo import ZoneInfo
        identity = raw.get("/rest/api/2/myself")
        assert identity.status_code == 200, identity.text
        actor = identity.json()
        assert actor["name"] == self.bundle.manifest["currentUser"]
        assert actor["timeZone"] == self.bundle.manifest["timezone"]
        task = raw.get("/rest/api/2/issue/DL-21010", params={"expand": "changelog"})
        assert task.status_code == 200, task.text
        task = task.json()
        history = task["changelog"]
        assert history["startAt"] == 0 and history["total"] == len(history["histories"])
        transitions = [(datetime.fromisoformat(row["created"]), item)
                       for row in history["histories"] for item in row["items"]
                       if item.get("field") == "status"]
        changed, item = min(transitions, key=lambda pair: pair[0])
        day = changed.astimezone(ZoneInfo(actor["timeZone"])).date() - timedelta(days=1)
        assert datetime.fromisoformat(task["fields"]["created"]).date() < day
        assert item["fromString"] != item["toString"]
        probes = []
        for status, wanted in ((item["fromString"], ["DL-21010"]), (item["toString"], [])):
            jql = (f'key = DL-21010 AND status WAS {json.dumps(status)} '
                   f'DURING ("{day}", "{day}")')
            response = raw.get("/rest/api/2/search", params={"jql": jql})
            assert response.status_code == 200, response.text
            found = response.json()
            assert [row["key"] for row in found["issues"]] == wanted and found["total"] == len(wanted)
            probes.append({"jql": jql, "keys": wanted})
        unsupported = raw.get("/rest/api/2/search", params={
            "jql": 'key = DL-21010 OR status WAS Open BY unsupportedActor'})
        assert unsupported.status_code == 400, unsupported.text
        # Also exercise the observed broad monthly query: a singleton probe
        # cannot expose an inconsistent history elsewhere in the full corpus.
        # The independent oracle reads sourced status events, not this adapter.
        start, end = self.bundle.spec.months[-1]
        monthly_query = ('project = DL AND component = ETL AND issuetype != Epic '
                         'AND status WAS IN ("In Progress", Reopened) '
                         f'DURING ("{start.date()}", "{(end - timedelta(days=1)).date()}") ORDER BY key ASC')
        monthly = raw.get("/rest/api/2/search", params={"jql": monthly_query, "maxResults": 500})
        assert monthly.status_code == 200, monthly.text
        monthly = monthly.json()
        monthly_keys = {row["key"] for row in monthly["issues"]}
        expected_monthly = set(self.bundle.oracle.active_development(start, end)) - self.store.denied_issue_keys
        development = {event.issue_key for event in self.bundle.oracle.events
                       if event.kind == "issue" and event.stream == "development"}
        # Operations and VoC are also Tasks: the server returns candidates;
        # their business classification remains a separate investigation step.
        assert monthly_keys & development == expected_monthly
        assert monthly["total"] == len(monthly_keys)
        for identity, page_id in self.document_ids.items():
            response = raw.get("/rest/api/content/" + page_id, params={"expand": "body.storage,version"})
            assert response.status_code == 200 and response.json()["title"] == self.bundle.documents[identity]["title"]
        assert not self.upstream_calls
        return {"cacheWarmed": False, "etlIssueCount": len(actual), "documents": dict(self.document_ids),
                "dataManifest": deepcopy(self.bundle.manifest), "skillCondition": self.skill_condition,
                "statusHistorySearch": {"contract": self.status_history_search.contract, "probes": probes,
                                        "unsupportedStatus": unsupported.status_code,
                                        "monthlyActiveCount": len(monthly_keys & development),
                                        "monthlyCandidateCount": len(monthly_keys),
                                        "currentUser": actor["name"], "timeZone": actor["timeZone"]},
                "researchFiles": self.files is not None}

    def close(self):
        try:
            super().close()
        finally:
            # Only this fixture's verified, randomly allocated OS scratch is
            # removed. Shared investigation files belong to the run owner.
            installation = getattr(self, "status_history_search", None)
            if installation is not None and not (self._thread and self._thread.is_alive()):
                installation.uninstall()
            target = self._scratch.resolve()
            if target.parent != Path(tempfile.gettempdir()).resolve():
                raise RuntimeError("ETL scratch escaped the OS temporary directory")
            if target.exists() and not (self._thread and self._thread.is_alive()):
                shutil.rmtree(target)

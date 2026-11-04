"""Authored Korean identities over the actual Jira820 search/serialization path.

Only source records are replaced. No nickname, honorific or name-search behavior
is simulated here: the evaluated agent must resolve these through ordinary MCP.
"""
from __future__ import annotations

import copy
from datetime import date, timedelta
from unittest.mock import patch

from tools.codex_mcp_eval.fixture import EvaluationFixture, World, _hash

PEOPLE = (
    ("kang", "강수아 가람데이터", "강수아 가람데이터"),
    ("kim", "identity.sua.kim", "김수아 나래솔루션"),
    ("hanul", "정한울 가람데이터", "정한울 가람데이터"),
    ("jiyun", "identity.jiyun", "박지윤 나래솔루션"),
    ("jung_garam", "정민수 가람데이터", "정민수 가람데이터"),
    ("jung_narae", "identity.minsu.jung", "정민수 나래솔루션"),
    ("namgung", "남궁민수 두루시스템", "남궁민수 두루시스템"),
)
CONTEXT_TICKET = "DL-93001"
ACTOR_ID = World.ME
IDENTITY_PLAN = {"project_key": "DL", "modules": ["TEST"], "epics": {}, "wbs": []}
IDENTITY_ROSTER = {"TEST": [uid for _, uid, _ in PEOPLE] + [ACTOR_ID]}


def build_identity_data(today: date | str) -> dict:
    today = date.fromisoformat(today) if isinstance(today, str) else today
    users = {uid: {"name": uid, "displayName": display} for _, uid, display in PEOPLE}
    users[ACTOR_ID] = {"name": ACTOR_ID, "displayName": "검증계정 가상회사"}
    issues, identities = {}, {}
    for index, (alias, uid, display) in enumerate(PEOPLE):
        base = 93000 + index * 10
        keys = [f"DL-{base + offset}" for offset in (1, 2)]
        identities[alias] = {"id": uid, "displayName": display,
                             "openTaskKeys": keys, "total": len(keys)}
        for offset, kind, category, status in (
            (1, "Task", "open", "Open"), (2, "Task", "inprogress", "In Progress"),
            (3, "Task", "done", "Resolved"), (4, "Sub-Task", "inprogress", "In Progress"),
            (5, "Epic", "inprogress", "In Progress"),
        ):
            key = f"DL-{base + offset}"
            issues[key] = {"key": key, "project": "DL", "type": kind,
                "summary": f"이름 조회 검증 {index + 1}-{offset}",
                "description": "합성 업무 자료. 담당자는 Jira assignee 필드에서 확인하세요.",
                "module": "TEST", "component": "TEST", "assignee": uid, "reporter": uid,
                "statusCategory": category, "statusName": status, "priority": "P2-Major",
                "labels": ["identity_fixture"], "sp": 3 if kind == "Task" else None,
                "epicKey": f"DL-{base + 5}" if kind == "Task" else None,
                "epicName": f"이름 조회 검증 {index + 1}-5" if kind == "Epic" else None,
                "parentKey": keys[0] if kind == "Sub-Task" else None,
                "created": today - timedelta(days=5), "updated": today,
                "resolved": today if category == "done" else None, "due": None,
                "tcreated": "09:00", "tupdated": "10:00",
                "tresolved": "10:00" if category == "done" else None,
                "comments": [], "changelog": [], "worklog": [], "links": [], "remotelinks": [],
                "subtasks": [f"DL-{base + 4}"] if offset == 1 else []}
    return {"users": users, "issues": issues,
            "oracle": {"identities": identities, "contextTicket": CONTEXT_TICKET, "currentUserId": ACTOR_ID,
                       "contextAssignee": identities["kang"]["id"], "today": today.isoformat()}}


class IdentityFixture(EvaluationFixture):
    def configure_scope(self, settings):
        settings.search_jira_projects, settings.search_confluence_spaces = ["DL"], []
        settings.managers = [ACTOR_ID]

    def __enter__(self):
        try:
            from app.infra import settings
            from tools.codex_mcp_eval import fixture
            # module_dir may already be warm. Imported function aliases still read this global;
            # replace its cache for the fixture and restore the exact host object on exit.
            self._patches.enter_context(patch.object(settings, "_PEOPLE_CACHE", {"data": None, "dir": None}))
            # Patch both imported aliases and dynamic product imports before World/runtime setup.
            for module in (settings, fixture):
                self._patches.enter_context(patch.object(module, "load_plan", lambda: copy.deepcopy(IDENTITY_PLAN)))
                self._patches.enter_context(patch.object(module, "load_people", lambda: copy.deepcopy(IDENTITY_ROSTER)))
            super().__enter__()
            data = build_identity_data(self.today)
            self._identity_truth = data["oracle"]
            for owner in (self.world, self.store):
                owner.users = copy.deepcopy(data["users"])
                owner.issues = copy.deepcopy(data["issues"])
                owner.attachments, owner.confluence, owner.activity = {}, {}, {}
            self.world._index()
            self.store.denied_issue_keys = set()
            self.store.reindex()
            self._before = self.fingerprint()
            return self
        except BaseException:
            self.close()
            raise

    def fingerprint(self):
        result = super().fingerprint()
        # The base fixture fingerprints tickets; this battery also protects identities.
        result["worldSha256"] = _hash([result["worldSha256"], self.world.users])
        result["providerStoreSha256"] = _hash([result["providerStoreSha256"], self.store.users])
        return result

    def ground_truth(self):
        return {**copy.deepcopy(self._identity_truth), "users": copy.deepcopy(self.store.users),
                "issues": copy.deepcopy(self.store.issues),
                "readOnlyToolNames": sorted(tool["name"] for tool in self.tool_catalog)}

    def assert_conformance(self):
        """Host-only REST checks; no cache warming or fabricated alias endpoint."""
        raw, searches, queries = self.provider._client, {}, []
        actor = raw.get("/rest/api/2/myself").json()
        assert actor["name"] == ACTOR_ID and actor["displayName"] == self.store.users[ACTOR_ID]["displayName"]
        for query, aliases in (("수아", ("kang", "kim")), ("정민수", ("jung_garam", "jung_narae")),
                               ("남궁민수", ("namgung",)), ("유나", ())):
            response = raw.get("/rest/api/2/user/search", params={"username": query, "maxResults": 100})
            assert response.status_code == 200, response.text
            rows = response.json()
            expected = {self._identity_truth["identities"][alias]["id"] for alias in aliases}
            assert {row["name"] for row in rows} == expected, (query, rows)
            searches[query] = [{"name": row["name"], "displayName": row["displayName"]} for row in rows]
        for person in self._identity_truth["identities"].values():
            query = (f'project = DL AND assignee = "{person["id"]}" AND issuetype = Task '
                     'AND statusCategory != Done ORDER BY key ASC')
            response = raw.get("/rest/api/2/search", params={"jql": query, "maxResults": 100})
            assert response.status_code == 200, response.text
            page = response.json()
            assert [row["key"] for row in page["issues"]] == person["openTaskKeys"], page
            assert page["total"] == person["total"]
            assert all(row["fields"]["assignee"]["name"] == person["id"] and
                       row["fields"]["assignee"]["displayName"] == person["displayName"]
                       for row in page["issues"])
            queries.append({"jql": query, "keys": person["openTaskKeys"], "total": page["total"]})
        assert self.upstream_calls == [] and self.tool_calls == []
        return {"userSearch": searches, "quotedAssigneeJql": queries, "cacheWarmed": False, "currentUserId": actor["name"],
                "aliasSearchOverride": False, "usernameDistinctFromDisplayName": True}

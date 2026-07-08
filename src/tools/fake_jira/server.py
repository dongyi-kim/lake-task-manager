"""
Fake Jira/Confluence REST 서버 (Jira DC 8.20.8 형태).
app/world.py 의 단일 결정적 world 를 HTTP 로 서빙 → local provider 가 진짜 HTTP 로 붙는다.
설치·라이선스·DB 불필요. FAKE_LATENCY_MS 로 지연 주입(캐시 실측용).
"""

import asyncio
import hashlib
import os
import re

from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse

from app.world import get_world

from . import atom
from . import jql as jqlmod

app = FastAPI(title="Fake Jira DC 8.20.8")

# 사내 이슈타입 / 상태 (world 와 일치)
ISSUE_TYPES = [
    {"id": "1", "name": "Bug", "subtask": False},
    {"id": "2", "name": "Epic", "subtask": False},
    {"id": "3", "name": "Improvement", "subtask": False},
    {"id": "4", "name": "New Feature", "subtask": False},
    {"id": "5", "name": "Story", "subtask": False},
    {"id": "6", "name": "Task", "subtask": False},
    {"id": "7", "name": "Sub-Task", "subtask": True},
]
_CATS = {"Open": ("2", "new", "To Do"), "Reopened": ("2", "new", "To Do"),
         "In Progress": ("4", "indeterminate", "In Progress"),
         "Resolved": ("3", "done", "Done"), "Closed": ("3", "done", "Done")}
_STATUS_ID = {"Open": "1", "In Progress": "3", "Reopened": "4", "Resolved": "5", "Closed": "6"}
STATUSES = [{"id": _STATUS_ID[n], "name": n,
             "statusCategory": {"id": int(_CATS[n][0]), "key": _CATS[n][1], "name": _CATS[n][2]}}
            for n in ["Open", "In Progress", "Resolved", "Closed", "Reopened"]]


@app.middleware("http")
async def _latency(request: Request, call_next):
    ms = int(os.getenv("FAKE_LATENCY_MS", "0"))
    if ms:
        await asyncio.sleep(ms / 1000.0)
    return await call_next(request)


def _base(req):
    return str(req.base_url).rstrip("/")


def _iid(key):
    return str(int(hashlib.md5(key.encode()).hexdigest()[:8], 16))


def _issue_res(world, req, key):
    it = world.issues.get(key)
    if not it:
        return None
    return {"expand": "renderedFields,names,schema,transitions,operations,editmeta,changelog",
            "id": _iid(key), "self": f"{_base(req)}/rest/api/2/issue/{key}",
            "key": key, "fields": world.jira_fields(it)}


# ── 메타/인증 ──
@app.get("/rest/api/2/serverInfo")
def server_info(req: Request):
    return {"baseUrl": _base(req), "version": "8.20.8", "versionNumbers": [8, 20, 8],
            "deploymentType": "Server", "buildNumber": 820008,
            "serverTitle": "Fake Jira (Lake Task Manager dev)"}


@app.get("/rest/api/2/myself")
def myself():
    return {"self": "", "key": "admin", "name": "admin", "emailAddress": "admin@example.com",
            "displayName": "Administrator", "active": True, "timeZone": "Asia/Seoul"}


@app.get("/rest/api/2/user")
def user(username: str = ""):
    # displayName "{본명} {소속회사명}" 반환 (워크로드 본명 표시용). 미등록 id 는 최소 형태로.
    w = get_world()
    u = w.users.get(username)
    if u:
        return w._user_obj(username)
    return {"name": username, "key": username, "displayName": username, "active": True}


@app.get("/rest/api/2/field")
def fields():
    w = get_world()
    std = [
        {"id": "summary", "name": "Summary", "custom": False, "navigable": True, "searchable": True},
        {"id": "status", "name": "Status", "custom": False},
        {"id": "issuetype", "name": "Issue Type", "custom": False},
        {"id": "assignee", "name": "Assignee", "custom": False},
        {"id": "labels", "name": "Labels", "custom": False},
    ]
    custom = [
        {"id": w.sp_field, "name": "Story Points", "custom": True, "navigable": True,
         "searchable": True, "schema": {"type": "number", "custom":
                                        "com.atlassian.jira.plugin.system.customfieldtypes:float",
                                        "customId": 10004}},
        {"id": w.epic_link_field, "name": "Epic Link", "custom": True,
         "schema": {"type": "any", "custom":
                    "com.pyxis.greenhopper.jira:gh-epic-link", "customId": 10008}},
        {"id": "customfield_10011", "name": "Epic Name", "custom": True,
         "schema": {"type": "string", "custom":
                    "com.pyxis.greenhopper.jira:gh-epic-label", "customId": 10011}},
    ]
    return std + custom


@app.get("/rest/api/2/status")
def statuses():
    return STATUSES


@app.get("/rest/api/2/issuetype")
def issuetypes():
    return ISSUE_TYPES


@app.get("/rest/api/2/workflow")
def workflows():
    return [{"name": "LAKE Software Workflow", "description": "Open→In Progress→Resolved→Closed (+Reopened)",
             "steps": 5, "default": True}]


@app.get("/rest/api/2/project")
def projects(req: Request):
    w = get_world()
    return [{"id": "10000", "key": w.project, "name": "Lake Task Manager",
             "projectTypeKey": "software", "self": f"{_base(req)}/rest/api/2/project/{w.project}"}]


@app.get("/rest/api/2/project/{key}")
def project(key: str, req: Request):
    w = get_world()
    return {"id": "10000", "key": w.project, "name": "Lake Task Manager",
            "projectTypeKey": "software",
            "components": [{"name": m} for m in list(w.modules) + ["사용자 VoC"]],
            "issueTypes": ISSUE_TYPES}


@app.get("/rest/api/2/project/{key}/components")
def components(key: str):
    w = get_world()
    names = list(w.modules) + ["사용자 VoC"]      # 사용자 VoC = 고객의 소리성 업무 컴포넌트
    return [{"id": str(100 + i), "name": m} for i, m in enumerate(names)]


@app.get("/rest/api/2/project/{key}/statuses")
def project_statuses(key: str):
    # 각 이슈타입이 사용하는 상태 목록 (워크플로 상태명 확인용)
    return [{"id": t["id"], "name": t["name"], "subtask": t["subtask"], "statuses": STATUSES}
            for t in ISSUE_TYPES]


# ── search / issue / comment ──
@app.get("/rest/api/2/search")
def search(req: Request, jql: str = Query(""), startAt: int = 0,
           maxResults: int = 50, fields: str = ""):
    w = get_world()
    keys = jqlmod.filter_keys(w, jql)
    total = len(keys)
    page = keys[startAt:startAt + maxResults]
    return {"expand": "schema,names", "startAt": startAt, "maxResults": maxResults,
            "total": total, "issues": [_issue_res(w, req, k) for k in page]}


@app.get("/rest/api/2/issue/{key}")
def issue(key: str, req: Request):
    res = _issue_res(get_world(), req, key)
    return res or JSONResponse({"errorMessages": [f"Issue Does Not Exist: {key}"]}, status_code=404)


@app.get("/rest/api/2/issue/{key}/comment")
def comment(key: str, maxResults: int = 50, orderBy: str = "", startAt: int = 0):
    w = get_world()
    cs = w.jira_comments(key)
    return {"startAt": startAt, "maxResults": maxResults, "total": len(cs),
            "comments": cs[startAt:startAt + maxResults]}


@app.get("/rest/agile/1.0/epic/{key}/issue")
def agile_epic_issue(key: str, req: Request, startAt: int = 0, maxResults: int = 50):
    w = get_world()
    ch = w.epic_children.get(key, [])
    return {"startAt": startAt, "maxResults": maxResults, "total": len(ch),
            "issues": [_issue_res(w, req, k) for k in ch[startAt:startAt + maxResults]]}


# ── activity (ATOM) ──
@app.get("/activity")
def activity(req: Request, streams: str = "", maxResults: int = 20):
    w = get_world()
    m = re.search(r"user\s+IS\s+(\S+)", streams, re.I)
    user = m.group(1) if m else ""
    events = w.activity.get(user, [])
    xml = atom.feed(_base(req), user, events, maxResults)
    return Response(content=xml, media_type="application/atom+xml; charset=utf-8")


# ── Confluence CQL ──
@app.get("/rest/api/content/search")
def content_search(cql: str = "", limit: int = 25):
    w = get_world()
    m = re.search(r'contributor\s*=\s*"?([^"\s]+)"?', cql, re.I)
    user = m.group(1) if m else ""
    pages = w.confluence.get(user, [])
    results = [{"id": str(9000 + i), "type": "page", "title": p["title"],
                "space": {"key": p["space"]},
                "version": {"when": w._dt(p["date"], p.get("time"))},
                "history": {"createdBy": {"username": user}}}
               for i, p in enumerate(pages[:limit])]
    return {"results": results, "start": 0, "limit": limit, "size": len(results)}

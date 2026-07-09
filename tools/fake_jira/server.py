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

# 사내 이슈타입 / 상태 (world 와 일치) — 실 Jira DC 응답 필드 포함
_TYPE_NAMES = [(1, "Bug"), (2, "Epic"), (3, "Improvement"), (4, "New Feature"),
               (5, "Story"), (6, "Task"), (7, "Sub-Task")]
ISSUE_TYPES = [
    {"self": f"/rest/api/2/issuetype/{i}", "id": str(i), "description": "",
     "iconUrl": f"/secure/viewavatar?avatarType=issuetype&avatarId={10300 + i}"
                f"&type={n.lower().replace(' ', '').replace('-', '')}",
     "name": n, "subtask": (n == "Sub-Task"), "avatarId": 10300 + i}
    for i, n in _TYPE_NAMES
]
_CATCOLOR = {"new": "blue-gray", "indeterminate": "yellow", "done": "green"}
_CATS = {"Open": ("2", "new", "To Do"), "Reopened": ("2", "new", "To Do"),
         "In Progress": ("4", "indeterminate", "In Progress"),
         "Resolved": ("3", "done", "Done"), "Closed": ("3", "done", "Done")}
_STATUS_ID = {"Open": "1", "In Progress": "3", "Reopened": "4", "Resolved": "5", "Closed": "6"}
STATUSES = [{"self": f"/rest/api/2/status/{_STATUS_ID[n]}", "description": "",
             "iconUrl": f"/images/icons/statuses/{_CATS[n][1]}.png",
             "name": n, "id": _STATUS_ID[n],
             "statusCategory": {"self": f"/rest/api/2/statuscategory/{_CATS[n][0]}",
                                "id": int(_CATS[n][0]), "key": _CATS[n][1],
                                "colorName": _CATCOLOR[_CATS[n][1]], "name": _CATS[n][2]}}
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


def _project_fields(allf, fields):
    """실 Jira 처럼 `fields` 파라미터에 명시된 것만 투영. *all/*navigable/빈값 → 전체."""
    if not fields:
        return allf
    want = {x.strip() for x in fields.split(",") if x.strip()}
    if not want or "*all" in want or "*navigable" in want:
        return allf
    return {k: v for k, v in allf.items() if k in want}


def _issue_res(world, req, key, fields=None):
    it = world.issues.get(key)
    if not it:
        return None
    return {"expand": "renderedFields,names,schema,transitions,operations,editmeta,changelog",
            "id": _iid(key), "self": f"{_base(req)}/rest/api/2/issue/{key}",
            "key": key, "fields": _project_fields(world.jira_fields(it), fields)}


# ── 메타/인증 ──
def _avatars(owner):
    return {"48x48": f"/secure/useravatar?ownerId={owner}&avatarId=10122",
            "32x32": f"/secure/useravatar?size=medium&ownerId={owner}&avatarId=10122",
            "24x24": f"/secure/useravatar?size=small&ownerId={owner}&avatarId=10122",
            "16x16": f"/secure/useravatar?size=xsmall&ownerId={owner}&avatarId=10122"}


@app.get("/rest/api/2/serverInfo")
def server_info(req: Request):
    return {"baseUrl": _base(req), "version": "8.20.8", "versionNumbers": [8, 20, 8],
            "deploymentType": "Server", "buildNumber": 820008,
            "buildDate": "2022-05-31T00:00:00.000+0000",
            "databaseBuildNumber": 820008,
            "scmInfo": "0000000000000000000000000000000000000000",
            "serverTime": "2026-07-08T09:00:00.000+0000",
            "serverTitle": "Fake Jira (Lake Task Manager dev)"}


@app.get("/rest/api/2/myself")
def myself(req: Request):
    return {"self": f"{_base(req)}/rest/api/2/user?username=admin",
            "key": "admin", "name": "admin", "emailAddress": "admin@example.com",
            "avatarUrls": _avatars("admin"), "displayName": "Administrator",
            "active": True, "deleted": False, "timeZone": "Asia/Seoul", "locale": "ko_KR",
            "groups": {"size": 1, "items": []},
            "applicationRoles": {"size": 1, "items": []},
            "expand": "groups,applicationRoles"}


@app.get("/rest/api/2/user")
def user(req: Request, username: str = "", key: str = ""):
    # DC 8.20.8: username(or key) 파라미터. displayName "{본명} {소속회사명}" 반환.
    w = get_world()
    uid = username or key
    u = w.users.get(uid)
    if u:
        obj = dict(w._user_obj(uid))
        obj["self"] = f"{_base(req)}/rest/api/2/user?username={uid}"
        obj["locale"] = "ko_KR"
        obj["deleted"] = False
        return obj
    return {"self": f"{_base(req)}/rest/api/2/user?username={uid}", "name": uid, "key": uid,
            "avatarUrls": _avatars(uid), "displayName": uid, "active": True,
            "deleted": False, "timeZone": "Asia/Seoul", "locale": "ko_KR"}


@app.get("/rest/api/2/field")
def fields():
    w = get_world()
    # system 필드: clauseNames = JQL 에서 쓰는 이름(id 와 동일), schema.system 지정
    def sysf(fid, name, stype, clause=None):
        return {"id": fid, "name": name, "custom": False, "orderable": True,
                "navigable": True, "searchable": True,
                "clauseNames": clause or [fid], "schema": {"type": stype, "system": fid}}
    std = [
        sysf("summary", "Summary", "string"),
        sysf("status", "Status", "status"),
        sysf("issuetype", "Issue Type", "issuetype", ["issuetype", "type"]),
        sysf("assignee", "Assignee", "user"),
        sysf("reporter", "Reporter", "user"),
        sysf("components", "Component/s", "array", ["component"]),
        sysf("labels", "Labels", "array"),
        sysf("created", "Created", "datetime", ["created", "createdDate"]),
        sysf("updated", "Updated", "datetime", ["updated", "updatedDate"]),
        sysf("resolutiondate", "Resolved", "datetime", ["resolved", "resolutiondate"]),
        sysf("duedate", "Due Date", "date", ["due", "duedate"]),
    ]

    def cf(fid, name, stype, custom_key, cid):
        return {"id": fid, "name": name, "custom": True, "orderable": True,
                "navigable": True, "searchable": True,
                "clauseNames": [f"cf[{cid}]", name],
                "schema": {"type": stype, "custom": custom_key, "customId": cid}}
    custom = [
        cf(w.sp_field, "Story Points", "number",
           "com.atlassian.jira.plugin.system.customfieldtypes:float", 10004),
        cf(w.epic_link_field, "Epic Link", "any",
           "com.pyxis.greenhopper.jira:gh-epic-link", 10008),
        cf("customfield_10011", "Epic Name", "string",
           "com.pyxis.greenhopper.jira:gh-epic-label", 10011),
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
    return [{"expand": "description,lead,url,projectKeys",
             "self": f"{_base(req)}/rest/api/2/project/{w.project}",
             "id": "10000", "key": w.project, "name": "Lake Task Manager",
             "avatarUrls": _avatars(w.project), "projectTypeKey": "software"}]


def _component_list(req):
    w = get_world()
    names = list(w.modules) + ["사용자 VoC"]      # 사용자 VoC = 고객의 소리성 업무 컴포넌트
    return [{"self": f"{_base(req)}/rest/api/2/component/{100 + i}",
             "id": str(100 + i), "name": m, "isAssigneeTypeValid": False}
            for i, m in enumerate(names)]


@app.get("/rest/api/2/project/{key}")
def project(key: str, req: Request):
    w = get_world()
    return {"expand": "description,lead,issueTypes,url,projectKeys,permissions",
            "self": f"{_base(req)}/rest/api/2/project/{w.project}",
            "id": "10000", "key": w.project, "name": "Lake Task Manager",
            "avatarUrls": _avatars(w.project), "projectTypeKey": "software",
            "components": _component_list(req), "issueTypes": ISSUE_TYPES,
            "assigneeType": "UNASSIGNED", "versions": []}


@app.get("/rest/api/2/project/{key}/components")
def components(key: str, req: Request):
    return _component_list(req)


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
            "total": total, "issues": [_issue_res(w, req, k, fields) for k in page]}


@app.get("/rest/api/2/issue/{key}")
def issue(key: str, req: Request, fields: str = ""):
    res = _issue_res(get_world(), req, key, fields)
    return res or JSONResponse(
        {"errorMessages": [f"Issue Does Not Exist: {key}"], "errors": {}}, status_code=404)


@app.get("/rest/api/2/issue/{key}/comment")
def comment(key: str, maxResults: int = 50, orderBy: str = "", startAt: int = 0):
    w = get_world()
    cs = w.jira_comments(key)
    return {"startAt": startAt, "maxResults": maxResults, "total": len(cs),
            "comments": cs[startAt:startAt + maxResults]}


@app.get("/rest/agile/1.0/epic/{key}/issue")
def agile_epic_issue(key: str, req: Request, startAt: int = 0, maxResults: int = 50, fields: str = ""):
    w = get_world()
    ch = w.epic_children.get(key, [])
    page = ch[startAt:startAt + maxResults]
    return {"startAt": startAt, "maxResults": maxResults, "total": len(ch),
            "isLast": startAt + len(page) >= len(ch),
            "issues": [_issue_res(w, req, k, fields) for k in page]}


# ── activity (ATOM) ──
@app.get("/activity")
def activity(req: Request, streams: str = "", maxResults: int = 20):
    w = get_world()
    m = re.search(r"user\s+IS\s+(\S+)", streams, re.I)
    user = m.group(1) if m else ""
    events = w.activity.get(user, [])
    dn = (w.users.get(user) or {}).get("displayName", user)
    xml = atom.feed(_base(req), user, events, maxResults, display_name=dn)
    return Response(content=xml, media_type="application/atom+xml; charset=utf-8")


# ── Confluence CQL ──
@app.get("/rest/api/content/search")
def content_search(cql: str = "", limit: int = 25, expand: str = ""):
    # 실 Confluence 처럼 expand 에 명시된 것만 확장(version/space/history). 없으면 최소 형태.
    w = get_world()
    exp = {e.strip() for e in expand.split(",") if e.strip()}
    m = re.search(r'contributor\s*=\s*"?([^"\s]+)"?', cql, re.I)
    user = m.group(1) if m else ""
    pages = w.confluence.get(user, [])
    results = []
    for i, p in enumerate(pages[:limit]):
        r = {"id": str(9000 + i), "type": "page", "status": "current", "title": p["title"]}
        if "space" in exp:
            r["space"] = {"key": p["space"], "name": p["space"]}
        if "version" in exp:
            r["version"] = {"when": w._dt(p["date"], p.get("time")), "number": 1}
        if "history" in exp:
            r["history"] = {"createdBy": {"username": user}}
        results.append(r)
    return {"results": results, "start": 0, "limit": limit, "size": len(results)}

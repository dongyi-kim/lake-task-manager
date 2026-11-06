"""App-scoped Confluence 9.2.4 published-document fixture; no external service writes."""
import copy
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .confluence_state import ConfluenceFixtureError, ConfluenceState


def install_confluence(app):
    state = app.state.confluence = ConfluenceState(app.state.store)
    router = APIRouter()

    @app.exception_handler(ConfluenceFixtureError)
    async def error(_request, exc):
        return JSONResponse({"statusCode": exc.status, "message": exc.message}, status_code=exc.status)

    def listing(request, rows, start, limit):
        if start < 0 or not 1 <= limit <= 100:
            raise ConfluenceFixtureError(400, "Invalid pagination")
        result = rows[start:start + limit]
        links = {"base": str(request.base_url).rstrip("/")}
        if start + len(result) < len(rows):
            links["next"] = request.url.path + f"?start={start + len(result)}&limit={limit}"
        return {"results": result, "start": start, "limit": limit, "size": len(result),
                "totalSize": len(rows), "_links": links}

    def serialize(request, row, expand=""):
        return state.content(row, str(request.base_url).rstrip("/"), expand)

    @router.get("/rest/api/search")
    @router.get("/rest/api/content/search")
    def search(request: Request, cql: str = "", start: int = 0, limit: int = 25, expand: str = ""):
        rows = state.search(cql)
        results = []
        for row in rows:
            content = serialize(request, row, expand.replace("content.", ""))
            results.append(content if request.url.path.endswith("/content/search") else {
                "content": content, "title": row["title"], "excerpt": row["title"],
                "url": content["_links"]["webui"], "lastModified": row["version"]["when"]})
        return listing(request, results, start, limit)

    @router.get("/rest/api/space")
    def spaces(request: Request, start: int = 0, limit: int = 50):
        state.seed()
        keys = sorted({r["space"]["key"] for r in state.pages.values() if r["id"] not in state.denied})
        return listing(request, [{"key": k, "name": k, "type": "global"} for k in keys], start, limit)

    @router.get("/rest/api/space/{space}/content/page")
    def roots(space: str, request: Request, depth: str = "all", start: int = 0, limit: int = 50, expand: str = ""):
        state.seed()
        rows = [r for r in state.pages.values() if r["type"] == "page" and r["status"] == "current"
                and r["space"]["key"] == space and (depth != "root" or not r["parent"]) and r["id"] not in state.denied]
        rows.sort(key=lambda r: (r["position"], r["title"]))
        return listing(request, [serialize(request, r, expand) for r in rows], start, limit)

    @router.get("/rest/api/content")
    def content_list(request: Request, spaceKey: str = "", title: str = "", type: str = "page",
                     status: str = "current", start: int = 0, limit: int = 50, expand: str = ""):
        state.seed()
        rows = [r for r in state.pages.values() if r["status"] == status and r["type"] == type
                and (not spaceKey or r["space"]["key"] == spaceKey) and (not title or r["title"] == title)
                and r["id"] not in state.denied]
        return listing(request, [serialize(request, r, expand) for r in rows], start, limit)

    @router.post("/rest/api/content")
    async def create(request: Request):
        payload = await request.json()
        state.seed()
        with state.lock:
            parent = str((payload.get("ancestors") or [{}])[-1].get("id") or (payload.get("container") or {}).get("id") or "")
            if parent:
                state.get(parent)
            row = state.create(payload)
            return serialize(request, row, "body.storage")

    @router.get("/rest/api/content/{cid}")
    def get(cid: str, request: Request, expand: str = "", status: str = "current", version: int | None = None):
        return serialize(request, state.get(cid, status=status, version=version), expand)

    @router.put("/rest/api/content/{cid}")
    async def update(cid: str, request: Request):
        payload = await request.json()
        with state.lock:
            row = state.update(cid, payload)
            return serialize(request, row, "body.storage")

    @router.delete("/rest/api/content/{cid}")
    def delete(cid: str):
        with state.lock:
            row = state.get(cid)
            row["status"] = "trashed"
        return Response(status_code=204)

    @router.get("/rest/api/content/{cid}/history")
    def history(cid: str):
        row = state.get(cid)
        number = row["version"]["number"]
        return {"latest": True, "lastUpdated": row["version"],
                "previousVersion": state.versions[cid].get(number - 1, {}).get("version")}

    @router.get("/rest/api/content/{cid}/child/{kind}")
    def children(cid: str, kind: str, request: Request, start: int = 0, limit: int = 50, expand: str = ""):
        state.get(cid)
        rows = [r for r in state.pages.values() if r["status"] == "current" and r["type"] == kind
                and (r.get("parent") == cid if kind == "page" else r.get("container", {}).get("id") == cid)
                and r["id"] not in state.denied]
        rows.sort(key=lambda r: (r["position"], r["title"]))
        return listing(request, [serialize(request, r, expand) for r in rows], start, limit)

    @router.post("/rest/api/content/{cid}/child/attachment")
    async def attach(cid: str, request: Request):
        state.get(cid)
        form = await request.form()
        upload = form["file"]
        data = await upload.read()
        with state.lock:
            row = state.create({"type": "attachment", "title": upload.filename, "container": {"id": cid}})
            state.files[row["id"]] = (data, upload.content_type or "application/octet-stream")
        return {"results": [serialize(request, row)]}

    @router.delete("/rest/api/content/{cid}/child/attachment/{aid}")
    def delete_attachment(cid: str, aid: str):
        state.get(cid)
        row = state.get(aid)
        if row.get("container", {}).get("id") != cid:
            raise ConfluenceFixtureError(404, "Attachment is not in this page")
        row["status"] = "trashed"
        return Response(status_code=204)

    @router.get("/download/attachments/{cid}/{filename}")
    def download(cid: str, filename: str, version: int = 1):
        state.get(cid)
        row = next((r for r in state.pages.values() if r["type"] == "attachment" and r["status"] == "current"
                    and r.get("container", {}).get("id") == cid and r["title"] == filename), None)
        if row is None:
            raise ConfluenceFixtureError(404, "Attachment not found")
        state.get(row["id"], version=version)
        data, mime = state.files[row["id"]]
        return Response(data, media_type=mime)

    @router.get("/rest/api/content/{cid}/label")
    def labels(cid: str, request: Request, start: int = 0, limit: int = 50):
        state.get(cid)
        return listing(request, [{"prefix": "global", "name": name} for name in sorted(state.labels.get(cid, set()))], start, limit)

    @router.post("/rest/api/content/{cid}/label")
    async def add_labels(cid: str, request: Request):
        state.get(cid)
        payload = await request.json()
        state.labels.setdefault(cid, set()).update(row["name"] for row in payload)
        return labels(cid, request)

    @router.delete("/rest/api/content/{cid}/label")
    def delete_label(cid: str, name: str):
        state.get(cid)
        state.labels.setdefault(cid, set()).discard(name)
        return Response(status_code=204)

    @router.get("/rest/api/content/{cid}/restriction/byOperation")
    def restrictions(cid: str):
        state.get(cid)
        return copy.deepcopy(state.restrictions.get(cid) or {
            op: {"operation": op, "restrictions": {"user": {"results": [], "size": 0}, "group": {"results": [], "size": 0}}}
            for op in ("read", "update")})

    @router.put("/rest/api/content/{cid}/restriction")
    async def set_restrictions(cid: str, request: Request):
        value = restrictions(cid)
        for row in await request.json():
            subjects = row.get("restrictions") or {}
            value[row["operation"]] = {"operation": row["operation"], "restrictions": {
                kind: {"results": subjects.get(kind, []), "size": len(subjects.get(kind, []))} for kind in ("user", "group")}}
        state.restrictions[cid] = value
        return value

    app.include_router(router)
    app.router.routes.insert(0, app.router.routes.pop())

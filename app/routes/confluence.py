"""Read-only document browser, sharing the MCP Confluence service and cache."""
from html import escape
from html.parser import HTMLParser
import re
from urllib.parse import quote, urljoin, urlsplit

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, Response

from app.auth.base import PermissionDenied, SessionExpired, UpstreamError, UpstreamUnavailable
from app.content.htmlsafe import sanitize_html
from app.jira import confluence_service as service


def _page_id(value):
    if not re.fullmatch(r"[0-9]+", value):
        raise ValueError("숫자 문서 ID가 필요합니다.")
    return value


def _spaces(client):
    values = [str(value) for value in getattr(client.s, "search_confluence_spaces", []) if str(value).strip()]
    if not values:
        raise ValueError("Confluence 문서를 보려면 search.confluence.spaces에 Space를 설정해 주세요.")
    return values


def _check_space(client, space):
    if not space or str(space).casefold() not in {value.casefold() for value in _spaces(client)}:
        raise PermissionDenied("설정된 Confluence Space 범위 밖입니다.")


def _media_path(base, page_id, value):
    target = urlsplit(urljoin(base.rstrip("/") + "/", value))
    origin = urlsplit(base)
    root = origin.path.rstrip("/")
    if target.scheme not in ("http", "https") or target.netloc != origin.netloc:
        return None
    if not any(target.path.startswith(root + f"/download/{kind}/{page_id}/")
               for kind in ("attachments", "thumbnails")):
        return None
    # Path traversal must not escape the selected page's attachment path after decoding.
    from urllib.parse import unquote
    if any(part in ("..", ".") for part in unquote(target.path).split("/")) or "\\" in unquote(target.path):
        return None
    return target.path + (("?" + target.query) if target.query else "")


def document_html(raw, base, page_id):
    """Sanitize server-rendered macros; resolve links and proxy only this page's media."""
    # Retain known native macro intent through the shared restrictive class allowlist.
    # This affects display only; Confluence storage is never rewritten as Jira panels.
    def panel_class(match):
        mapping = {"information": "info", "info": "info", "note": "note", "tip": "tip", "warning": "warning"}
        classes = match[3].split()
        for name, kind in mapping.items():
            if "confluence-information-macro-" + name in classes:
                classes += ["callout", "callout-" + kind]
                break
        return match[1] + match[2] + " ".join(classes) + match[2]
    raw = re.sub(r'''(\bclass\s*=\s*)(["'])(.*?)\2''', panel_class, raw, flags=re.I | re.S)
    class Links(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.out = []

        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == "img":
                path = _media_path(base, page_id, values.get("src", ""))
                if not path:
                    self.out.append('<span class="conf-media-unavailable">' + escape(values.get("alt") or "외부 이미지") + "</span>")
                    return
                values["src"] = f"/api/confluence/pages/{page_id}/image?path=" + quote(path, safe="")
                values["loading"] = "lazy"
            if tag == "a" and values.get("href"):
                path = _media_path(base, page_id, values["href"])
                values["href"] = (f"/api/confluence/pages/{page_id}/file?path=" + quote(path, safe="")
                                  if path else urljoin(base.rstrip("/") + "/", values["href"]))
            if tag == "input":
                values["disabled"] = "disabled"
            self.out.append("<" + tag + "".join(" " + k + '="' + escape(v or "", quote=True) + '"'
                                              for k, v in values.items()) + ">")

        handle_startendtag = handle_starttag

        def handle_endtag(self, tag):
            self.out.append("</" + tag + ">")

        def handle_data(self, data):
            self.out.append(escape(data))

    parser = Links()
    parser.feed(sanitize_html(raw))
    parser.close()
    return "".join(parser.out)


def build_confluence_router(*, get_client):
    router = APIRouter(prefix="/api/confluence")

    @router.get("/config")
    def config():
        client = get_client()
        return JSONResponse({"base": (client.s.confluence_base or "").rstrip("/"),
                             "spaces": getattr(client.s, "search_confluence_spaces", [])},
                            headers={"Cache-Control": "no-store"})

    def reply(callback):
        try:
            return JSONResponse(callback(), headers={"Cache-Control": "no-store"})
        except ValueError as exc:
            return JSONResponse({"error": str(exc), "service": "confluence"}, status_code=422)
        except PermissionDenied:
            return JSONResponse({"error": "이 Confluence 문서를 볼 권한이 없습니다.", "service": "confluence"}, status_code=403)
        except UpstreamError as exc:
            status = getattr(exc, "status", 502)
            status = status if status in (401, 403, 404, 409, 429) else 502
            return JSONResponse({"error": "Confluence 문서를 불러오지 못했습니다.", "service": "confluence",
                                 "needLogin": status == 401}, status_code=status)
        except SessionExpired:
            return JSONResponse({"error": "Confluence 인증을 복구하지 못했습니다. 연결 상태를 확인한 뒤 다시 시도해 주세요.",
                                 "service": "confluence", "needLogin": True}, status_code=401)
        except UpstreamUnavailable:
            return JSONResponse({"error": "Confluence 연결이 응답하지 않습니다. 잠시 후 다시 시도해 주세요.",
                                 "service": "confluence", "retryable": True}, status_code=503)

    @router.get("/spaces")
    def spaces(start: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
        def read():
            client = get_client()
            allowed = {value.casefold() for value in _spaces(client)}
            data = service.list_spaces(client, start=start, limit=limit)
            # Keep the upstream cursor/size even if this page contains no configured Spaces.
            return {**data, "results": [row for row in data.get("results", []) if str(row.get("key", "")).casefold() in allowed]}
        return reply(read)

    @router.get("/pages")
    def pages(space: str = "", parent: str = "", start: int = Query(0, ge=0),
              limit: int = Query(50, ge=1, le=100)):
        def read():
            client = get_client()
            _check_space(client, space)
            return service.list_pages(client, space_key=space, parent_id=parent, start=start, limit=limit)
        return reply(read)

    @router.get("/search")
    def search(q: str = Query("", max_length=1000), space: str = Query("", max_length=255),
               start: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100)):
        def run():
            import json
            client = get_client()
            clauses = ['type = page']
            if q.strip():
                clauses.append("text ~ " + json.dumps(q.strip(), ensure_ascii=False))
            if space:
                _check_space(client, space)
                clauses.append("space = " + json.dumps(space, ensure_ascii=False))
            else:
                clauses.append("space in (" + ",".join(json.dumps(value) for value in _spaces(client)) + ")")
            return service.search(client, " AND ".join(clauses) + " ORDER BY lastmodified DESC", start, limit)
        return reply(run)

    @router.get("/pages/{page_id}")
    def page(page_id: str, version: int | None = Query(None, ge=1)):
        def read():
            client = get_client()
            current = service.head(client, _page_id(page_id))
            _check_space(client, (current.get("space") or {}).get("key"))
            data = service.body(client, page_id, version=version, representation="view")
            _check_space(client, (data.get("space") or {}).get("key"))
            base = (client.s.confluence_base or "").rstrip("/")
            result = dict(data)
            # body.view does not expand ancestors. Navigation follows the current
            # tree even when the reader is displaying a pinned historical body.
            result["ancestors"] = current.get("ancestors") or []
            result["html"] = document_html((data.get("body", {}).get("view") or {}).get("value", ""), base, page_id)
            result.pop("body", None)
            result["url"] = base + f"/pages/viewpage.action?pageId={page_id}"
            result["confluenceBase"] = base
            return result
        return reply(read)

    @router.get("/pages/{page_id}/revision")
    def revision(page_id: str, known_version: int | None = Query(None, ge=1)):
        def read():
            client = get_client()
            current = service.head(client, _page_id(page_id))
            _check_space(client, (current.get("space") or {}).get("key"))
            result = service.check_revision(client, page_id, known_version)
            current = service.head(client, page_id)  # check_revision just refreshed this cache entry
            _check_space(client, (current.get("space") or {}).get("key"))
            return result
        return reply(read)

    @router.get("/pages/{page_id}/image")
    def image(page_id: str, path: str = Query(..., max_length=2048)):
        client = get_client()
        try:
            _page_id(page_id)
            base = (client.s.confluence_base or "").rstrip("/")
            checked = _media_path(base, page_id, path)
            if not checked:
                return Response(status_code=403)
            current = service.head(client, page_id)
            _check_space(client, (current.get("space") or {}).get("key"))
            url = urlsplit(base)
            target = f"{url.scheme}://{url.netloc}{checked}"
            try:
                data, mime = client.provider.get_bytes(target)
            except SessionExpired as exc:
                if isinstance(exc, UpstreamError) and exc.status != 401:
                    raise
                if not client._renew_service("Confluence"):
                    raise
                data, mime = client.provider.get_bytes(target)
            mime = (mime or "").split(";")[0].lower()
            if mime not in ("image/png", "image/jpeg", "image/gif", "image/webp", "image/avif"):
                return Response(status_code=415)
            return Response(data, media_type=mime, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
        except UpstreamError as exc:
            return Response(status_code=exc.status if exc.status in (401, 403, 404) else 502)
        except SessionExpired:
            return Response(status_code=401)
        except UpstreamUnavailable:
            return Response(status_code=503)
        except (ValueError, PermissionDenied):
            return Response(status_code=403)

    return router

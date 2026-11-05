"""Mutable DC document fixture. This models published versions, never Synchrony."""
import copy
import hashlib
import html
import re
import threading
from datetime import datetime, timezone

from app.jira.jql import And, Atom, Not, _Parser, _split_order, tokenize


class ConfluenceFixtureError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class ConfluenceState:
    def __init__(self, store):
        self.store, self.lock = store, threading.RLock()
        self.pages, self.versions, self.files, self.labels, self.restrictions = {}, {}, {}, {}, {}
        self.denied, self.initialized, self.counter = set(), False, 900000000000

    def seed(self):
        with self.lock:
            if self.initialized:
                return
            for original in self.store.confluence_pages():
                space, parent, trail = str(original.get("space") or "DL"), "", []
                for name in original.get("ancestors") or []:
                    name = name.get("title", "") if isinstance(name, dict) else str(name)
                    trail.append(name)
                    pid = str(800000000000 + int(hashlib.sha256((space + "/" + "/".join(trail)).encode()).hexdigest()[:9], 16))
                    if pid not in self.pages:
                        self.create({"id": pid, "title": name, "space": {"key": space},
                                     "ancestors": [{"id": parent}] if parent else []})
                    parent = pid
                text = str(original.get("body") or original.get("title") or "")
                storage = text if text.lstrip().startswith("<") else "<p>" + html.escape(text).replace("\n", "<br />") + "</p>"
                day = original.get("date")
                when = (day.isoformat() + "T00:00:00+00:00") if day else self.now()
                self.create({"id": str(original["id"]), "title": original.get("title", ""),
                             "space": {"key": space}, "ancestors": [{"id": parent}] if parent else [],
                             "body": {"storage": {"value": storage}},
                             "version": {"number": 1, "when": when, "by": {"username": original.get("author", "")}}})
            self.initialized = True

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    def create(self, payload):
        cid = str(payload.get("id") or self.counter)
        self.counter += 1
        kind = payload.get("type", "page")
        parent = str((payload.get("ancestors") or [{}])[-1].get("id") or "")
        container = str((payload.get("container") or {}).get("id") or "")
        space = (payload.get("space") or {}).get("key") or (self.pages.get(container) or {}).get("space", {}).get("key", "DL")
        row = {"id": cid, "type": kind, "status": "current", "title": payload.get("title", ""),
               "space": {"key": space, "name": space, "type": "global"}, "parent": parent,
               "position": len(self.pages), "container": {"id": container, "type": "page"} if container else {},
               "version": {"number": 1, "when": self.now(), "by": {"username": self.store.config.current_user}, **payload.get("version", {})},
               "body": copy.deepcopy(payload.get("body") or {"storage": {"value": "", "representation": "storage"}})}
        row["body"]["storage"]["representation"] = "storage"
        self.pages[cid] = row
        self.versions[cid] = {1: copy.deepcopy(row)}
        return row

    def get(self, cid, *, status="current", version=None):
        self.seed()
        cid = str(cid)
        if cid in self.denied:
            raise ConfluenceFixtureError(403, "No permission to view this document")
        row = self.pages.get(cid)
        if not row or (status and row["status"] != status):
            raise ConfluenceFixtureError(404, "Content not found")
        if version is not None:
            row = self.versions[cid].get(int(version))
            if row is None:
                raise ConfluenceFixtureError(404, "Version not found")
        return row

    def update(self, cid, payload):
        row = self.get(cid, status=None)
        if (payload.get("version") or {}).get("number") != row["version"]["number"] + 1:
            raise ConfluenceFixtureError(409, "Version conflict")
        if payload.get("status") == "draft":
            raise ConfluenceFixtureError(400, "Draft editing is outside this fixture")
        candidate = copy.deepcopy(row)
        for field in ("title", "body", "status", "position"):
            if field in payload:
                candidate[field] = copy.deepcopy(payload[field])
        if "ancestors" in payload:
            candidate["parent"] = str((payload["ancestors"] or [{}])[-1].get("id") or "")
        candidate["version"] = {"when": self.now(), "by": {"username": self.store.config.current_user}, **payload["version"]}
        self.pages[str(cid)] = candidate
        self.versions[str(cid)][candidate["version"]["number"]] = copy.deepcopy(candidate)
        return candidate

    def ancestors(self, row):
        values, seen = [], set()
        parent = row.get("parent")
        while parent and parent not in seen:
            seen.add(parent)
            item = self.pages.get(parent)
            if not item:
                break
            values.insert(0, {"id": parent, "title": item["title"], "type": "page"})
            parent = item.get("parent")
        return values

    def content(self, row, base="", expand=""):
        data = copy.deepcopy(row)
        cid = row["id"]
        data.pop("parent", None)
        data["ancestors"] = self.ancestors(row)
        data["_links"] = {"webui": f"/spaces/{row['space']['key']}/pages/{cid}", "self": base + f"/rest/api/content/{cid}"}
        if row["type"] == "attachment":
            data["_links"]["download"] = f"/download/attachments/{row['container']['id']}/{row['title']}"
            data["extensions"] = {"fileSize": len(self.files[cid][0]), "mediaType": self.files[cid][1]}
        if "body.view" in expand:
            data["body"]["view"] = {"value": row["body"]["storage"]["value"], "representation": "view"}
        if "body." not in expand:
            data.pop("body", None)
        return data

    def search(self, cql):
        from jira820.cql import _pred
        self.seed()
        tokens, order = _split_order(tokenize(cql))
        ast = _Parser(tokens).parse() if tokens else None
        def matches(node, row):
            if node is None:
                return True
            if isinstance(node, Not):
                return not matches(node.child, row)
            if not isinstance(node, Atom):
                items = [matches(n, row) for n in node.children]
                return all(items) if isinstance(node, And) else any(items)
            atom = tokenize(node.value)
            field, op = atom[0].value.lower(), atom[1].value.upper()
            values = [t.value.strip("\"'") for t in atom[2:] if t.kind in ("WORD", "STRING")]
            wanted = values[0] if values else ""
            if field in ("title", "type", "parent", "id") and op in ("=", "!=", "IN"):
                match = str(row.get(field, "")) in values
                return not match if op == "!=" else match
            if field == "ancestor":
                return wanted in [r["id"] for r in self.ancestors(row)]
            if field == "label":
                return wanted in self.labels.get(row["id"], set())
            pred = _pred(node.value, datetime.now(timezone.utc).date())
            if pred is None:
                raise ConfluenceFixtureError(400, "Unsupported fixture CQL clause: " + node.value)
            return pred({**row, "space": row["space"]["key"], "body": row["body"]["storage"]["value"],
                         "author": row["version"].get("by", {}).get("username"),
                         "date": datetime.fromisoformat(row["version"]["when"]).date()})
        rows = [r for r in self.pages.values() if r["status"] == "current" and r["id"] not in self.denied and matches(ast, r)]
        by_title = bool(order and order[0].value.lower() == "title")
        reverse = any(t.value.upper() == "DESC" for t in order) if order else True
        rows.sort(key=lambda r: (r["title"].casefold() if by_title else r["version"]["when"], r["id"]), reverse=reverse)
        return rows

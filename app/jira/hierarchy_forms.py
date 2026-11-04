"""Jira 8.20 native hierarchy wizards (not a REST issue-field shortcut).

The action aliases and controls are verified against Atlassian's 8.20.8 WAR:
WEB-INF/classes/actions.xml and secure/views/issue/convertissuetosubtask-*.jsp.
The caller serializes a whole wizard, authorizes its changes and owns receipts/cache invalidation.
"""

from dataclasses import dataclass, field
from html.parser import HTMLParser
import json
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit


class HierarchyFormError(ValueError):
    def __init__(self, message, *, code="unsupported_hierarchy_form", status=409):
        super().__init__(message)
        self.code, self.status = code, status


@dataclass
class _Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    def nodes(self, tags=None):
        for child in self.children:
            if isinstance(child, _Node):
                if tags is None or child.tag in tags:
                    yield child
                yield from child.nodes(tags)

    def text(self):
        if self.tag in ("script", "style"):
            return ""
        return " ".join("".join(child.text() if isinstance(child, _Node) else child
                                for child in self.children).split())


class _Page(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root, self.count = _Node("root"), 0
        self.stack = [self.root]
        if len(html.encode("utf-8")) > 1024 * 1024:
            raise HierarchyFormError("Jira 변환 화면이 허용 크기를 넘었습니다.", status=413)
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if self.count > 20000 or len(self.stack) > 96:
            raise HierarchyFormError("Jira 변환 화면의 구조가 너무 복잡합니다.", status=413)
        node = _Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


@dataclass
class PreparedForm:
    ready: bool
    stage: str
    inputs: list = field(default_factory=list)
    removed_fields: list = field(default_factory=list)
    changes: list = field(default_factory=list)
    _action: str = field(default="", repr=False)
    _pairs: list = field(default_factory=list, repr=False)
    _committed: bool = field(default=False, repr=False)

    def describe(self):
        result = {"ready": self.ready, "stage": self.stage, "inputs": self.inputs,
                  "removedFields": self.removed_fields, "changes": self.changes}
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 6 * 1024:
            raise HierarchyFormError("변환 선택 목록이 너무 큽니다. Jira 화면에서 확인하세요.", status=413)
        return result


class HierarchyForms:
    def __init__(self, provider, base_url):
        self.provider = provider
        self.base_url = base_url.rstrip("/") + "/"

    def _path(self, target, current, allowed):
        absolute = urlsplit(urljoin(urljoin(self.base_url, current.lstrip("/")), target))
        base = urlsplit(self.base_url)
        prefix = base.path.rstrip("/") + "/secure/"
        if (absolute.scheme, absolute.netloc) != (base.scheme, base.netloc) or absolute.fragment:
            raise HierarchyFormError("Jira 변환 화면이 예상하지 않은 주소를 반환했습니다.")
        if not absolute.path.startswith(prefix) or absolute.path[len(prefix):] not in allowed:
            raise HierarchyFormError("지원하지 않는 Jira 변환 단계입니다.")
        path = absolute.path[len(base.path.rstrip("/")):]
        return path + ("?" + absolute.query if absolute.query else "")

    @staticmethod
    def _issue_id(path, issue_id):
        ids = parse_qs(urlsplit(path).query).get("id", [])
        if ids and ids != [issue_id]:
            raise HierarchyFormError("변환 화면의 티켓 식별자가 요청과 다릅니다.")

    def _page(self, response, path, issue_id, redirect_alias=None):
        if response["status"] in (301, 302, 303):
            if not redirect_alias or not response.get("location"):
                raise HierarchyFormError("Jira 변환 화면이 예상하지 않은 이동을 요청했습니다.")
            path = self._path(response["location"], path, {redirect_alias})
            self._issue_id(path, issue_id)
            response = self.provider.web_form(path)
        if response["status"] != 200:
            raise HierarchyFormError("Jira 변환 화면을 열지 못했습니다.", status=response["status"])
        page = _Page(response.get("html") or "")
        errors = [node.text() for node in page.root.nodes() if
                  {"error", "errorMessage", "aui-message-error"} & set(node.attrs.get("class", "").split())]
        errors = list(dict.fromkeys(text for text in errors if text))
        if errors:
            raise HierarchyFormError("Jira 변환 검증: " + "; ".join(errors)[:500], code="jira_rejected")
        return page, path

    def _form(self, page, current, allowed, issue_id, conversion):
        forms = []
        for node in page.root.nodes({"form"}):
            try:
                path = self._path(node.attrs.get("action", ""), current, allowed)
            except HierarchyFormError:
                continue
            forms.append((node, path))
        if len(forms) != 1:
            raise HierarchyFormError("Jira 변환 폼을 확인하지 못했습니다. 권한·workflow 또는 화면 변경을 확인하세요.")
        form, path = forms[0]
        self._issue_id(path, issue_id)
        if form.attrs.get("method", "get").lower() != "post":
            raise HierarchyFormError("예상하지 않은 Jira 변환 폼 전송 방식입니다.")
        controls = [n for n in form.nodes({"input", "select", "textarea"})
                    if n.attrs.get("name") and "disabled" not in n.attrs]
        if len(controls) > 100:
            raise HierarchyFormError("변환 입력 항목이 너무 많습니다.", status=413)
        hidden = [(n.attrs["name"], n.attrs.get("value", "")) for n in controls
                  if n.tag == "input" and n.attrs.get("type", "text").lower() == "hidden"]
        if any(name not in {"id", "guid", "atl_token", "returnUrl"} for name, _value in hidden):
            raise HierarchyFormError("지원하지 않는 숨겨진 Jira 변환 입력이 있습니다. Jira에서 확인하세요.")
        for name in ("id", "atl_token", *(('guid',) if conversion else ())):
            values = [value for key, value in hidden if key == name]
            if len(values) != 1 or not values[0] or (name == "id" and values[0] != issue_id):
                raise HierarchyFormError("Jira 변환 폼의 보호 필드를 확인하지 못했습니다.")
        groups = {}
        labels = {n.attrs.get("for"): n.text() for n in form.nodes({"label"})}
        for node in controls:
            kind = node.attrs.get("type", "text").lower() if node.tag == "input" else node.tag
            if kind in ("hidden", "submit", "reset", "button"):
                continue
            if kind not in ("text", "number", "date", "textarea", "select", "radio", "checkbox"):
                raise HierarchyFormError("지원하지 않는 Jira 변환 입력입니다: " + node.attrs["name"][:100])
            name = node.attrs["name"]
            if name in {key for key, _value in hidden}:
                raise HierarchyFormError("중복된 Jira 변환 보호 필드입니다.")
            group = groups.setdefault(name, {"id": name, "label": labels.get(node.attrs.get("id")) or name,
                "type": kind, "required": False, "options": [], "_nodes": []})
            if group["type"] != kind or group["_nodes"] and kind not in ("radio", "checkbox"):
                raise HierarchyFormError("중복되거나 혼합된 Jira 변환 입력입니다: " + name[:100])
            group["_nodes"].append(node)
            group["required"] |= "required" in node.attrs or node.attrs.get("aria-required") == "true"
            if kind == "select":
                group["multiple"] = "multiple" in node.attrs
                group["options"] = [{"id": opt.attrs.get("value", opt.text()), "label": opt.text()}
                                    for opt in node.nodes({"option"}) if "disabled" not in opt.attrs]
            elif kind in ("radio", "checkbox"):
                group["options"].append({"id": node.attrs.get("value", "on"),
                    "label": labels.get(node.attrs.get("id")) or node.attrs.get("value", "on")})
        return path, hidden, groups

    @staticmethod
    def _fill(groups, supplied):
        missing, pairs = [], []
        for name, group in groups.items():
            public = {key: value for key, value in group.items() if not key.startswith("_")}
            if name not in supplied:
                missing.append(public)
                continue
            value = supplied[name]
            values = value if isinstance(value, list) else [value]
            if not all(isinstance(item, str) for item in values):
                raise HierarchyFormError("변환 필드 값은 문자열 또는 문자열 배열이어야 합니다.", status=400)
            multiple = group.get("multiple") or group["type"] == "checkbox"
            if not multiple and len(values) != 1:
                raise HierarchyFormError("단일 값이 필요한 변환 필드입니다: " + name[:100], status=400)
            if group["required"] and not any(values):
                raise HierarchyFormError("필수 변환 필드입니다: " + name[:100], status=400)
            if group["type"] in ("select", "radio", "checkbox"):
                allowed = {option["id"] for option in group["options"]}
                if any(value not in allowed for value in values):
                    raise HierarchyFormError("Jira에서 제공하지 않은 변환 선택지입니다: " + name[:100], status=400)
            pairs.extend((name, value) for value in values)
        if missing:
            return missing, []
        if set(supplied) - set(groups):
            raise HierarchyFormError("현재 변환 화면에 없는 필드를 입력했습니다.", status=400)
        return [], pairs

    @staticmethod
    def _changes(page):
        changes, removed = [], []
        for row in page.root.nodes({"tr"}):
            cells = [child for child in row.children if isinstance(child, _Node) and child.tag == "td"]
            if len(cells) != 3 or not any("status-inactive" in n.attrs.get("class", "").split()
                                          for n in cells[1].nodes()):
                continue
            name, before, after = (cell.text() for cell in cells)
            if not name or len(name) > 200 or max(len(before), len(after)) > 2000:
                raise HierarchyFormError("변환 영향 내용이 너무 큽니다. Jira에서 확인하세요.", status=413)
            changes.append({"field": name, "before": before, "after": after})
            if not after and not any("status-active" in n.attrs.get("class", "").split()
                                     for n in cells[2].nodes()):
                removed.append(name)
        return changes, removed

    def prepare(self, action, issue_id, *, parent_key="", issue_type_id="", target_status_id="", field_values=None):
        """Advance session-only stages; missing selections stop without committing a ticket."""
        issue_id = str(issue_id)
        if not re.fullmatch(r"[0-9]+", issue_id):
            raise HierarchyFormError("Jira의 숫자 issue ID가 필요합니다.", status=400)
        if action not in {"convert_to_subtask", "convert_to_task", "change_parent"}:
            raise HierarchyFormError("지원하지 않는 계층 변경입니다.", status=400)
        parent = action == "change_parent"
        if parent and (issue_type_id or target_status_id or field_values) or action == "convert_to_task" and parent_key:
            raise HierarchyFormError("이 계층 변경에서 사용하지 않는 입력이 있습니다.", status=400)
        prefix = "ConvertIssue" if action == "convert_to_subtask" else "ConvertSubTask"
        initial = "MoveSubTaskChooseOperation!default.jspa" if parent else prefix + ".jspa"
        path = "/secure/" + initial + "?" + urlencode({"id": issue_id})
        response, seen, wizard_guid = self.provider.web_form(path), set(), None
        aliases = ({"MoveSubTaskChooseOperation.jspa": "operation", "MoveSubTaskParent.jspa": "parent"} if parent else
                   {prefix + suffix + ".jspa": stage for suffix, stage in (
                       ("SetIssueType", "type"), ("SetStatus", "status"), ("UpdateFields", "fields"), ("Convert", "confirm"))})
        for _ in range(5):
            page, path = self._page(response, path, issue_id, "MoveSubTaskParent!default.jspa" if parent else None)
            target, hidden, groups = self._form(page, path, set(aliases), issue_id, not parent)
            if not parent:
                current_guid = dict(hidden)["guid"]
                if wizard_guid is not None and current_guid != wizard_guid:
                    raise HierarchyFormError("진행 중인 Jira 변환 세션이 바뀌었습니다. 처음부터 다시 확인하세요.")
                wizard_guid = current_guid
            alias = urlsplit(target).path.rsplit("/", 1)[-1]
            stage = aliases[alias]
            if stage in seen:
                raise HierarchyFormError("Jira 변환 단계가 진행되지 않았습니다.", code="jira_rejected")
            expected = ({"operation"} if not seen else {"parent"}) if parent else (
                {"type"} if not seen else {"status", "fields"} if seen == {"type"}
                else {"fields"} if seen == {"type", "status"} else {"confirm"})
            if stage not in expected:
                raise HierarchyFormError("Jira 변환 단계 순서가 예상과 다릅니다.")
            seen.add(stage)
            if stage == "operation":
                supplied = {"operation": "move.subtask.parent.operation.name"}
            elif stage == "type":
                supplied = {"issuetype": issue_type_id} if issue_type_id else {}
                if action == "convert_to_subtask" and parent_key:
                    supplied["parentIssueKey"] = parent_key
            elif stage == "status":
                supplied = {"targetStatusId": target_status_id} if target_status_id else {}
            elif stage == "parent":
                supplied = {"parentIssue": parent_key} if parent_key else {}
            else:
                supplied = (field_values or {}) if stage == "fields" else {}
            missing, pairs = self._fill(groups, supplied)
            if missing:
                result = PreparedForm(False, stage, inputs=missing)
                result.describe()
                return result
            if stage in ("confirm", "parent"):
                if stage == "confirm" and "fields" not in seen:
                    raise HierarchyFormError("Jira 변환 필드 검증 단계가 누락됐습니다.")
                if parent and field_values or not parent and target_status_id and "status" not in seen:
                    raise HierarchyFormError("현재 변환 화면에서 사용하지 않는 입력이 있습니다.", status=400)
                changes, removed = self._changes(page)
                result = PreparedForm(True, stage, removed_fields=removed, changes=changes,
                                      _action=target, _pairs=hidden + pairs)
                result.describe()
                return result
            path = target
            response = self.provider.web_form(path, hidden + pairs, may_commit=False)
        raise HierarchyFormError("Jira 변환 단계 수를 초과했습니다.")

    def commit(self, prepared, *, allow_removed_fields=()):
        """Exactly one final POST. The caller must verify the resulting Jira hierarchy by REST."""
        if not prepared.ready or prepared._committed:
            raise HierarchyFormError("변환 준비가 필요하거나 이미 제출한 요청입니다.")
        if set(prepared.removed_fields) != set(allow_removed_fields):
            raise HierarchyFormError("변환으로 제거될 필드를 먼저 확인해야 합니다.", code="hierarchy_field_removal")
        prepared._committed = True
        return self.provider.web_form(prepared._action, prepared._pairs, may_commit=True)

"""Native wizard contracts derived from Jira 8.20.8 JSP/action names, without live Jira.

These are minimal independently authored rendered forms, not copied Atlassian templates.
Application-level state/permission and REST reconciliation tests live with the command caller.
"""

import pytest

from app.jira.hierarchy_forms import HierarchyFormError, HierarchyForms


def form(action, controls="", *, conversion=True, issue_id="100", token="secret-xsrf", extra=""):
    protected = f'<input type="hidden" name="id" value="{issue_id}">'
    protected += f'<input type="hidden" name="atl_token" value="{token}">'
    if conversion:
        protected += '<input type="hidden" name="guid" value="secret-guid">'
    return f'<form action="{action}" method="post">{protected}{controls}{extra}</form>'


def type_form(prefix="ConvertIssue", parent=True):
    controls = '<select name="issuetype" required><option value="5">Sub-task</option></select>'
    if parent:
        controls += '<input name="parentIssueKey" required>'
    return form(prefix + "SetIssueType.jspa", controls)


def status_form(prefix="ConvertIssue"):
    return form(prefix + "SetStatus.jspa", '<select name="targetStatusId" required>'
                '<option value="1" selected>Open</option><option value="3">Progress</option></select>')


def fields_form(controls="", prefix="ConvertIssue"):
    return form(prefix + "UpdateFields.jspa", controls)


def confirm_form(extra="", prefix="ConvertIssue"):
    return form(prefix + "Convert.jspa", extra=extra)


class Provider:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def web_form(self, path, fields=None, *, may_commit=False):
        self.calls.append((path, fields, may_commit))
        value = self.responses.pop(0)
        return value if isinstance(value, dict) else {"status": 200, "html": value, "location": ""}


@pytest.mark.parametrize("action,prefix,parent_key", [
    ("convert_to_subtask", "ConvertIssue", "DL-2"),
    ("convert_to_task", "ConvertSubTask", ""),
])
def test_conversion_prepares_all_validation_steps_then_commits_once(action, prefix, parent_key):
    provider = Provider(type_form(prefix, bool(parent_key)), fields_form(prefix=prefix),
                        confirm_form(prefix=prefix), {"status": 302, "html": "", "location": "/browse/DL-1"})
    engine = HierarchyForms(provider, "https://jira.example/jira")
    prepared = engine.prepare(action, "100", parent_key=parent_key, issue_type_id="5")
    assert prepared.ready and prepared.stage == "confirm"
    assert [row[0] for row in provider.calls] == [f"/secure/{prefix}.jspa?id=100",
        f"/secure/{prefix}SetIssueType.jspa", f"/secure/{prefix}UpdateFields.jspa"]
    assert not any(row[2] for row in provider.calls)
    assert "secret" not in str(prepared.describe()) and "secret" not in repr(prepared)
    result = engine.commit(prepared)
    assert result["status"] == 302
    assert provider.calls[-1][0] == f"/secure/{prefix}Convert.jspa"
    assert provider.calls[-1][2] is True
    with pytest.raises(HierarchyFormError):
        engine.commit(prepared)
    assert len(provider.calls) == 4


def test_missing_type_or_status_returns_exact_choices_without_accepting_defaults():
    provider = Provider(type_form())
    prepared = HierarchyForms(provider, "https://jira.example").prepare("convert_to_subtask", "100", parent_key="DL-2")
    assert not prepared.ready and prepared.inputs[0]["id"] == "issuetype"
    assert prepared.inputs[0]["options"] == [{"id": "5", "label": "Sub-task"}]
    assert len(provider.calls) == 1
    provider = Provider(type_form(), status_form())
    prepared = HierarchyForms(provider, "https://jira.example").prepare("convert_to_subtask", "100",
                                                                 parent_key="DL-2", issue_type_id="5")
    assert not prepared.ready and prepared.stage == "status"
    assert prepared.inputs[0]["id"] == "targetStatusId"
    assert [option["id"] for option in prepared.inputs[0]["options"]] == ["1", "3"]
    assert not any(row[2] for row in provider.calls)


def test_explicit_custom_fields_preserve_multiple_values_and_report_removals():
    controls = ('<label for="choice">Required choice</label><select id="choice" name="customfield_1" multiple required>'
                '<option value="a">Alpha</option><option value="b">Beta</option></select>'
                '<textarea name="customfield_2"></textarea>')
    changes = ('<table><tr><td>Old field</td><td><span class="status-inactive">old value</span></td><td>&nbsp;</td></tr>'
               '<tr><td>Kept field</td><td><span class="status-inactive">before</span></td>'
               '<td><span class="status-active">after</span></td></tr></table>')
    provider = Provider(type_form(), status_form(), fields_form(controls), confirm_form(changes),
                        {"status": 302, "html": "", "location": "/browse/DL-1"})
    engine = HierarchyForms(provider, "https://jira.example")
    prepared = engine.prepare("convert_to_subtask", "100", parent_key="DL-2", issue_type_id="5",
                              target_status_id="3", field_values={"customfield_1": ["a", "b"], "customfield_2": "hello"})
    assert prepared.ready and prepared.removed_fields == ["Old field"]
    assert prepared.changes[1] == {"field": "Kept field", "before": "before", "after": "after"}
    assert [(key, value) for key, value in provider.calls[-1][1] if key == "customfield_1"] == [
        ("customfield_1", "a"), ("customfield_1", "b")]
    with pytest.raises(HierarchyFormError, match="제거"):
        engine.commit(prepared)
    assert not any(row[2] for row in provider.calls)
    engine.commit(prepared, allow_removed_fields=["Old field"])
    assert provider.calls[-1][2] is True


def test_reparent_initializes_move_bean_then_follows_only_expected_redirect():
    choose = form("MoveSubTaskChooseOperation.jspa?id=100",
                  '<input type="radio" name="operation" value="move.subtask.parent.operation.name">', conversion=False)
    parent = form("MoveSubTaskParent.jspa?id=100", '<input name="parentIssue" required>', conversion=False)
    provider = Provider(choose, {"status": 303, "html": "", "location": "MoveSubTaskParent!default.jspa?id=100"},
                        parent, {"status": 302, "html": "", "location": "/jira/browse/DL-1"})
    engine = HierarchyForms(provider, "https://jira.example/jira")
    prepared = engine.prepare("change_parent", "100", parent_key="DL-2")
    assert prepared.ready and prepared.stage == "parent"
    assert provider.calls[0][0] == "/secure/MoveSubTaskChooseOperation!default.jspa?id=100"
    assert provider.calls[1][1][-1] == ("operation", "move.subtask.parent.operation.name")
    assert provider.calls[2][0] == "/secure/MoveSubTaskParent!default.jspa?id=100"
    engine.commit(prepared)
    assert provider.calls[-1][1][-1] == ("parentIssue", "DL-2")
    assert sum(row[2] for row in provider.calls) == 1


@pytest.mark.parametrize("html", [
    type_form().replace('value="100"', 'value="999"'),
    type_form().replace('value="secret-xsrf"', 'value=""'),
    type_form().replace('action="ConvertIssueSetIssueType.jspa"', 'action="https://attacker.example/secure/ConvertIssueSetIssueType.jspa"'),
    type_form().replace('method="post"', 'method="get"'),
    type_form().replace('<input name="parentIssueKey" required>', '<input type="file" name="file">'),
    type_form().replace('</form>', '<input type="hidden" name="customfield_9" value="unrequested"></form>'),
    type_form() + '<div class="aui-message-error">Permission denied</div>',
    fields_form(),
    confirm_form(),
])
def test_unknown_or_wrong_ticket_forms_never_commit(html):
    provider = Provider(html)
    with pytest.raises(HierarchyFormError):
        HierarchyForms(provider, "https://jira.example").prepare("convert_to_subtask", "100", parent_key="DL-2", issue_type_id="5")
    assert len(provider.calls) == 1 and not provider.calls[0][2]


@pytest.mark.parametrize("values", [{"issue_type_id": "unknown"}, {"issue_type_id": ["5", "6"]}])
def test_unknown_type_selection_does_not_post(values):
    provider = Provider(type_form())
    with pytest.raises(HierarchyFormError):
        HierarchyForms(provider, "https://jira.example").prepare("convert_to_subtask", "100", parent_key="DL-2", **values)
    assert len(provider.calls) == 1


def test_repeated_step_stops_and_unexpected_custom_fields_are_not_silently_dropped():
    provider = Provider(type_form(), type_form())
    with pytest.raises(HierarchyFormError, match="진행되지"):
        HierarchyForms(provider, "https://jira.example").prepare("convert_to_subtask", "100", parent_key="DL-2", issue_type_id="5")
    assert len(provider.calls) == 2 and not any(row[2] for row in provider.calls)
    provider = Provider(type_form(), fields_form())
    with pytest.raises(HierarchyFormError, match="없는 필드"):
        HierarchyForms(provider, "https://jira.example").prepare("convert_to_subtask", "100", parent_key="DL-2",
              issue_type_id="5", field_values={"invented_field": "not dropped"})
    assert len(provider.calls) == 2


def test_changed_wizard_identity_or_cross_origin_redirect_is_not_followed():
    provider = Provider(type_form(), fields_form().replace("secret-guid", "another-guid"))
    with pytest.raises(HierarchyFormError, match="세션이 바뀌었습니다"):
        HierarchyForms(provider, "https://jira.example").prepare("convert_to_subtask", "100", parent_key="DL-2", issue_type_id="5")
    assert len(provider.calls) == 2 and not any(row[2] for row in provider.calls)
    choose = form("MoveSubTaskChooseOperation.jspa?id=100",
                  '<input type="radio" name="operation" value="move.subtask.parent.operation.name">', conversion=False)
    provider = Provider(choose, {"status": 303, "html": "", "location": "https://other.example/secure/MoveSubTaskParent!default.jspa?id=100"})
    with pytest.raises(HierarchyFormError):
        HierarchyForms(provider, "https://jira.example").prepare("change_parent", "100", parent_key="DL-2")
    assert len(provider.calls) == 2 and not any(row[2] for row in provider.calls)


def test_input_discovery_size_limit_does_not_truncate_choice_ids():
    options = ''.join(f'<option value="{i}">{"x" * 100}</option>' for i in range(100))
    provider = Provider(form("ConvertIssueSetIssueType.jspa", '<select name="issuetype">' + options + '</select>'))
    with pytest.raises(HierarchyFormError) as error:
        HierarchyForms(provider, "https://jira.example").prepare("convert_to_subtask", "100")
    assert error.value.status == 413 and len(provider.calls) == 1
@pytest.mark.parametrize("values,required,kind", [(["a", "b"], False, "text"), ("", True, "text"), ("bad", False, "select")])
def test_oversized_field_names_cannot_escape_error_context_budget(values, required, kind):
    name = "customfield_" + "x" * 60000
    group = {"id": name, "label": "field", "type": kind, "required": required, "options": []}
    with pytest.raises(HierarchyFormError) as caught:
        HierarchyForms._fill({name: group}, {name: values})
    assert len(str(caught.value).encode("utf-8")) < 512


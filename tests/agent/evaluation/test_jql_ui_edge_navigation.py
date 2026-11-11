"""Composable UI scenarios keep navigation out of generic JQL measurements."""
from types import SimpleNamespace

import pytest

from tools import jql_ui_benchmark as harness
from tools import jql_ui_edge_scenarios as edge


class Page:
    def __init__(self, view="task", fail_task_entry=False):
        self.view = view
        self.fail_task_entry = fail_task_entry
        self.requests = []
        self.submitted = []

    def goto(self, url):
        self.enter(url.rsplit("/", 1)[-1])

    def enter(self, view):
        if view == "mytasks" and self.fail_task_entry:
            raise TimeoutError("Task entry did not settle")
        self.view = "task" if view == "mytasks" else view
        self.requests.append("/api/" + view)

    def locator(self, selector):
        if selector.startswith('a[href="#/'):
            view = selector.split('#/', 1)[1].split('"', 1)[0]
            return SimpleNamespace(click=lambda: self.enter(view))
        assert selector == ".mt-qf-jql"

        def fill(value):
            if self.view != "task":
                raise TimeoutError("JQL input is unavailable outside Task")
            self.value = value

        def press(key):
            assert key == "Enter" and self.view == "task"
            self.submitted.append(self.value)
            self.requests.append("/api/mytasks/jql")

        return SimpleNamespace(is_visible=lambda: self.view == "task", fill=fill, press=press)


def scenario_runtime(monkeypatch, page):
    def measure(current, name, action, rows):
        start = len(current.requests)
        row = {"name": name}
        try:
            action()
            row.update(ok=True, state={"keys": ["DL-1", "DL-2"]})
        except BaseException:
            row["ok"] = False
            raise
        finally:
            row.update(requests=current.requests[start:], searchRequests=0,
                       settlement={"complete": row["ok"]})
            rows.append(row)

    monkeypatch.setattr(harness, "measure", measure)
    monkeypatch.setattr(edge, "measure", measure)
    monkeypatch.setattr(harness, "new_page", lambda *_: page)
    monkeypatch.setattr(harness, "wait_idle", lambda: None)
    monkeypatch.setattr(harness, "request", lambda *_args, **_kwargs: {})


@pytest.mark.parametrize("reverse", [False, True], ids=["already-task", "reverse-then-generic"])
def test_generic_query_enters_task_only_when_needed_and_measures_entry_separately(monkeypatch, reverse):
    page, rows = Page(), []
    scenario_runtime(monkeypatch, page)
    if reverse:
        browser = SimpleNamespace(contexts=[])
        page = harness.reverse_flow(browser, None, rows, [])
        assert page.view == "workload"
    boundary = len(rows)

    edge.generic_query(page, rows)

    added = rows[boundary:]
    expected = (["generic-task-entry"] if reverse else []) + ["generic-jql-cold", "generic-jql-equivalent"]
    assert [row["name"] for row in added] == expected
    assert len(page.submitted) == 2 and page.submitted[0] != page.submitted[1]
    assert all(row["ok"] and row["settlement"]["complete"] for row in added)
    if reverse:
        assert added[0]["requests"] == ["/api/mytasks"]
    assert added[-2]["requests"] == added[-1]["requests"] == ["/api/mytasks/jql"]


def test_failed_task_entry_is_preserved_without_attempting_generic_query(monkeypatch):
    page, rows = Page(view="workload", fail_task_entry=True), []
    scenario_runtime(monkeypatch, page)
    with pytest.raises(TimeoutError, match="Task entry"):
        edge.generic_query(page, rows)
    assert [row["name"] for row in rows] == ["generic-task-entry"]
    assert rows[0]["ok"] is False and rows[0]["settlement"]["complete"] is False
    assert page.submitted == []

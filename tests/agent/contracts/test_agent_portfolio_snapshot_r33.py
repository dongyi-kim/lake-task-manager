"""Portfolio deterministic material crosses the graph as typed, machine-owned data."""

from __future__ import annotations

import inspect

from langchain_core.messages import HumanMessage


def _message(text: str, **state):
    return {"messages": [HumanMessage(content=text)], **state}


def test_group_material_projects_original_tool_rows_without_reparsing(monkeypatch):
    from app.agent import tools as tool_registry
    from app.agent.workflow.agents import portfolio_analyst as module

    class Tool:
        def __init__(self, result):
            self.result = result

        def invoke(self, args):
            return self.result(args) if callable(self.result) else self.result

    monkeypatch.setitem(
        tool_registry.BY_NAME, "get_module_people",
        Tool({"people": ["skcc.a100", "skcc.b200"]}),
    )
    monkeypatch.setitem(
        tool_registry.BY_NAME, "get_user_activity",
        Tool(lambda args: {
            "touched": [{
                "key": "DL-100" if args["user_id"] == "skcc.a100" else "DL-200",
                "summary": "수집" if args["user_id"] == "skcc.a100" else "검증",
                "status": "In Progress",
            }],
            "jiraActivity": [{"key": "DL-300", "what": f"{args['user_id']} 코멘트"}],
            "docActivity": [{"title": f"{args['user_id']} 문서", "url": "https://example.test/doc"}],
        }),
    )

    material = module._group_activity_material(_message(
        "최근 7일간 ETL 모듈 구성원들의 주요 활동 내역", intent="activity", module="ETL",
    ))

    assert material["complete"] is True
    snapshot = material["snapshot"]
    assert snapshot["kind"] == "group_activity"
    assert snapshot["roster"] == ["skcc.a100", "skcc.b200"]
    assert [row["user_id"] for row in snapshot["activities"]] == snapshot["roster"]
    assert snapshot["activities"][0]["data"]["touched"][0] == {
        "key": "DL-100", "summary": "수집", "status": "In Progress",
    }
    assert "DL-100" in material["text"] and "DL-200" in material["text"]
    # Acquisition may inspect dict/list fields, but must never parse its rendered text.
    source = inspect.getsource(module._group_activity_material)
    assert "splitlines(" not in source and "finditer(" not in source


def test_progress_material_keeps_raw_ticket_children_and_evidence(monkeypatch):
    from app.agent.workflow.agents import portfolio_analyst as module
    from app.agent.tools import survey_tools

    report = {
        "key": "DL-10", "title": "진척 대상", "status": "In Progress", "done": False,
        "assigneeId": "skcc.a100", "due": "2026-08-25", "updated": "2026-08-18",
        "children_done": "1/2",
        "children": [
            {"key": "DL-11", "title": "완료", "status": "Closed", "done": True,
             "assigneeId": "skcc.a100", "updated": "2026-08-17"},
            {"key": "DL-12", "title": "진행", "status": "In Progress", "done": False,
             "assigneeId": "skcc.b200", "updated": "2026-08-18"},
        ],
        "changes": [{"date": "2026-08-01", "field": "status", "from": "Open",
                     "to": "In Progress", "who": "skcc.a100"}],
        "comments": [{"date": "2026-08-18", "who": "skcc.b200", "text": "남은 작업"}],
        "links": [{"key": "DL-20", "title": "선행", "status": "Closed", "done": True,
                   "rel": "blocks", "updated": "2026-08-16"}],
        "documents": [{"title": "결과 문서", "url": "https://example.test/result",
                       "updated": "2026-08-18", "excerpt": "남은 작업 기록"}],
    }
    monkeypatch.setattr(survey_tools, "progress_report", lambda _key: report)

    material = module._ticket_progress_material(_message(
        "DL-10 지금 어디까지 됐어?", intent="progress", mentioned_keys=["DL-10"],
    ))

    assert material["complete"] is True
    ticket = material["snapshot"]["tickets"][0]
    assert ticket["key"] == "DL-10"
    assert [row["key"] for row in ticket["children"]] == ["DL-11", "DL-12"]
    assert ticket["childrenAggregate"] == {
        "total": 2, "done": 1, "returned": 2, "remainingCount": 0,
    }
    assert ticket["comments"] == report["comments"]
    assert ticket["documents"][0]["url"] == "https://example.test/result"
    assert "DL-12" in material["text"] and "남은 작업" in material["text"]
    source = inspect.getsource(module._ticket_progress_material)
    assert "splitlines(" not in source and "finditer(" not in source


def test_complete_gate_requires_every_activity_and_epic_tool_call(monkeypatch):
    from app.agent import tools as tool_registry
    from app.agent.workflow.agents import portfolio_analyst as module
    from app.agent.tools import survey_tools

    class Tool:
        def __init__(self, call):
            self.call = call

        def invoke(self, args):
            return self.call(args)

    monkeypatch.setitem(tool_registry.BY_NAME, "get_module_people",
                        Tool(lambda _args: {"people": ["skcc.a100", "skcc.b200"]}))

    def activity(args):
        if args["user_id"] == "skcc.b200":
            raise RuntimeError("one roster lookup failed")
        return {"touched": [], "jiraActivity": [], "docActivity": []}

    monkeypatch.setitem(tool_registry.BY_NAME, "get_user_activity", Tool(activity))
    group = module._group_activity_material(_message(
        "최근 7일간 ETL 모듈 구성원 활동", intent="activity", module="ETL",
    ))
    assert group["complete"] is False and group["text"] == ""
    assert [row["availability"] for row in group["snapshot"]["activities"]] == [
        "available", "unavailable",
    ]

    monkeypatch.setattr(survey_tools, "progress_report", lambda _key: {
        "key": "DL-10", "title": "진척", "status": "Open", "children": [],
        "changes": [], "comments": [], "links": [], "documents": [],
    })
    monkeypatch.setitem(tool_registry.BY_NAME, "get_epic_tree",
                        Tool(lambda _args: (_ for _ in ()).throw(RuntimeError("tree failed"))))
    progress = module._ticket_progress_material(_message(
        "DL-10 진척 어때?", intent="progress", mentioned_keys=["DL-10"],
    ))
    assert progress["complete"] is False
    assert progress["snapshot"]["tickets"][0]["epic_tree"] == {
        "availability": "unavailable",
    }
    monkeypatch.setitem(tool_registry.BY_NAME, "get_epic_tree",
                        Tool(lambda _args: {"error": "provider denied"}))
    explicit_error = module._ticket_progress_material(_message(
        "DL-10 진척 어때?", intent="progress", mentioned_keys=["DL-10"],
    ))
    assert explicit_error["complete"] is False
    assert explicit_error["snapshot"]["tickets"][0]["epic_tree"]["availability"] == "unavailable"


def test_complete_gate_rejects_success_shaped_but_incomplete_payloads(monkeypatch):
    from app.agent import tools as tool_registry
    from app.agent.workflow.agents import portfolio_analyst as module
    from app.agent.tools import survey_tools

    class Tool:
        def __init__(self, result):
            self.result = result

        def invoke(self, _args):
            return self.result

    monkeypatch.setitem(tool_registry.BY_NAME, "get_module_people",
                        Tool({"people": ["user.a", "user.b"]}))
    monkeypatch.setitem(tool_registry.BY_NAME, "get_team_workload", Tool({"people": []}))
    monkeypatch.setitem(tool_registry.BY_NAME, "get_user_activity", Tool({
        "touched": [], "jiraActivity": [], "docActivity": [],
    }))
    group = module._group_activity_material(_message(
        "ETL 팀 부하를 알려줘", intent="activity", module="ETL",
    ))
    assert group["complete"] is False
    assert group["snapshot"]["workload"]["availability"] == "unavailable"

    monkeypatch.setattr(survey_tools, "progress_report", lambda _key: {
        "key": "ABC-1", "title": "제목", "status": "Open",
        # Success-shaped response, but the typed evidence lists are absent.
    })
    incomplete = module._ticket_progress_material(_message(
        "ABC-1 진행 상황", intent="progress", mentioned_keys=["ABC-1"],
    ))
    assert incomplete["complete"] is False and incomplete["text"] == ""
    assert incomplete["snapshot"]["tickets"] == [
        {"key": "ABC-1", "availability": "unavailable"},
    ]

    monkeypatch.setattr(survey_tools, "progress_report", lambda _key: ["malformed"])
    malformed = module._ticket_progress_material(_message(
        "ABC-1 진행 상황", intent="progress", mentioned_keys=["ABC-1"],
    ))
    assert malformed["complete"] is False
    assert malformed["snapshot"]["tickets"] == [
        {"key": "ABC-1", "availability": "unavailable"},
    ]


def test_progress_cardinality_and_epic_coverage_are_explicit(monkeypatch):
    from app.agent import tools as tool_registry
    from app.agent.tools import survey_tools
    from app.agent.workflow.agents import portfolio_analyst as module

    class Tool:
        def invoke(self, _args):
            children = [{"key": f"ABC-{n}", "done": n <= 31, "status": "Done"}
                        for n in range(10, 45)]
            return {"children": children}

    report = {"title": "진척", "status": "Open", "children": [], "changes": [],
              "comments": [], "links": [], "documents": []}
    monkeypatch.setattr(survey_tools, "progress_report",
                        lambda key: {**report, "key": key})
    monkeypatch.setitem(tool_registry.BY_NAME, "get_epic_tree", Tool())
    material = module._ticket_progress_material(_message(
        "ABC-1 ABC-2 ABC-3 ABC-4 ABC-5 진행 상황", intent="progress",
        mentioned_keys=["ABC-1", "ABC-2", "ABC-3", "ABC-4", "ABC-5"],
    ))

    assert material["complete"] is False
    assert material["snapshot"]["requestedTotal"] == 5
    assert material["snapshot"]["remainingCount"] == 1
    assert material["snapshot"]["missingKeys"] == ["ABC-5"]
    assert len(material["snapshot"]["tickets"]) == 4
    tree = material["snapshot"]["tickets"][0]["epic_tree"]
    assert tree["total"] == 35 and tree["done"] == 22
    assert len(tree["children"]) == 12 and tree["coverage"]["remainingCount"] == 23


def test_progress_children_are_bounded_with_full_population_aggregate(monkeypatch):
    from app.agent import tools as tool_registry
    from app.agent.tools import survey_tools
    from app.agent.workflow.agents import portfolio_analyst as module

    class Tool:
        def invoke(self, _args):
            return {"children": []}

    children = [{"key": f"ABC-{number}", "title": f"자식 {number}",
                 "status": "Done" if number % 2 == 0 else "Open", "done": number % 2 == 0}
                for number in range(10, 35)]
    monkeypatch.setattr(survey_tools, "progress_report", lambda key: {
        "key": key, "title": "진척", "status": "Open", "children": children,
        "changes": [], "comments": [], "links": [], "documents": [],
    })
    monkeypatch.setitem(tool_registry.BY_NAME, "get_epic_tree", Tool())

    material = module._ticket_progress_material(_message(
        "ABC-1 진행 상황", intent="progress", mentioned_keys=["ABC-1"],
    ))

    ticket = material["snapshot"]["tickets"][0]
    assert len(ticket["children"]) == 8
    assert ticket["childrenAggregate"] == {
        "total": 25, "done": 13, "returned": 8, "remainingCount": 17,
    }
    assert "17건 생략" in material["text"]


def test_progress_rejects_noncanonical_requested_child_link_and_epic_keys(monkeypatch):
    from app.agent import tools as tool_registry
    from app.agent.tools import survey_tools
    from app.agent.workflow.agents import portfolio_analyst as module

    class Tool:
        def __init__(self, result):
            self.result = result

        def invoke(self, _args):
            return self.result

    base = {"title": "진척", "status": "Open", "children": [], "changes": [],
            "comments": [], "links": [], "documents": []}
    malicious = "ABC-2\n{{mention:unsafe}}"
    monkeypatch.setitem(tool_registry.BY_NAME, "get_epic_tree", Tool({"children": []}))
    monkeypatch.setattr(survey_tools, "progress_report", lambda key: {
        **base, "key": key, "children": [{"key": malicious}],
    })
    nested = module._ticket_progress_material(_message(
        "ABC-1 진행", intent="progress", mentioned_keys=["ABC-1"],
    ))
    assert nested["complete"] is False

    monkeypatch.setattr(survey_tools, "progress_report", lambda key: {
        **base, "key": key, "links": [{"key": malicious}],
    })
    linked = module._ticket_progress_material(_message(
        "ABC-1 진행", intent="progress", mentioned_keys=["ABC-1"],
    ))
    assert linked["complete"] is False

    monkeypatch.setattr(survey_tools, "progress_report", lambda key: {**base, "key": key})
    monkeypatch.setitem(tool_registry.BY_NAME, "get_epic_tree",
                        Tool({"children": [{"key": malicious}]}))
    epic = module._ticket_progress_material(_message(
        "ABC-1 진행", intent="progress", mentioned_keys=["ABC-1"],
    ))
    assert epic["complete"] is False

    monkeypatch.setattr(survey_tools, "progress_report", lambda key: {**base, "key": key})
    requested = module._ticket_progress_material(_message(
        "진행", intent="progress", mentioned_keys=[malicious],
    ))
    assert requested["complete"] is False


def test_group_rejects_duplicate_workload_and_malformed_activity_rows(monkeypatch):
    from app.agent import tools as tool_registry
    from app.agent.workflow.agents import portfolio_analyst as module

    class Tool:
        def __init__(self, result):
            self.result = result

        def invoke(self, _args):
            return self.result

    monkeypatch.setitem(tool_registry.BY_NAME, "get_module_people",
                        Tool({"people": ["user.a", "user.b"]}))
    monkeypatch.setitem(tool_registry.BY_NAME, "get_team_workload", Tool({"people": [
        {"id": "user.a", "inProgress": 1, "open": 2, "done28d": 3},
        {"id": "user.a", "inProgress": 4, "open": 5, "done28d": 6},
    ]}))
    monkeypatch.setitem(tool_registry.BY_NAME, "get_user_activity", Tool({
        "touched": [{"key": f"ABC-{n}"} for n in range(6)],
        "jiraActivity": [], "docActivity": [],
    }))
    material = module._group_activity_material(_message(
        "ETL 팀 부하", intent="activity", module="ETL",
    ))
    assert material["complete"] is False
    assert material["snapshot"]["workload"]["availability"] == "unavailable"
    coverage = material["snapshot"]["activities"][0]["data"]["coverage"]["touched"]
    assert coverage == {"total": 6, "returned": 5, "remainingCount": 1}

    monkeypatch.setitem(tool_registry.BY_NAME, "get_user_activity", Tool({
        "touched": [{"key": "ABC-1"}, "malformed"],
        "jiraActivity": [], "docActivity": [],
    }))
    malformed = module._group_activity_material(_message(
        "ETL 팀 활동", intent="activity", module="ETL",
    ))
    assert malformed["complete"] is False and malformed["text"] == ""


def test_complete_progress_skips_portfolio_llm_and_emits_typed_snapshot(monkeypatch):
    from app.agent.workflow.agents import portfolio_analyst as module

    material = {
        "text": '[DL-10] "진척 대상" — 상태 In Progress',
        "snapshot": {"kind": "ticket_progress", "requested_keys": ["DL-10"],
                     "requestedTotal": 1, "remainingCount": 0,
                     "missingKeys": [],
                     "tickets": [{"key": "DL-10", "title": "진척 대상",
                                  "status": "In Progress", "availability": "available",
                                  "epic_tree": {"availability": "not_applicable"},
                                  "children": [], "changes": [], "comments": [],
                                  "links": [], "documents": []}],
                     "complete": True},
        "complete": True,
    }
    monkeypatch.setattr(module, "_group_activity_material", lambda _state: {})
    monkeypatch.setattr(module, "_self_report", lambda _state: "")
    monkeypatch.setattr(module, "_my_day", lambda _state: "")
    monkeypatch.setattr(module, "_module_compare", lambda _state: "")
    monkeypatch.setattr(module, "_ticket_progress_material", lambda _state: material)
    monkeypatch.setattr(module, "_current_person_work", lambda _state: {})

    analyst = module.PortfolioAnalyst()
    monkeypatch.setattr(
        analyst, "_conclude",
        lambda *_args: (_ for _ in ()).throw(AssertionError("Portfolio LLM call is forbidden")),
    )
    monkeypatch.setattr(
        analyst, "invoke_structured",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("token-spending call")),
    )

    out = analyst.node()(_message(
        "DL-10 진척 어때?", intent="progress", mentioned_keys=["DL-10"], evidence=[],
    ))

    assert out["ticket_progress"] == material["text"]
    assert out["portfolio_snapshot"]["version"] == "portfolio.snapshot.v1"
    assert out["portfolio_snapshot"]["materials"] == [material["snapshot"]]
    assert "atomic_facts" not in out["portfolio_snapshot"] and "evidence" not in out
    assert not out.get("situation") and not out.get("pmo_findings")
    fast = [row["fastPath"] for row in out["trace"] if row.get("fastPath")]
    assert fast == [{"contract": "typed-fast-path.v1", "id": "portfolio.intermediate.v1",
                     "complete": True, "authority": "portfolio_analyst.raw_tool_snapshot",
                     "savedCalls": 1, "missing": []}]


def test_partial_progress_and_explicit_jql_keep_existing_conclusion_path(monkeypatch):
    from app.agent.workflow.agents import portfolio_analyst as module

    partial = {
        "text": "[DL-404] 존재하지 않는 티켓이다.",
        "snapshot": {"kind": "ticket_progress", "requested_keys": ["DL-404"],
                     "tickets": [{"key": "DL-404", "exists": False}]},
        "complete": False,
    }
    monkeypatch.setattr(module, "_group_activity_material", lambda _state: {})
    monkeypatch.setattr(module, "_self_report", lambda _state: "")
    monkeypatch.setattr(module, "_my_day", lambda _state: "")
    monkeypatch.setattr(module, "_module_compare", lambda _state: "")
    monkeypatch.setattr(module, "_ticket_progress_material", lambda _state: partial)
    monkeypatch.setattr(module, "_current_person_work", lambda _state: {})

    analyst = module.PortfolioAnalyst()
    calls = []
    monkeypatch.setattr(analyst, "_conclude", lambda _state, _scratch: (
        calls.append("conclude") or {"headline": "미존재", "findings": [], "caution": ""}
    ))

    out = analyst.node()(_message(
        "DL-404 진척 어때?", intent="progress", mentioned_keys=["DL-404"],
    ))
    assert calls == ["conclude"]
    assert out["situation"] == "미존재"
    miss = [row["fastPath"] for row in out["trace"] if row.get("fastPath")]
    assert miss[0]["complete"] is False and miss[0]["savedCalls"] == 0

    # Explicit JQL never takes the deterministic final-only fast path.
    monkeypatch.setattr(module, "_ticket_progress_material", lambda _state: {
        **partial, "complete": True,
    })
    react_calls = []
    monkeypatch.setattr(
        module.ToolAgent, "node", lambda _self: lambda _state: (
            react_calls.append("react") or {"situation": "JQL 실행 결과"}
        ),
    )
    jql_analyst = module.PortfolioAnalyst()
    jql_out = jql_analyst.node()(_message(
        "DL-404 진척 JQL 보여줘", intent="progress", mentioned_keys=["DL-404"],
    ))
    assert react_calls == ["react"]
    assert jql_out["situation"] == "JQL 실행 결과"


def test_bounded_progress_batch_routes_missing_keys_to_retrieval_and_disclosure(monkeypatch):
    from app.agent.workflow.agents import portfolio_analyst as module

    snapshot = {"kind": "ticket_progress", "complete": False,
                "requested_keys": ["ABC-1", "ABC-2", "ABC-3", "ABC-4"],
                "requestedTotal": 5, "remainingCount": 1, "missingKeys": ["ABC-5"],
                "tickets": []}
    material = {"text": "bounded first batch", "snapshot": snapshot, "complete": False}
    monkeypatch.setattr(module, "_group_activity_material", lambda _state: {})
    monkeypatch.setattr(module, "_self_report", lambda _state: "")
    monkeypatch.setattr(module, "_my_day", lambda _state: "")
    monkeypatch.setattr(module, "_module_compare", lambda _state: "")
    monkeypatch.setattr(module, "_ticket_progress_material", lambda _state: material)
    monkeypatch.setattr(module, "_current_person_work", lambda _state: {})
    calls = []
    monkeypatch.setattr(module.ToolAgent, "node", lambda _self: lambda _state: (
        calls.append("react") or {"situation": "retrieved missing key"}))
    analyst = module.PortfolioAnalyst()
    monkeypatch.setattr(analyst, "_conclude", lambda *_args: (
        _ for _ in ()).throw(AssertionError("partial batch must not conclude directly")))
    state = _message("ABC-1 ABC-2 ABC-3 ABC-4 ABC-5 진행", intent="progress",
                     mentioned_keys=["ABC-1", "ABC-2", "ABC-3", "ABC-4", "ABC-5"])

    out = analyst.node()(state)

    assert calls == ["react"] and out["portfolio_snapshot"]["materials"][0]["missingKeys"] == ["ABC-5"]
    miss = [row["fastPath"] for row in out["trace"] if row.get("fastPath")]
    assert miss[0]["complete"] is False and miss[0]["savedCalls"] == 0
    assert "requested_targets_complete" in miss[0]["missing"]
    task = analyst.task({**state, "ticket_progress": material["text"],
                         "portfolio_snapshot": out["portfolio_snapshot"]})
    assert "Query every missing key before concluding: ABC-5" in task
    assert "without another query" not in task


def test_portfolio_manifest_declares_machine_snapshot_and_evidence_outputs():
    from app.agent.workflow.role_manifest import ROLE_SPECS

    spec = ROLE_SPECS["portfolio_analyst"]
    assert {"portfolio_snapshot", "group_activity", "ticket_progress"} <= set(
        spec.output_keys
    )
    assert "portfolio_snapshot" in ROLE_SPECS["result_integrator"].input_keys

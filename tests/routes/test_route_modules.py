"""Contract tests for routes moved out of the application entrypoint."""

from app.main import app


def _routes():
    schema = app.openapi()
    return {
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        for method in operations
    }


def test_dashboard_router_preserves_public_api_contract():
    routes = _routes()

    expected = {
        ("GET", "/api/wbs"),
        ("GET", "/api/epic/{epic_key}/tree"),
        ("GET", "/api/epic/{epic_key}/progress"),
        ("GET", "/api/vit/shell"),
        ("GET", "/api/vit/module/{module}"),
        ("GET", "/api/vit/{key}"),
        ("GET", "/api/vit"),
        ("GET", "/api/workload"),
        ("GET", "/api/workload/shell"),
        ("GET", "/api/workload/module/{module}"),
        ("GET", "/api/workload/person/{user}"),
        ("GET", "/api/workload/{user}/{bucket}"),
        ("GET", "/api/workload/{user}"),
        ("GET", "/api/activity/{user}"),
        ("POST", "/api/refresh"),
    }

    assert expected <= routes


def test_resource_and_mytasks_routers_preserve_public_api_contract():
    expected = {
        ("GET", "/api/issue/{key}"),
        ("GET", "/api/issue/{key}/comments"),
        ("GET", "/api/recent"),
        ("POST", "/api/recent"),
        ("DELETE", "/api/recent"),
        ("GET", "/api/search"),
        ("GET", "/api/linktitle"),
        ("GET", "/api/favicon"),
        ("GET", "/api/mention/users"),
        ("GET", "/api/mention/user/{user_id}"),
        ("GET", "/api/img"),
        ("GET", "/api/file"),
        ("GET", "/api/mytasks"),
        ("GET", "/api/mytasks/stream"),
        ("GET", "/api/mytasks/sync/{sync_id}/group/{key}"),
        ("GET", "/api/mytasks/epics"),
        ("GET", "/api/mytasks/sync/{sync_id}/epics"),
    }
    assert expected <= _routes()


def test_ticket_routers_preserve_public_api_contract():
    read_routes = {
        ("GET", "/api/ticket/{key}"),
        ("GET", "/api/ticket/{key}/badge"),
        ("GET", "/api/ticket/{key}/timeline"),
        ("GET", "/api/ticket/{key}/children"),
        ("POST", "/api/ticket/{key}/comment"),
        ("PUT", "/api/ticket/{key}/comment/{cid}"),
        ("DELETE", "/api/ticket/{key}/comment/{cid}"),
        ("POST", "/api/ticket/{key}/refresh"),
    }
    command_routes = {
        ("PUT", "/api/ticket/{key}/assignee"),
        ("DELETE", "/api/ticket/{key}"),
        ("POST", "/api/ticket/{key}/child"),
        ("POST", "/api/task"),
        ("POST", "/api/bulk/create"),
        ("POST", "/api/bulk/update"),
        ("POST", "/api/epic"),
        ("PUT", "/api/ticket/{key}/fields"),
        ("POST", "/api/ticket/{key}/transition"),
        ("POST", "/api/ticket/{key}/attachment"),
    }
    assert read_routes | command_routes <= _routes()

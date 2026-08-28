"""Contract tests for routes moved out of the application entrypoint."""

from app.main import app


def test_dashboard_router_preserves_public_api_contract():
    schema = app.openapi()
    routes = {
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        for method in operations
    }

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

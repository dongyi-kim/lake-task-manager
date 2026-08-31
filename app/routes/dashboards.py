"""Dashboard API routes.

The application owns its long-lived Jira/cache objects.  This module only wires
those objects to HTTP endpoints so the domain builders remain usable without a
FastAPI application import.
"""

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.domain import rollup, vit, workload
from app.infra.settings import load_people, load_plan, reload_people


def build_dashboard_router(
    *,
    client: Any,
    cache: Any,
    settings: Any,
    scoped_people: Callable[[], dict],
    require_manager: Callable[[], None],
    require_module_access: Callable[[str], None],
    require_person_access: Callable[[str], None],
) -> APIRouter:
    """Create dashboard routes bound to the process-wide application services."""

    router = APIRouter()

    def with_partial(build: Callable[[], Any]) -> Any:
        """Attach partial-fetch metadata without coupling domain builders to HTTP."""
        client.miss_begin()
        data = build()
        missing = client.miss_count()
        if isinstance(data, dict) and missing:
            data["partial"] = True
            data["missing"] = missing
        return data

    @router.get("/api/wbs")
    def api_wbs():
        require_manager()
        plan = load_plan()

        def build():
            epic_progress = client.epic_progress_map(plan)
            return rollup.build(plan, epic_progress)

        data = with_partial(build)
        cache.add_snapshot("pmo", plan.get("project_key", "LAKE"), data["rollup"]["pmo"])
        return JSONResponse(data)

    @router.get("/api/epic/{epic_key}/tree")
    def api_epic_tree(epic_key: str):
        return JSONResponse(with_partial(lambda: {"tree": client.epic_tree(epic_key)}))

    @router.get("/api/epic/{epic_key}/progress")
    def api_epic_progress(epic_key: str):
        return JSONResponse(client.epic_progress_one(epic_key))

    @router.get("/api/vit/shell")
    def api_vit_shell():
        plan = load_plan()
        return JSONResponse(
            vit.build_vit_shell(client, plan, load_people(), jira_base=settings.jira_base)
        )

    @router.get("/api/vit/module/{module}")
    def api_vit_module(module: str):
        plan = load_plan()
        return JSONResponse(
            with_partial(lambda: vit.build_vit_module(client, plan, load_people(), module))
        )

    @router.get("/api/vit/{key}")
    def api_vit_detail(key: str):
        plan = load_plan()
        return JSONResponse(
            with_partial(lambda: vit.vit_detail(client, plan, load_people(), key))
        )

    @router.get("/api/vit")
    def api_vit():
        plan = load_plan()
        return JSONResponse(
            with_partial(
                lambda: vit.build_vit(client, plan, load_people(), jira_base=settings.jira_base)
            )
        )

    @router.get("/api/workload")
    def api_workload():
        plan = load_plan()
        return JSONResponse(
            workload.build_workload(client, plan, scoped_people(), jira_base=settings.jira_base)
        )

    @router.get("/api/workload/shell")
    def api_workload_shell():
        plan = load_plan()
        return JSONResponse(
            workload.build_workload_shell(
                client, plan, scoped_people(), jira_base=settings.jira_base
            )
        )

    @router.get("/api/workload/module/{module}")
    def api_workload_module(module: str):
        require_module_access(module)
        plan = load_plan()
        return JSONResponse(workload.build_workload_module(client, plan, load_people(), module))

    @router.get("/api/workload/person/{user}")
    def api_workload_person(user: str, days: int = 7, assignedWindow: str = "all"):
        require_person_access(user)
        return JSONResponse(
            workload.build_workload_person(client, user, days, assignedWindow)
        )

    @router.get("/api/workload/{user}/{bucket}")
    def api_workload_bucket(user: str, bucket: str, days: int = 7,
                            assignedWindow: str = "all"):
        require_person_access(user)
        rows = client.workload_bucket(user, bucket, days, assignedWindow)
        if rows is None:
            return JSONResponse({"error": "unknown bucket", "bucket": bucket}, status_code=404)
        return JSONResponse(rows)

    @router.get("/api/workload/{user}")
    def api_workload_tickets(user: str, days: int = 7, assignedWindow: str = "all"):
        require_person_access(user)
        return JSONResponse(client.workload_tickets(user, days, assignedWindow))

    @router.get("/api/activity/{user}")
    def api_activity(user: str):
        require_manager()
        return JSONResponse(client.activity(user))

    @router.post("/api/refresh")
    def api_refresh():
        cache.invalidate()
        client.advance_jql_generation()
        reload_people()
        return {"status": "refreshed"}

    return router

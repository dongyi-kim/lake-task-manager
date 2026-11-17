"""Tracking routes reuse one manager and enforce ownership on every stored read."""
import anyio
from fastapi import APIRouter, Body, HTTPException

from app.tracking.manager import TrackingManager


def build_tracking_router(*, get_client, manager=None):
    router = APIRouter()
    manager = manager or TrackingManager(get_client())
    cancel_limiter = anyio.CapacityLimiter(2)

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, "수집 결과를 찾을 수 없습니다.") from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/api/tracking/options")
    def options():
        return call(manager.options)

    @router.get("/api/tracking/runs")
    def runs():
        return {"runs": call(manager.list)}

    @router.post("/api/tracking/runs", status_code=202)
    def create(body: dict = Body(default={})):
        return call(manager.create, body)

    @router.get("/api/tracking/runs/{run_id}")
    def get(run_id: str):
        return call(manager.get, run_id)

    @router.post("/api/tracking/runs/{run_id}/resume", status_code=202)
    def resume(run_id: str):
        return call(manager.resume, run_id)

    @router.post("/api/tracking/runs/{run_id}/cancel")
    async def cancel(run_id: str):
        # Data handlers may occupy the default AnyIO pool. Cancellation retains
        # two dedicated workers and never performs disk/ownership work on the loop.
        return await anyio.to_thread.run_sync(call, manager.cancel, run_id, limiter=cancel_limiter)

    @router.post("/api/tracking/runs/{run_id}/summary")
    def summary(run_id: str):
        from app.tracking.summary import summarize_snapshot, SummaryInputError, SummaryUnavailable, SummaryFailure
        with manager.lock:
            guard = manager.summary_locks.setdefault(run_id, __import__("threading").Lock())
        if not guard.acquire(blocking=False):
            raise HTTPException(409, "요약을 생성하고 있습니다.")
        try:
            snapshot = call(manager.get, run_id)
            if snapshot["status"] in ("queued", "running"):
                raise HTTPException(409, "수집을 마친 뒤 요약해 주세요.")
            result = summarize_snapshot(snapshot)
            return call(manager.record_summary, run_id, result)
        except SummaryInputError as exc:
            raise HTTPException(422, str(exc)) from exc
        except SummaryUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except SummaryFailure as exc:
            raise HTTPException(502, str(exc)) from exc
        finally:
            guard.release()

    return router

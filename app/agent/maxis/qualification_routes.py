"""Explicit manager operations; no automatic review, key enrollment or activation."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import Field

from app.agent.maxis import profiles, qualification as q, qualification_runs as runs
from app.agent.maxis.setup_routes import _Body, _SafeRoute, _can_manage, _denied

router = APIRouter(prefix="/api/agent/maxis/qualification", route_class=_SafeRoute)


class RunBody(_Body):
    workflows: list[str] = Field(min_length=1, max_length=3)


class KeyBody(_Body):
    publicKey: str = Field(max_length=64)
    fingerprint: str = Field(max_length=64)
    confirmed: bool


class ArtifactBody(_Body):
    artifact: dict


def _call(function, *args, **kwargs):
    try:
        return JSONResponse(function(*args, **kwargs), headers={"Cache-Control": "no-store"})
    except (ValueError, KeyError, TypeError, OSError):
        return JSONResponse({"ok": False, "error": "PROD 평가·검수 입력과 현재 배포 일치를 확인하세요."},
                            status_code=400, headers={"Cache-Control": "no-store"})


@router.get("/status")
def status():
    if not _can_manage():
        return _denied()

    def read():
        profile = profiles.resolve("prod")
        return {**q.status(profile), "keys": [
            {**value, "keyId": fingerprint} for fingerprint, value in q.trusted_keys().items()
        ], "currentRun": runs.current(),
            "requirements": {"knowledgeCases": len(q.knowledge_cases(["agent_core", "editor_author"])),
                             "roleRepetitions": q.REPETITIONS,
                             "minimumScore": 4, "independentReview": True}}
    return _call(read)


@router.post("/runs")
def start(body: RunBody):
    if not _can_manage():
        return _denied()
    return _call(runs.start, body.workflows)


@router.get("/runs/{run_id}")
def progress(run_id: str):
    if not _can_manage():
        return _denied()
    return _call(runs.progress, run_id)


@router.get("/runs/{run_id}/review")
def review(run_id: str):
    if not _can_manage():
        return _denied()
    response = _call(runs.review_bundle, run_id)
    if response.status_code == 200:
        response.headers["Content-Disposition"] = 'attachment; filename="ltm-prod-qualification-review.json"'
    return response


@router.post("/keys")
def enroll(body: KeyBody):
    if not _can_manage():
        return _denied()
    return _call(q.enroll_key, body.publicKey, body.fingerprint, confirmed=body.confirmed)


@router.delete("/keys/{fingerprint}")
def revoke(fingerprint: str):
    if not _can_manage():
        return _denied()
    return _call(lambda: (q.revoke_key(fingerprint), {"ok": True})[1])


@router.post("/artifacts")
def install(body: ArtifactBody):
    if not _can_manage():
        return _denied()
    if len(q.canonical(body.artifact)) > q.MAX_BYTES:
        return JSONResponse({"ok": False, "error": "평가 파일이 너무 큽니다."}, status_code=413)
    return _call(lambda: q.install(body.artifact, profiles.resolve("prod")))

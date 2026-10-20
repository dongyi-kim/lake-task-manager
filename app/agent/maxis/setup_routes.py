"""Manager-only local PROD setup routes; deliberately no remote portal mutation."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agent.maxis import setup

class _SafeRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                return await original(request)
            except RequestValidationError:
                # Pydantic's default errors include the supplied value, including a
                # portal password accidentally entered into an unsupported field.
                return JSONResponse({"ok": False, "error": "MAXIS 준비 입력 형식을 확인하세요. 로그인 ID/PW와 token은 이 준비 요청에 넣지 않습니다."}, status_code=422)
        return handler


router = APIRouter(prefix="/api/agent/maxis/setup", route_class=_SafeRoute)


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HostBody(_Body):
    baseUrl: str = Field(max_length=512)
    confirmed: bool


class ModelBody(_Body):
    plugin_unique_identifier: str = Field(max_length=256)
    provider: str = Field(max_length=256)
    name: str = Field(max_length=128)
    completion_params: dict | None = None

    @field_validator("completion_params", mode="before")
    @classmethod
    def public_model_parameters(cls, value):
        return setup._materializer().validate_completion_params(value)


class ModelExportBody(_Body):
    yamlText: str = Field(max_length=2097152)


class BundleBody(_Body):
    runId: str = Field(max_length=32)
    baseUrl: str = Field(max_length=512)
    datasetId: str = Field(max_length=36)
    sentinelDatasetId: str = Field(max_length=36)
    roleMetadataFieldId: str = Field(max_length=36)
    sentinelDocumentId: str = Field(max_length=36)
    documentIds: dict[str, str]
    model: ModelBody


class CandidateBody(_Body):
    runId: str = Field(max_length=32)
    workflowVersions: dict[str, str]
    remoteLogPolicyId: str = Field(max_length=128)
    remoteLogMode: str
    remoteLogRetentionDays: int = Field(ge=0, le=90)


class ApplyBody(_Body):
    workflows: list[str] = Field(min_length=2, max_length=3)


def _can_manage() -> bool:
    try:
        from app.agent.tools.pmo_tools import _is_manager
        return bool(_is_manager(fresh=True))
    except Exception:
        return False


def _denied():
    return JSONResponse({"ok": False, "error": "MAXIS 준비와 설정 변경은 현재 Jira 매니저만 할 수 있습니다."}, status_code=403)


def _call(function, *args, **kwargs):
    try:
        return JSONResponse(function(*args, **kwargs))
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except OSError:
        return JSONResponse({"ok": False, "error": "MAXIS 준비 파일을 저장하지 못했습니다."}, status_code=503)


@router.get("")
def get_setup(profile: str = "prod"):
    return _call(setup.status, profile_name=profile)


@router.get("/guide")
def get_guide():
    """Serve only the public repository guide, including before any setup exists."""
    try:
        content = (setup._root() / "docs" / "agent-maxis-operations.md").read_text(encoding="utf-8")
    except OSError:
        return JSONResponse({"ok": False, "error": "저장소의 MAXIS 운영 가이드를 확인하세요."}, status_code=404)
    return PlainTextResponse(content, headers={
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    })


@router.put("/host")
def register_host(body: HostBody, profile: str = "prod"):
    if not _can_manage():
        return _denied()
    return _call(setup.register_prod_host, body.baseUrl, confirmed=body.confirmed, profile_name=profile)


@router.post("/knowledge")
def prepare_knowledge(profile: str = "prod"):
    if not _can_manage():
        return _denied()
    return _call(setup.prepare_knowledge, profile_name=profile)


@router.post("/bundle")
def prepare_bundle(body: BundleBody, profile: str = "prod"):
    if not _can_manage():
        return _denied()
    return _call(setup.prepare_bundle, body.model_dump(exclude_unset=True), profile_name=profile)


@router.post("/model")
def model_from_export(body: ModelExportBody):
    if not _can_manage():
        return _denied()
    return _call(setup.model_from_export, body.yamlText)


@router.post("/candidate")
def save_candidate(body: CandidateBody, profile: str = "prod"):
    if not _can_manage():
        return _denied()
    return _call(setup.save_candidate, body.model_dump(), profile_name=profile)


@router.post("/apply")
def apply_production(body: ApplyBody, profile: str = "prod"):
    if not _can_manage():
        return _denied()
    from app.agent.maxis import profiles
    from app.infra.prefs import PrefsPersistenceError
    try:
        return _call(profiles.apply_profile, profile, body.workflows)
    except PrefsPersistenceError:
        return JSONResponse({"ok": False, "error": "운영 설정을 저장하지 못했습니다. 이전 적용 상태를 유지합니다."}, status_code=503)


@router.get("/download/{run_id}/{kind}")
def download(run_id: str, kind: str, profile: str = "prod"):
    if not _can_manage():
        return _denied()
    try:
        path = setup.download(run_id, kind, profile_name=profile)
        return FileResponse(path, media_type="application/zip", filename="ltm-maxis-" + kind + ".zip",
                            headers={"Cache-Control": "no-store"})
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

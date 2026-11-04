"""SEP-2640 transport on the installed SDK's public Extension API.

The pinned SDK predates its built-in Skills helper. This adapter only registers
the two required methods; authentication and resource transport stay with MCP.
"""

from mcp.server.extension import Extension, MethodBinding, ResourceBinding
from mcp.server.mcpserver.resources import TextResource
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS, PaginatedRequestParams, RequestParams
from pydantic import StrictStr

from app.local_mcp.skill_catalog import SkillCatalog


class _GetSkillParams(RequestParams):
    uri: StrictStr


class SkillsExtension(Extension):
    identifier = "io.modelcontextprotocol/skills"

    def __init__(self, catalog: SkillCatalog):
        self.catalog = catalog

    def resources(self):
        entries = {row["uri"]: row["frontmatter"] for row in self.catalog.list_skills()}
        resources = []
        for file in self.catalog.files():
            metadata = entries.get(file["uri"], {})
            resources.append(ResourceBinding(TextResource(uri=file["uri"],
                name=metadata.get("name", file["uri"].split("/")[-1]),
                description=metadata.get("description"), mime_type=file["mimeType"], text=file["text"])))
        return resources

    def methods(self):
        return [MethodBinding("skills/list", PaginatedRequestParams, self._list),
                MethodBinding("skills/get", _GetSkillParams, self._get)]

    async def _list(self, ctx, params):
        if params.cursor is not None:
            raise MCPError(INVALID_PARAMS, "This complete skill catalog has no continuation cursor")
        return self._result(ctx, skills=self.catalog.list_skills())

    @staticmethod
    def _result(ctx, **result):
        if ctx.protocol_version >= "2026-07-28":
            result.update(resultType="complete", ttlMs=0, cacheScope="private")
        return result

    async def _get(self, ctx, params):
        try:
            return self._result(ctx, skill=self.catalog.get_skill(params.uri))
        except ValueError as exc:
            raise MCPError(INVALID_PARAMS, str(exc)) from None

"""Explicit output contracts for public tools, shared by SDK metadata and validation."""

from functools import lru_cache

from app.local_mcp.schemas.common import output_model


@lru_cache(maxsize=1)
def data_types():
    from app.local_mcp.schemas.tickets import DATA_MODELS as tickets
    from app.local_mcp.schemas.workspace import DATA_MODELS as workspace
    from app.local_mcp.schemas.mutations import DATA_MODELS as mutations
    from app.local_mcp.schemas.guides import DATA_MODELS as guides
    from app.local_mcp.schemas.llm import DATA_MODELS as llm

    result = {}
    for group in (tickets, workspace, mutations, guides, llm):
        if result.keys() & group.keys():
            raise ValueError("Duplicate MCP output contract")
        result.update(group)
    return result


@lru_cache(maxsize=None)
def schema_for(name, data_type=None, read_only=False):
    # Additional internal/test tools must declare their output type explicitly.
    if data_type is None:
        data_type = data_types()[name]
    return output_model(name, data_type, read_only)

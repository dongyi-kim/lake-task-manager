"""Render the one canonical lexical helper into the Dify guard template."""

HELPER_IMPORT = "from app.agent.maxis.source_grounding import source_lexical_parts"
HELPER_PATH = "app/agent/maxis/source_grounding.py"


def render_contract_guard(template: str, helper_source: str) -> str:
    """Replace exactly one standalone import; drift is not a repair opportunity."""
    lines = template.splitlines()
    if lines.count(HELPER_IMPORT) != 1:
        raise ValueError("Contract Guard canonical helper import drifted")
    lines[lines.index(HELPER_IMPORT)] = helper_source.rstrip()
    return "\n".join(lines).rstrip()

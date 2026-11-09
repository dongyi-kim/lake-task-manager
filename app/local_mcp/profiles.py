"""Optional catalogs reuse registered functions, auth, capacity, cache and receipts."""
from mcp.server import MCPServer

from app.local_mcp.skill_protocol import SkillsExtension

PROFILE_LABELS = {"all": "전체 기능", "jira": "Jira 업무", "confluence": "Confluence 문서",
                  "reporting": "조회·보고", "settings": "설정 관리"}
CONTROL = {"authenticate", "get_auth_status", "get_content_guide"}
CONFIG = {"get_workspace_config", "create_wbs_task", "update_wbs_task", "delete_wbs_task",
          "set_module_people", "rename_people_module", "delete_people_module"}
ADDITIVE = {"create_task", "create_subtask", "create_epic", "bulk_create", "add_comment",
            "add_issue_link", "add_document", "add_attachment", "create_confluence_page",
            "create_confluence_comment", "add_confluence_labels", "create_wbs_task", "create_llm_config"}


def includes(profile, tool):
    name = tool.name
    if profile == "all" or name in CONTROL:
        return True
    config = name in CONFIG or "llm" in name
    confluence = "confluence" in name
    if profile == "settings":
        return config
    if profile == "reporting":
        return bool(tool.annotations.read_only_hint) and not config
    if profile == "confluence":
        return confluence or name in {"search_workspace", "search_users", "get_user", "get_ticket", "get_tickets", "search_issues"}
    if profile == "jira":
        return not config and (not confluence or name in {"search_confluence_documents", "read_confluence_document"})
    return False


def build_profiles(runtime):
    result = {"all": (runtime.server, runtime.http_app)}
    for profile in (name for name in PROFILE_LABELS if name != "all"):
        server = MCPServer("lake-task-manager-" + profile, log_level="WARNING",
            extensions=[SkillsExtension(runtime.skill_catalog)],
            instructions=runtime.server.instructions + " This connection exposes the " + profile
                         + " catalog; unavailable tools require another profile or the full /mcp endpoint.")
        for tool in runtime.server._tool_manager.list_tools():
            if includes(profile, tool):
                # SDK 2.0 Tool records are immutable after LTM registration. Reuse
                # their compiled validators rather than compiling every schema five times.
                # Each server still owns its own catalog dictionary and HTTP sessions.
                server._tool_manager._tools[tool.name] = tool
        result[profile] = (server, server.streamable_http_app(streamable_http_path="/mcp/" + profile,
            stateless_http=True, json_response=True, max_request_body_size=16 * 1024 * 1024))
    return result

"""Local WBS planning and module roster management, never arbitrary YAML/path writes."""
from app.services.workspace_config import WbsTask, WbsTaskChanges


def register_admin(register, runtime):
    def get_workspace_config() -> dict:
        """Read manager-only current local configuration; returns revision, projectKey, tasks and people.
        WBS Tasks are PMO plans, not Jira tickets; their relative Epic weights need not sum to one.
        people maps module names to Jira user IDs; a person can belong to several modules.
        Roster modules, WBS modules and Jira Components are separate concepts: choose the requested
        grouping explicitly. This is not historical membership. Preserve revision for configuration writes.
        """
        return runtime.workspace_config.read()

    def create_wbs_task(task: WbsTask, expected_revision: str) -> dict:
        """Register a local WBS Task with a unique ID, module, dates and weighted Epic keys.
        task requires id/module/name/start/end/epics. Dates are YYYY-MM-DD with start < end;
        epics has 1..200 distinct key/weight entries, nonnegative weights and a positive total.
        Supply revision from get_workspace_config. This does not create or edit a Jira ticket.
        Requires manager permission; returns the full updated configuration with its new revision.
        """
        return runtime.workspace_config.create_task(task.model_dump(), expected_revision)

    def update_wbs_task(task_id: str, changes: WbsTaskChanges, expected_revision: str) -> dict:
        """Edit selected fields of a local WBS Task; preserve its ID and other tasks.
        Omit unchanged fields (do not send null). Dates use YYYY-MM-DD and start < end;
        epics replaces the complete weighted list and its total weight must remain positive.
        Re-read configuration after a 409 conflict; never overwrite another user's new plan.
        Requires manager permission and expected_revision; returns the updated configuration.
        """
        return runtime.workspace_config.update_task(task_id, changes.model_dump(exclude_unset=True), expected_revision)

    def delete_wbs_task(task_id: str, expected_revision: str) -> dict:
        """Remove one local WBS Task mapping; referenced Jira Epics/tickets remain intact.
        Requires manager permission and expected_revision from get_workspace_config.
        Returns updated configuration; an empty WBS plan is valid.
        """
        return runtime.workspace_config.delete_task(task_id, expected_revision)

    def set_module_people(module: str, user_ids: list[str], expected_revision: str) -> dict:
        """Create or replace one module's ordered Jira username roster; other modules stay intact.
        Resolve IDs with search_users/get_user; usernames must be nonblank, whitespace-free and unique
        within the module. Supply expected_revision from get_workspace_config, not a guessed value.
        Empty list keeps an empty module. A person may belong to several modules. This does not
        create Jira users or change manager permissions, issue assignees or Jira Components.
        Requires manager permission and expected_revision; returns the updated configuration.
        """
        return runtime.workspace_config.module_people(module, user_ids, expected_revision)

    def rename_people_module(module: str, new_name: str, expected_revision: str) -> dict:
        """Rename a people-roster module; WBS modules and Jira Components remain unchanged.
        Requires manager permission and expected_revision; returns the updated configuration.
        """
        return runtime.workspace_config.module_people(module, None, expected_revision, rename=new_name)

    def delete_people_module(module: str, expected_revision: str) -> dict:
        """Remove a module from the local people roster; Jira users and WBS plans remain intact.
        Requires manager permission and expected_revision; returns the updated configuration.
        """
        return runtime.workspace_config.module_people(module, None, expected_revision, remove=True)

    register(get_workspace_config, read_only=True)
    for fn in (create_wbs_task, update_wbs_task, delete_wbs_task, set_module_people,
               rename_people_module, delete_people_module):
        register(fn, read_only=False)

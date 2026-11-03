"""Local WBS planning and module roster management, never arbitrary YAML/path writes."""
from app.services.workspace_config import WbsTask, WbsTaskChanges


def register_admin(register, runtime):
    def get_workspace_config() -> dict:
        """Read manager-only local WBS Tasks, Epic weights and module people plus revision.
        WBS Tasks are local PMO plans, not Jira tickets. Weights are relative and need not sum
        to one; schedules belong to WBS. People IDs drive module filters/recommendations.
        """
        return runtime.workspace_config.read()

    def create_wbs_task(task: WbsTask, expected_revision: str) -> dict:
        """Register a local WBS Task with a unique ID, module, dates and weighted Epic keys.
        Supply revision from get_workspace_config. This does not create or edit a Jira ticket.
        """
        return runtime.workspace_config.create_task(task.model_dump(), expected_revision)

    def update_wbs_task(task_id: str, changes: WbsTaskChanges, expected_revision: str) -> dict:
        """Edit selected fields of a local WBS Task; preserve its ID and other tasks.
        Re-read configuration after a 409 conflict; never overwrite another user's new plan.
        """
        return runtime.workspace_config.update_task(task_id, changes.model_dump(exclude_unset=True), expected_revision)

    def delete_wbs_task(task_id: str, expected_revision: str) -> dict:
        """Remove only this local WBS Task mapping; referenced Jira Epics/tickets remain intact."""
        return runtime.workspace_config.delete_task(task_id, expected_revision)

    def set_module_people(module: str, user_ids: list[str], expected_revision: str) -> dict:
        """Create or replace one module's ordered Jira username roster; other modules stay intact.
        Empty list keeps an empty module. A person may belong to several modules. This does not
        create Jira users or change manager permissions, issue assignees or Jira Components.
        """
        return runtime.workspace_config.module_people(module, user_ids, expected_revision)

    def rename_people_module(module: str, new_name: str, expected_revision: str) -> dict:
        """Rename a roster module only. WBS modules and Jira Components are separate and unchanged."""
        return runtime.workspace_config.module_people(module, None, expected_revision, rename=new_name)

    def delete_people_module(module: str, expected_revision: str) -> dict:
        """Remove a module from the local people roster; do not delete Jira users or WBS plans."""
        return runtime.workspace_config.module_people(module, None, expected_revision, remove=True)

    register(get_workspace_config, read_only=True)
    for fn in (create_wbs_task, update_wbs_task, delete_wbs_task, set_module_people,
               rename_people_module, delete_people_module):
        register(fn, read_only=False)

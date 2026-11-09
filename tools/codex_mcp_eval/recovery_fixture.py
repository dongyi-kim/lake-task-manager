"""Private file MCP transport alongside a fresh, read-only work fixture."""
from tools.codex_mcp_eval.work_fixture import WorkFixture


class RecoveryFixture(WorkFixture):
    def __init__(self, *args, files_root, controller=None, **kwargs):
        self.controller = controller
        super().__init__(*args, files_root=files_root,
                         files_after_write=controller.after_write if controller else None, **kwargs)
        if controller:
            controller.files = self.files

    def observe_read(self, name, arguments, body):
        if self.controller:
            self.controller.observe_ltm(name, arguments, body)

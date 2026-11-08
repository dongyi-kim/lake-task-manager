"""Private file MCP transport alongside a fresh, read-only work fixture."""
from tools.codex_mcp_eval.research_files import ResearchFiles
from tools.codex_mcp_eval.work_fixture import WorkFixture


class RecoveryFixture(WorkFixture):
    def __init__(self, *args, files_root, controller=None, **kwargs):
        self.controller = controller
        self.files = ResearchFiles(files_root, after_write=controller.after_write if controller else None)
        if controller:
            controller.files = self.files
        self.files_http = self.files.server.streamable_http_app(streamable_http_path="/research-files",
            stateless_http=True, json_response=True)
        super().__init__(*args, **kwargs)

    def extra_http_apps(self):
        return [("/research-files", self.files_http)]

    @property
    def files_url(self):
        return f"http://127.0.0.1:{self.port}/research-files"

    def observe_read(self, name, arguments, body):
        if self.controller:
            self.controller.observe_ltm(name, arguments, body)

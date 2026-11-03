"""Small Jira permission fixture missing from jira820; shared by mock/local transports."""

from jira820.store import JiraError, Store


class PermissionStore(Store):
    # Tests can revoke these independently of LTM's manager/parent ownership rules.
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.permissions = {"CREATE_ISSUES": True, "MODIFY_REPORTER": True}
        self.denied_issue_keys = set()

    def get_issue(self, key):
        if key in self.denied_issue_keys:
            raise JiraError(403, "You do not have permission to view this issue.")
        return super().get_issue(key)

    def _check_reporter(self, fields):
        if "reporter" in fields and not self.permissions["MODIFY_REPORTER"]:
            raise JiraError(400, [], {"reporter": "You do not have permission to modify the reporter."})

    def create_issue(self, fields):
        if not self.permissions["CREATE_ISSUES"]:
            raise JiraError(403, "You do not have permission to create issues.")
        self._check_reporter(fields)
        # Server-side default. The actual LTM HTTP payload must omit this field.
        fields = {"reporter": {"name": self.config.current_user}, **fields}
        return super().create_issue(fields)

    def update_issue(self, key, fields):
        self._check_reporter(fields)
        return super().update_issue(key, fields)


def install_permission_route(app):
    @app.get("/rest/api/2/mypermissions")
    def mypermissions(issueKey: str = ""):
        store = app.state.store
        if issueKey:
            store.get_issue(issueKey)
        return {"permissions": {
            name: {"key": name, "havePermission": allowed}
            for name, allowed in store.permissions.items()
        }}

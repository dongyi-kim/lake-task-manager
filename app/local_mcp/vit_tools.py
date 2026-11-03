"""PMO_VIT is Jira label membership, not a second local WBS registration table."""


def register_vit(register, runtime):
    def get_vit_overview() -> dict:
        """List registered PMO_VIT roots, visible roots and nested roots hidden by a registered ancestor.
        Membership is the exact Jira PMO_VIT label; grouping uses Component/module. Progress counts
        done descendants / all descendants (WBS instead uses Story Points). get_vit_detail reads tree/news.
        Create a new VIT ticket with create_task labels=["PMO_VIT"] and its module components.
        """
        client = runtime.client
        client.miss_begin()
        bases = client.vit_bases()
        keys = {row["key"] for row in bases}
        visible, skipped = [], []
        for row in bases:
            parents = [key for key in row.get("ancestors") or [] if key in keys]
            if parents:
                skipped.append({"key": row["key"], "hiddenBy": parents})
            else:
                visible.append(row)
        return {"registered": bases, "visibleRoots": visible, "skipped": skipped,
                "partial": bool(client.miss_count()),
                "rules": {"selectionLabel": "PMO_VIT", "grouping": "Component/module",
                          "progress": "done descendants / all descendants", "newsDays": 21}}

    def set_vit_tracking(key: str, tracked: bool, client_mutation_id: str) -> dict:
        """Enroll/remove an existing ticket in PMO_VIT by adding/removing only the PMO_VIT label.
        Preserves other labels using a fresh read and shared field permissions/serializer/invalidation.
        A registered descendant is hidden while its ancestor is registered; see get_vit_overview.
        Done tickets follow the same field-edit restriction as the UI and require reopening first.
        """
        runtime.check_key(key)
        runtime.check_mutation_id(client_mutation_id)
        return runtime.commands.set_vit_tracking(key, tracked, client_mutation_id)

    register(get_vit_overview, read_only=True)
    register(set_vit_tracking, read_only=False)

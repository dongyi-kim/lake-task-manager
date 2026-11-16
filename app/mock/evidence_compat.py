"""App-owned Jira serializer additions; never patch installed jira820 globally."""
from jira820.serialize import Serializer, project_fields


class EvidenceSerializer(Serializer):
    def issue_fields(self, issue, base_url=""):
        fields = super().issue_fields(issue, base_url)
        creator = issue.get("creator") or issue.get("reporter")
        fields["creator"] = self.user_obj(creator) if creator else None
        return fields

    def issue_res(self, issue, base_url, fields="", expand=""):
        result = super().issue_res(issue, base_url, fields, expand)
        if "comment" in project_fields({"comment": None}, fields):
            comments = [self.comment_obj(issue["key"], index, comment)
                        for index, comment in enumerate(issue.get("comments", []))]
            result["fields"]["comment"] = {"startAt": 0, "maxResults": len(comments), "total": len(comments), "comments": comments}
        return result

    def comment_obj(self, key, index, comment):
        result = super().comment_obj(key, index, comment)
        result["updateAuthor"] = self.user_obj(comment.get("updateAuthor") or comment["author"])
        return result


def search_evidence(serializer, issue, base, fields, expand, *, comment_limit=None,
                    changelog_limit=None, supports_comment=True, supports_changelog=True):
    result = serializer.issue_res(issue, base, fields, expand)
    for enabled, container, name, rows, limit in (
        (supports_comment, result["fields"], "comment", "comments", comment_limit),
        (supports_changelog, result, "changelog", "histories", changelog_limit),
    ):
        if not enabled:
            container.pop(name, None)
        elif name in container and limit is not None:
            page = container[name]
            page[rows] = page[rows][:limit]
            page["maxResults"] = min(page["maxResults"], limit)
    return result

"""Bounded discovery of Jira create requirements and exact allowed values."""

import json

from app.services.ticket_commands import CommandError


def create_options(client, settings, issue_type, field_id="", start_at=0, max_results=20):
    metadata = client.create_fields(issue_type, strict=True)
    if field_id and field_id not in metadata:
        raise CommandError("생성 항목 목록에서 확인한 field_id를 사용하세요.", 404)
    epic = issue_type.casefold() == "epic"
    inputs = {name: ["body." + name] for name in ("summary", "priority", "duedate", "assignee", "components")}
    inputs.update(description=["body.descriptionHtml"])
    if not epic:
        inputs.update(labels=["body.labels"], parent=["parent_key"])
        inputs[settings.epic_link_field_id] = ["epic_key"]

    def field_row(field, value, *, detail=False):
        managed = field in {"project", "issuetype", "reporter"}
        paths = ["body.epicName"] if epic and client._is_epic_name_meta(value) else inputs.get(field, [])
        tools = (["create_epic"] if epic else ["create_task", "create_subtask"]) if managed or paths else []
        if field == "parent" and paths:
            tools = ["create_subtask"]
        elif field == settings.epic_link_field_id and paths:
            tools = ["create_task"]
        schema = value.get("schema") or {}
        row = {"name": value.get("name") or field, "required": bool(value.get("required")),
               "schema": {key: schema[key] for key in ("type", "items", "system", "custom") if key in schema},
               "hasDefaultValue": bool(value.get("hasDefaultValue", "defaultValue" in value)),
               "inputFields": paths, "inputTools": tools, "managed": managed, "supported": managed or bool(paths),
               "allowedValuesTotal": len(value.get("allowedValues") or []), "allowedValuesIncluded": detail,
               "defaultValueIncluded": detail and "defaultValue" in value}
        if detail and "defaultValue" in value:
            row["defaultValue"] = value["defaultValue"]
        return row

    def unsupported(options):
        return [field for field, value in options.items()
                if value["required"] and not value["supported"] and not value["hasDefaultValue"]]

    values_mode = bool(field_id)
    source = (metadata[field_id].get("allowedValues") or []) if values_mode else sorted(metadata)
    detail = field_row(field_id, metadata[field_id], detail=True) if values_mode else None

    def result(rows):
        options = ({field_id: {**detail, "allowedValues": rows}} if values_mode else
                   {field: field_row(field, metadata[field]) for field in rows})
        following = start_at + len(rows)
        return {"options": options, "issueType": issue_type, "projectKey": settings.project_key,
                "fieldId": field_id or None, "paginationKind": "allowedValues" if values_mode else "fields",
                "startAt": start_at, "returned": len(rows), "total": len(source),
                "hasMore": following < len(source), "nextStartAt": following if following < len(source) else None,
                "unsupportedRequired": unsupported(options)}

    def fits(data):
        return len(json.dumps(data, ensure_ascii=False).encode("utf-8")) <= 6144

    rows = []
    for item in source[start_at:start_at + max_results]:
        candidate = [*rows, item]
        if not fits(result(candidate)):
            if not rows:
                raise CommandError("단일 생성 필드·기본값·선택지가 응답 한도를 넘습니다. Jira에서 확인해 주세요.", 413)
            break
        rows = candidate
    data = result(rows)
    if not fits(data):
        raise CommandError("생성 필드 metadata가 응답 한도를 넘습니다. Jira에서 확인해 주세요.", 413)
    return data

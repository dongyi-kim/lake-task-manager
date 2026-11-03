"""Request schemas shared by UI and local MCP commands."""

from pydantic import BaseModel

class AssigneeBody(BaseModel):
    assignee: str = ""


class TransitionBody(BaseModel):
    id: str = ""
    targetStatusId: str = ""
    targetStatusName: str = ""
    targetStatusCategory: str = ""
    days: int = 0
    hours: int = 0
    minutes: int = 0
    assignee: str = ""
    resolution: str = ""
    commentHtml: str = ""
    clientMutationId: str | None = None


class FieldsBody(BaseModel):
    clientMutationId: str | None = None
    priority: str | None = None
    assignee: str | None = None
    reporter: str | None = None
    duedate: str | None = None
    labels: list[str] | None = None
    components: list[str] | None = None
    summary: str | None = None
    descriptionHtml: str | None = None
    epic: str | None = None


class ChildBody(BaseModel):
    type: str
    summary: str
    priority: str | None = None
    duedate: str | None = None
    assignee: str | None = None
    components: list[str] | None = None
    labels: list[str] | None = None
    descriptionHtml: str | None = None
    clientMutationId: str | None = None
    parentIsEpic: bool | None = None


class TaskBody(ChildBody):
    pass


class BulkBody(BaseModel):
    mode: str
    items: list[dict]


class BulkUpdateBody(BaseModel):
    items: list[dict]


class BulkCommentBody(BaseModel):
    items: list[dict]


class EpicBody(BaseModel):
    summary: str
    epicName: str | None = None
    priority: str | None = None
    duedate: str | None = None
    assignee: str | None = None
    components: list[str] | None = None
    descriptionHtml: str | None = None
    taskKeys: list[str] | None = None
    clientMutationId: str | None = None


class EpicLinkBody(BaseModel):
    taskKeys: list[str]


class LinkBody(BaseModel):
    clientMutationId: str | None = None
    key: str
    type: str = "Relates"
    direction: str = "outward"


class DocumentBody(BaseModel):
    clientMutationId: str | None = None
    url: str
    title: str = ""


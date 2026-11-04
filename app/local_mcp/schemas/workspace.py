"""Output data contracts for document, portfolio and local configuration tools."""

from typing import Literal

from pydantic import Field

from .common import JsonValue, OpenModel
from .tickets import MissingParent


class SearchItem(OpenModel):
    type: Literal["jira", "confluence"]
    title: str
    url: str
    key: str | None = None
    id: str | int | None = None
    excerpt: str | None = None
    space: str | None = None
    status: str | None = None
    statusCategory: str | None = None
    issuetype: str | None = None
    project: str | None = None
    assignee: str | None = None
    assigneeId: str | None = None
    epicKey: str | None = None
    epicName: str | None = None
    updated: str | None = None
    exact: bool | None = None
    path: list[str] | None = None


class SearchSource(OpenModel):
    items: list[SearchItem]
    error: str | None = None
    needLogin: bool | None = None
    authentication: dict[str, JsonValue] | None = None


class WorkspaceSearch(OpenModel):
    query: str
    scope: str
    jira: SearchSource
    confluence: SearchSource
    bitbucket: SearchSource


class DocumentSummary(OpenModel):
    id: str
    space: str
    title: str
    excerpt: str
    url: str
    modified: str | None


class DocumentPage(OpenModel):
    documents: list[DocumentSummary]
    canonicalCql: str
    scopeSpaces: list[str]
    startAt: int
    returned: int
    total: int | str | None
    hasMore: bool
    nextStartAt: int | None


class DocumentBody(OpenModel):
    id: str
    title: str
    space: str
    url: str
    version: dict[str, JsonValue]
    format: Literal["text", "storage"]
    content: str
    offset: int
    totalCharacters: int
    hasMore: bool
    nextOffset: int | None


class AttachmentBytes(OpenModel):
    id: str
    filename: str
    mimeType: str | None
    totalBytes: int
    offset: int
    base64: str
    hasMore: bool
    nextOffset: int | None


class TicketReference(OpenModel):
    key: str | None  # Unlinked work has a virtual "Epic 없음" ancestor.
    summary: str
    type: str
    statusCategory: str | None = None
    status: str | None = None
    assignee: str | None = None
    rel: str | None = None
    via: Literal["link", "mention"] | None = None


class DocumentLink(OpenModel):
    title: str
    url: str
    linkId: str | None = None
    mention: bool | None = None
    rel: str | None = None


class TicketContext(OpenModel):
    ancestors: list[TicketReference]
    siblings: list[TicketReference]
    related: list[TicketReference]
    documents: list[DocumentLink]


class FieldChange(OpenModel):
    date: str
    author: str
    field: str
    from_: str | None = Field(alias="from")
    to: str | None


class FieldHistory(OpenModel):
    history: list[FieldChange]
    limit: int
    mayHaveMore: bool


class EpicProgress(OpenModel):
    name: str
    doneSp: float
    totalSp: float
    mockSp: float
    progressPct: float
    countedIssues: int
    excludedIssues: int


class Timeline(OpenModel):
    start: str | None
    end: str | None


class ModuleLabel(OpenModel):
    id: str
    name: str


class WeightedEpic(OpenModel):
    epicKey: str
    epicName: str
    weight: float
    weightNorm: float
    epicPct: float
    contribution: float
    start: str
    end: str


class WbsProgress(OpenModel):
    id: str | int
    moduleId: str
    name: str | int | float | bool  # Legacy YAML scalars are returned without coercion.
    start: str
    end: str
    progressPct: float
    effectiveSp: float
    epics: list[WeightedEpic]


class SpRollup(OpenModel):
    progressPct: float
    rawDoneSp: float
    rawTotalSp: float
    rawMockSp: float


class ModuleRollup(SpRollup):
    id: str
    name: str
    effectiveSp: float
    start: str
    end: str
    wbsIds: list[str | int]


class PmoRollup(SpRollup):
    moduleCount: int
    wbsCount: int
    epicCount: int


class Rollup(OpenModel):
    pmo: PmoRollup
    modules: list[ModuleRollup]


class EpicRollup(OpenModel):
    key: str
    name: str
    doneSp: float
    totalSp: float
    mockSp: float
    progressPct: float


class WbsDashboard(OpenModel):
    generatedAt: str
    projectKey: str
    timeline: Timeline
    modules: list[ModuleLabel]
    epics: dict[str, EpicRollup]
    wbs: list[WbsProgress]
    rollup: Rollup


class CountProgress(OpenModel):
    done: int
    total: int
    pct: float


class VitIssue(TicketReference):
    progress: CountProgress
    counts: dict[str, int]
    statusCounts: dict[str, int]
    children: list[TicketReference]
    news: list[dict[str, str]]


class VitModule(OpenModel):
    module: str
    issues: list[VitIssue]


class VitSummary(OpenModel):
    total: int
    skippedDup: int
    byModule: dict[str, int]


class VitDashboard(OpenModel):
    generatedAt: str
    projectKey: str
    jiraBase: str
    modules: list[VitModule]
    summary: VitSummary


class EpicWorkload(OpenModel):
    count: int
    hr: float


class WorkloadBucket(OpenModel):
    count: dict[str, int]
    hr: dict[str, float]
    epics: dict[str, EpicWorkload]
    partial: bool | None = None
    missingParents: list[MissingParent] | None = None
    retryable: bool | None = None


class WorkloadPerson(OpenModel):
    id: str
    name: str
    kind: str | None
    open: WorkloadBucket
    inProgress: WorkloadBucket
    done7d: WorkloadBucket
    partial: bool | None = None
    missingParents: list[MissingParent] | None = None
    retryable: bool | None = None
    error: bool | None = None
    errorKind: str | None = None
    message: str | None = None


class WorkloadModule(OpenModel):
    module: str
    people: list[WorkloadPerson]
    peopleCount: int
    openTotal: int
    inProgressTotal: int
    done7dTotal: int
    avgInProgress: float
    avgDone7d: float


class WorkloadDashboard(OpenModel):
    generatedAt: str
    projectKey: str
    jiraBase: str
    scaleInProgress: int
    scaleDone7d: int
    modules: list[WorkloadModule]


class DescendantNode(TicketReference):
    children: list["DescendantNode"]
    created: str | None
    resolved: str | None


class CommentPreview(OpenModel):
    author: str
    date: str
    html: str


class VitDetail(OpenModel):
    key: str
    tree: list[DescendantNode]
    comments: list[CommentPreview]


class ActivityRow(OpenModel):
    date: str
    kind: str | None  # Atom feeds may provide a category without its optional term.
    key: str
    summary: str


class DocumentActivity(OpenModel):
    date: str
    title: str
    space: str


class ActivityError(OpenModel):
    source: str
    kind: str
    retryable: bool
    message: str


class Activity(OpenModel):
    user: str
    jira: list[ActivityRow]
    confluence: list[DocumentActivity]
    partial: bool
    sourceErrors: list[ActivityError]


class HiddenRoot(OpenModel):
    key: str
    hiddenBy: list[str]


class VitRules(OpenModel):
    selectionLabel: str
    grouping: str
    progress: str
    newsDays: int


class VitOverview(OpenModel):
    registered: list[TicketReference]
    visibleRoots: list[TicketReference]
    skipped: list[HiddenRoot]
    partial: bool
    rules: VitRules


class VitTracking(OpenModel):
    ok: bool
    key: str
    tracked: bool
    labels: list[str]


class EpicWeight(OpenModel):
    key: str
    weight: float


class WbsTask(OpenModel):
    id: str | int
    module: str
    name: str | int | float | bool
    start: str
    end: str
    epics: list[EpicWeight]


class WorkspaceConfig(OpenModel):
    revision: str
    projectKey: str
    tasks: list[WbsTask]
    people: dict[str, list[str]]


DATA_MODELS = {
    "search_workspace": WorkspaceSearch,
    "search_confluence_documents": DocumentPage,
    "read_confluence_document": DocumentBody,
    "read_attachment": AttachmentBytes,
    "get_ticket_context": TicketContext,
    "get_field_history": FieldHistory,
    "get_epic_progress": EpicProgress,
    "get_dashboard": WbsDashboard | VitDashboard | VitModule | WorkloadDashboard | WorkloadModule,
    "get_vit_detail": VitDetail,
    "get_activity": Activity,
    "get_vit_overview": VitOverview,
    "set_vit_tracking": VitTracking,
    **dict.fromkeys(("get_workspace_config", "create_wbs_task", "update_wbs_task", "delete_wbs_task",
                     "set_module_people", "rename_people_module", "delete_people_module"), WorkspaceConfig),
}

"""Ticket read projections; raw Jira/custom fields remain extensible, never discarded."""

from typing import Literal

from pydantic import Field

from .common import JsonValue, OpenModel


Category = Literal["todo", "inprogress", "done"]


class RawIssue(OpenModel):
    key: str
    fields: dict[str, JsonValue]
    id: str | int | None = None
    url: str | None = None


class SearchPage(OpenModel):
    issues: list[RawIssue]
    startAt: int
    maxResults: int
    total: int | None
    returned: int
    hasMore: bool
    nextStartAt: int | None
    snapshotId: str | None = None
    canonicalJql: str | None = None


class TicketBadge(OpenModel):
    key: str
    summary: str
    type: str
    status: str
    statusCategory: Category
    assignee: str | None
    assigneeId: str | None = None
    reporterId: str | None = None
    epicKey: str | None = None
    epicName: str | None = None
    epicSummary: str | None = None
    due: str | None = None
    updated: str | None = None
    resolved: str | None = None
    priority: str | None = None
    priRank: int | None = None
    isSubtask: bool | None = None


class DescriptionSection(OpenModel):
    html: str
    title: str | None = None


class TicketView(TicketBadge):
    subtask: bool
    reporter: str | None
    assigneeDisplay: str | None
    reporterDisplay: str | None
    created: str | None
    started: str | None
    labels: list[str]
    components: list[str]
    sp: float | None
    descriptionHtml: str
    descriptionEditHtml: str
    descriptionFormat: Literal["html", "text"]
    descriptionSections: list[DescriptionSection]
    url: str


class CommentPreview(OpenModel):
    id: str | None = None
    date: str
    updated: str | None = None
    author: str | None
    authorId: str | None = None
    html: str
    mine: bool


class Comments(OpenModel):
    comments: list[CommentPreview]
    returnedCount: int
    limit: int
    sourceLimit: int
    mayHaveMore: bool


class CommentSource(OpenModel):
    id: str
    html: str


class TimelineEvent(OpenModel):
    kind: str
    date: str | None
    author: str | None
    authorId: str | None
    field: str | None
    from_: str | None = Field(alias="from")
    to: str | None
    srcKey: str | None
    fromCat: str | None = None
    toCat: str | None = None


class History(OpenModel):
    timeline: list[TimelineEvent]
    limit: int


class Children(OpenModel):
    children: list[TicketBadge]


class EpicMetadata(OpenModel):
    key: str
    title: str
    summary: str | None = None
    epicName: str | None = None
    status: str | None = None
    statusCategory: Category | None = None
    assignee: str | None = None


class DisplayNode(OpenModel):
    key: str
    summary: str
    type: str
    statusName: str
    statusCat: Category
    start: str | None = None
    end: str | None = None
    sp: float | None = None
    component: str | None = None
    children: list["DisplayNode"] | None = None


class Epic(OpenModel):
    epic: EpicMetadata | None
    tree: list[DisplayNode]


class Identity(OpenModel):
    id: str
    name: str
    username: str | None = None
    display: str | None = None
    displayName: str | None = None
    avatar: str | None = None
    timezone: str | None = None
    actorAliases: list[str] | None = None


class PersonMatchContext(OpenModel):
    configured: bool | None
    sameModule: bool | None
    recentLookupAt: float | None
    recentSelectionAt: float | None
    recentMentionAt: float | None


class PersonCandidate(Identity):
    matchContext: PersonMatchContext


class PersonContextAvailability(OpenModel):
    configuration: bool
    history: bool
    actor: bool


class Users(OpenModel):
    users: list[PersonCandidate]
    returned: int
    limit: int
    mayHaveMore: bool | None
    coverage: Literal["bounded"]
    contextAvailable: PersonContextAvailability
    contextChanged: bool


class Option(OpenModel):
    id: str | int
    name: str


class EditField(Option):
    type: str
    required: bool
    operations: list[str]
    allowedValues: list[Option]


class ScreenField(Option):
    type: str
    system: str
    required: bool
    allowedValues: list[Option]
    inputFields: list[str] = Field(default_factory=list, description=(
        "Exact transition_ticket body argument paths. Required worklog maps to days/hours/minutes: "
        "at least one must be positive. Empty means no supported LTM input for this Jira field."))


class TransitionScreen(OpenModel):
    fields: list[ScreenField]
    unsupported: list[str]


class TransitionOption(Option):
    to: str
    toId: str
    toCategory: Category
    hasScreen: bool
    fields: TransitionScreen
    commentRequired: bool = Field(default=False, description=(
        "Send non-empty body.commentHtml. True for every Done transition under LTM policy, "
        "even when Jira omits comment from fields, or when Jira requires it on another transition."))


class EpicOption(OpenModel):
    key: str
    name: str
    summary: str


class LinkTypeOption(OpenModel):
    name: str
    inward: str
    outward: str


class CreateField(OpenModel):
    required: bool
    inputFields: list[str]
    inputTools: list[Literal["create_task", "create_subtask", "create_epic"]]
    schema_: dict[str, JsonValue] = Field(alias="schema")
    managed: bool
    supported: bool
    allowedValuesTotal: int
    allowedValuesIncluded: bool
    defaultValueIncluded: bool
    name: str | None = None
    hasDefaultValue: bool | None = None
    defaultValue: JsonValue = None
    allowedValues: list[JsonValue] | None = None


class Options(OpenModel):
    options: list[str | TransitionOption | EpicOption | LinkTypeOption | Option] | dict[str, EditField | CreateField]
    issueType: str | None = None
    projectKey: str | None = None
    unsupportedRequired: list[str] | None = None
    fieldId: str | None = None
    paginationKind: Literal["fields", "allowedValues"] | None = None
    startAt: int | None = None
    returned: int | None = None
    total: int | None = None
    hasMore: bool | None = None
    nextStartAt: int | None = None


class CategoryCounts(OpenModel):
    task: int
    subtask: int
    voc: int


class CategoryHours(OpenModel):
    task: float
    subtask: float
    voc: float


class EpicWorkload(OpenModel):
    count: int
    hr: float


class MissingParent(OpenModel):
    parentKey: str
    issueKeys: list[str]
    kind: str
    retryable: bool
    message: str
    bucket: str | None = None


class WorkloadBucket(OpenModel):
    count: CategoryCounts
    hr: CategoryHours
    epics: dict[str, EpicWorkload]
    partial: bool | None = None
    missingParents: list[MissingParent] | None = None
    retryable: bool | None = None


class WorkloadStatistics(OpenModel):
    id: str
    displayName: str
    open: WorkloadBucket
    inProgress: WorkloadBucket
    done7d: WorkloadBucket
    epicNames: dict[str, str]
    assignedWindow: str | None = None
    doneDays: int | None = None
    error: bool | None = None
    errorKind: str | None = None
    status: int | None = None
    message: str | None = None
    partial: bool | None = None
    missingParents: list[MissingParent] | None = None
    retryable: bool | None = None


class EpicResolution(OpenModel):
    complete: bool
    parentKey: str | None
    kind: str | None
    retryable: bool


class WorkloadTicket(OpenModel):
    key: str
    summary: str
    type: str
    status: str
    statusCategory: Category
    due: str | None
    resolved: str | None
    priority: str | None
    priRank: int
    epic: str | None
    voc: bool
    components: list[str]
    epicName: str | None = None
    epicResolution: EpicResolution | None = None


class WorkloadTickets(OpenModel):
    user: str
    assignedWindow: str
    open: list[WorkloadTicket]
    inProgress: list[WorkloadTicket]
    done7d: list[WorkloadTicket]


class Workload(OpenModel):
    statistics: WorkloadStatistics
    tickets: WorkloadTickets


class Attachment(OpenModel):
    id: str
    filename: str
    size: int
    mime: str
    created: str | None
    author: str | None
    url: str | None
    thumb: str | None
    isImage: bool


class Attachments(OpenModel):
    attachments: list[Attachment]


class DocumentLink(OpenModel):
    title: str
    url: str
    linkId: str | None = None
    mention: bool | None = None
    rel: str | None = None


class Documents(OpenModel):
    documents: list[DocumentLink]


class EvidenceRecord(OpenModel):
    id: str | int
    authorId: str | None
    authorDisplayName: str | None
    author: dict[str, JsonValue] | None = None
    created: str | None = None
    updated: str | None = None
    body: str | None = None
    renderedBody: str | None = None
    items: list[dict[str, JsonValue]] | None = None


class EvidenceChunk(OpenModel):
    id: str | int
    sourceIndex: int
    recordJson: str
    recordOffset: int
    nextRecordOffset: int | None
    totalCharacters: int
    recordSha256: str


class EvidencePage(OpenModel):
    key: str
    kind: Literal["comments", "history"]
    items: list[EvidenceRecord | EvidenceChunk]
    total: int | None
    sourceStartAt: JsonValue
    sourceComplete: bool
    complete: bool
    partial: bool
    incompleteReason: str | None
    consistency: str
    startAt: int
    maxResults: int
    returned: int
    hasMore: bool | None
    nextStartAt: int | None
    availableTotal: int | None = None


class TicketArguments(OpenModel):
    key: str


class TicketDetails(OpenModel):
    tool: Literal["get_ticket"]
    arguments: TicketArguments


class Descendant(OpenModel):
    key: str
    parentKey: str | None
    depth: int
    epicKey: str | None
    issue: RawIssue
    projectionOmissions: list[str] | None = None
    details: TicketDetails | None = None


class MissingBranch(OpenModel):
    key: str
    kind: str
    stage: str
    parentKey: str | None = None


class Descendants(OpenModel):
    rootKey: str
    includeRoot: bool
    issues: list[Descendant]
    startAt: int
    maxResults: int
    returned: int
    total: int
    hasMore: bool
    nextStartAt: int | None
    complete: bool
    partial: bool
    missing: list[MissingBranch]
    missingStartAt: int
    missingTotal: int
    missingHasMore: bool
    nextMissingStartAt: int | None
    consistency: str
    visibility: str


DATA_MODELS = {
    "search_issues": SearchPage, "get_ticket": TicketView, "get_comments": Comments,
    "get_comment_source": CommentSource,
    "get_history": History, "get_children": Children, "get_epic": Epic,
    "search_users": Users, "get_user": Identity | None, "list_options": Options,
    "get_workload": Workload, "get_attachments": Attachments, "get_documents": Documents,
    "get_evidence_page": EvidencePage, "get_descendants": Descendants,
}

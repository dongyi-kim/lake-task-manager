"""Structured-output contracts for the Work Architect agent.

The runtime agent remains available from ``workflow.agents.work_architect``.  Keeping the
transport schemas here makes them independently reviewable and prevents the orchestration
facade from becoming the only place where every Work Architect concern can live.
"""

from __future__ import annotations

import copy


ITEM = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "Distinct Korean summary for one deliverable."},
        "tier": {"type": "string", "enum": ["epic", "task", "subtask"],
                 "description": "Hierarchy tier. Bug, Story, Improvement, and Feature are all task tier."},
        "issue_type": {"type": "string",
                       "description": "Exact issue type name allowed by project createmeta."},
        "type": {"type": "string", "description": "Exact allowed Jira type such as Task, Story, Bug, Improvement, or Sub-Task."},
        "epic": {"type": "string", "description": "Verified parent Epic key in task mode; empty for an intentional top-level Task."},
        "epic_name": {"type": "string",
                      "description": "Short Korean Epic label for WBS and badges, at most ten characters; empty uses summary."},
        "parent": {"type": "string", "description": "Verified Task-tier parent key in subtask mode."},
        "description": {
            "type": "string",
            "description": (
                "Korean HTML ticket body. A general Task uses <h3>배경</h3>, <h3>작업 범위</h3>, "
                "and <h3>완료 조건 (DoD)</h3> in that order, with optional <h3>참고</h3>. Background "
                "states only a verified trigger or that the user's concrete change was requested. When no "
                "reason is verified, write the literal action followed by `요청됨`; never add generic claims "
                "about user experience, efficiency, accuracy, performance, stability, or operational benefit, "
                "and never specialize a vague verb such as `개선` into an unmentioned quality dimension. Scope "
                "includes inclusions and exclusions; DoD contains two to four independently testable "
                "<li data-checked=\"false\"> items. A Bug instead separates "
                "재현 경로, 기대 동작, and 실제 동작. Every reference must contain a real ticket key or "
                "verified URL and explain relevance. Never copy one reference list across unrelated items. "
                "Represent real child work in children, not as a prose candidate list."),
        },
        "children": {
            "type": "array",
            "description": (
                "Actual Sub-Tasks created under this new parent. Use separate children for distinct execution "
                "units and distribute repeated target batches when evidence supports it. Do not decompose work "
                "whose approach is still undecided."),
            "items": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Distinct Korean child summary; do not copy the parent title."},
                    "description": {"type": "string",
                                    "description": "Korean HTML with only this child's 작업 범위 and 완료 조건 (DoD); do not copy parent background."},
                    "duedate": {"type": "string", "description": "YYYY-MM-DD, or empty when unknown."},
                },
                "required": ["summary"],
            },
        },
        "components": {"type": "array", "items": {"type": "string"}},
        "labels": {"type": "array", "items": {"type": "string"}},
        "priority": {"type": "string"},
        "duedate": {"type": "string", "description": "YYYY-MM-DD, or empty when unknown; never invent."},
    },
    "required": ["summary", "type"],
}


QUESTION = {
    "type": "object",
    "properties": {
        "question": {"type": "string", "description": "One concise Korean question."},
        "kind": {"type": "string", "enum": ["text", "choice", "multi", "date"],
                 "description": "choice=one option, multi=multiple options, date=calendar value, text=free prose. "
                                "Prefer choice whenever a recommendation is possible; reserve text for facts "
                                "such as reproduction steps or background. The UI adds a custom-input option."},
        "options": {"type": "array", "items": {"type": "string"},
                    "description": "Two to five Korean options for choice, with the recommended option first and an optional short reason."},
        "field": {"type": "string",
                  "enum": ["", "assignee", "epic", "priority", "duedate", "component",
                           "target", "parent", "scope", "background", "acceptance",
                           "reproduction", "structure", "status"],
                  "description": "Ticket field being asked; the UI supplies field-specific autocomplete."},
        "required_input": {
            "type": "boolean",
            "description": ("True only when no valid and truthful draft can be produced without user-owned "
                            "information. False for a preference with a safe default or omission."),
        },
        "why_required": {
            "type": "string",
            "description": ("One concise Korean reason naming the unresolved decision or payload field when "
                            "required_input is true; otherwise an empty string."),
        },
    },
    "required": ["question", "kind", "required_input", "why_required"],
}


SCHEMA = {
    "type": "object",
    "properties": {
        "interpretation": {
            "type": "string",
            "description": ("Only in a pre-research interpretation turn: two or three Korean sentences "
                            "covering target, purpose, and intended artifact. Empty in other turns."),
        },
        "questions": {
            "type": "array", "items": QUESTION,
            "description": ("At most three questions about user-owned scope, DoD, deadline, or intent. "
                            "Never ask for a fact available through internal retrieval."),
        },
        "mode": {"type": "string", "enum": ["task", "subtask", "epic"],
                 "description": "Creation mode. subtask requires a verified existing Task-tier parent. epic contains exactly one Epic item."},
        "structure": {
            "type": "string",
            "enum": ["single_task", "task_with_subtasks", "multiple_tasks", "new_epic"],
            "description": (
                "Structure decision. Default to single_task. task_with_subtasks is one deliverable split by "
                "stage, target, or owner; multiple_tasks is independent deliverables; new_epic requires at "
                "least two sprints, at least three cross-module or cross-owner Tasks, no suitable verified "
                "existing Epic, and an explicit independent reporting intent."),
        },
        "structure_why": {
            "type": "string",
            "description": "One Korean sentence citing the factual signal behind the structure decision.",
        },
        "items": {"type": "array", "items": ITEM,
                  "description": "Ticket drafts. May be empty while a blocking question remains."},
        "change": {
            "type": "object",
            "description": "Existing-ticket change plan for modify intent only; items stays empty.",
            "properties": {
                "key": {"type": "string", "description": "One verified existing ticket key to change."},
                "keys": {"type": "array", "items": {"type": "string"},
                         "description": "Complete verified key snapshot for the same bulk change; leave key empty."},
                "assignee": {"type": "string", "description": "New user ID; empty unassigns; omit when unchanged."},
                "duedate": {"type": "string", "description": "New YYYY-MM-DD due date; omit when unchanged."},
                "priority": {"type": "string", "description": "New exact priority; omit when unchanged."},
                "summary": {"type": "string", "description": "New Korean summary; omit when unchanged."},
                "description": {"type": "string",
                                "description": "Complete replacement HTML body, only when requested."},
                "labels": {"type": "array", "items": {"type": "string"},
                           "description": "Complete replacement labels; omit when unchanged."},
                "components": {"type": "array", "items": {"type": "string"},
                               "description": "Complete replacement component list with exactly one value; omit when unchanged."},
                "status": {"type": "string",
                           "description": "Exact target status name for a requested transition; code resolves its ID."},
                "link": {"type": "object",
                         "description": "Only for an explicit ticket-link request.",
                         "properties": {
                             "other": {"type": "string", "description": "Verified other ticket key."},
                             "relation": {"type": "string",
                                          "description": "Verified Jira link type such as Blocks or Relates."}}},
                "comment": {"type": "string",
                            "description": "Korean comment explicitly requested with the change; otherwise omit."},
            },
        },
        "rationale": {"type": "string", "description": "Two or three Korean user-visible sentences explaining the structure or change."},
    },
    "required": ["questions", "mode", "items"],
}


CREATE_CHILD = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "summary": {"type": "string", "maxLength": 180},
        "scope_in": {"type": "array", "minItems": 1, "maxItems": 6,
                     "items": {"type": "string", "maxLength": 320}},
        "dod": {"type": "array", "minItems": 1, "maxItems": 5,
                "items": {"type": "string", "maxLength": 320}},
        "duedate": {"type": "string", "maxLength": 20},
    },
    "required": ["summary", "scope_in", "dod"],
}

CREATE_ITEM = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "summary": {"type": "string", "maxLength": 180},
        "tier": {"type": "string", "enum": ["epic", "task", "subtask"]},
        "issue_type": {"type": "string", "maxLength": 80},
        "type": {"type": "string", "maxLength": 80},
        "epic": {"type": "string", "maxLength": 40},
        "epic_name": {"type": "string", "maxLength": 80},
        "parent": {"type": "string", "maxLength": 40},
        "background": {"type": "string", "maxLength": 1000},
        "reproduction": {"type": "array", "maxItems": 6,
                         "items": {"type": "string", "maxLength": 360}},
        "expected": {"type": "string", "maxLength": 700},
        "actual": {"type": "string", "maxLength": 700},
        "scope_in": {"type": "array", "minItems": 1, "maxItems": 8,
                     "items": {"type": "string", "maxLength": 360}},
        "scope_out": {"type": "array", "maxItems": 6,
                      "items": {"type": "string", "maxLength": 320}},
        "dod": {"type": "array", "minItems": 1, "maxItems": 6,
                "items": {"type": "string", "maxLength": 360}},
        "references": {"type": "array", "maxItems": 6,
                       "items": {"type": "string", "maxLength": 500}},
        "children": {"type": "array", "maxItems": 30, "items": CREATE_CHILD},
        "components": {"type": "array", "maxItems": 3,
                       "items": {"type": "string", "maxLength": 80}},
        "labels": {"type": "array", "maxItems": 8,
                   "items": {"type": "string", "maxLength": 80}},
        "priority": {"type": "string", "maxLength": 80},
        "duedate": {"type": "string", "maxLength": 20},
    },
    "required": ["summary", "type", "background", "scope_in", "dod"],
}

CREATE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "interpretation": {"type": "string", "maxLength": 600},
        "questions": {"type": "array", "maxItems": 3, "items": QUESTION},
        "mode": {"type": "string", "enum": ["task", "subtask", "epic"]},
        "structure": {"type": "string", "enum": [
            "single_task", "task_with_subtasks", "multiple_tasks", "new_epic"]},
        "structure_why": {"type": "string", "maxLength": 500},
        "items": {"type": "array", "maxItems": 12, "items": CREATE_ITEM},
        "rationale": {"type": "string", "maxLength": 800},
    },
    "required": ["questions", "mode", "items"],
}


CHANGE_FIELDS = {
    "key": {"type": "string", "maxLength": 40,
            "description": "One verified existing ticket key."},
    "keys": {"type": "array", "maxItems": 30,
             "items": {"type": "string", "maxLength": 40},
             "description": "Complete verified target snapshot for one bulk change."},
    "assignee": {"type": "string", "maxLength": 120},
    "duedate": {"type": "string", "maxLength": 20},
    "priority": {"type": "string", "maxLength": 80},
    "summary": {"type": "string", "maxLength": 240},
    "description": {"type": "string", "maxLength": 12000},
    "labels": {"type": "array", "maxItems": 20,
               "items": {"type": "string", "maxLength": 80}},
    "components": {"type": "array", "maxItems": 3,
                   "items": {"type": "string", "maxLength": 80}},
    "status": {"type": "string", "maxLength": 120},
    "link": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "other": {"type": "string", "maxLength": 40},
            "relation": {"type": "string", "maxLength": 120},
        },
        "required": ["other", "relation"],
    },
    "comment": {"type": "string", "maxLength": 12000},
}

UPDATE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "questions": {"type": "array", "maxItems": 3, "items": QUESTION},
        "change": {
            "type": "object", "additionalProperties": False,
            "properties": CHANGE_FIELDS,
            "description": "Existing-ticket update/comment/transition/link plan only.",
        },
        "rationale": {"type": "string", "maxLength": 800},
    },
    "required": ["questions"],
}

COMMENT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "questions": {"type": "array", "maxItems": 3, "items": QUESTION},
        "change": {
            "type": "object", "additionalProperties": False,
            "properties": {
                key: copy.deepcopy(CHANGE_FIELDS[key])
                for key in ("key", "keys", "comment")
            },
            "description": "Comment-only plan; no Jira field mutation is permitted.",
        },
        "rationale": {"type": "string", "maxLength": 800},
    },
    "required": ["questions"],
}


__all__ = [
    "CHANGE_FIELDS",
    "COMMENT_SCHEMA",
    "CREATE_CHILD",
    "CREATE_ITEM",
    "CREATE_SCHEMA",
    "ITEM",
    "QUESTION",
    "SCHEMA",
    "UPDATE_SCHEMA",
]

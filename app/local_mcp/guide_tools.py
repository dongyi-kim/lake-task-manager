"""Read packaged authoring instructions without requiring a Jira session."""

from typing import Annotated, Literal

from pydantic import Field


def register_guides(register, runtime):
    def get_content_guide(
        skill: Literal["", "ltm-task-authoring", "ltm-comment-authoring", "ltm-bulk-task-create", "ltm-confluence-authoring"] = "",
        path: Annotated[str, Field(max_length=200)] = "SKILL.md",
    ) -> dict:
        """Read LTM's version-matched Task, comment, bulk-create or Confluence authoring skills.
        With no skill, return the catalog and each skill's file manifest. Choose a skill to read
        SKILL.md; follow its relative references with the same skill and path, e.g.
        references/content-format.md or references/people-and-links.md. Returns URI, MIME,
        text, SHA-256 digest and byte size; reads only packaged guides, never arbitrary files/URLs.
        Covers HTML versus bulk Markdown, person resolution/mentions, Jira/Confluence/URL links,
        tables, checklists, code, images and preserving existing content when editing.
        All authoring skills include references/draft-review.md: recommend sharing a readable draft
        and destination for confirmation before submission; honor existing approval of that content
        or explicit direct-submit instructions without redundant confirmation. This is guidance,
        not a required server approval step; draft-only requests do not authorize publishing.
        Confluence authoring uses storage XHTML, published version checks and its own reference files;
        select ltm-confluence-authoring before document/comment editing or page organization.
        Also available via skills/list, skills/get and resources/read for capable clients.
        No Jira login or upstream request is needed; local MCP authentication still applies.
        """
        return runtime.skill_catalog.guide(skill, path)

    register(get_content_guide, read_only=True)

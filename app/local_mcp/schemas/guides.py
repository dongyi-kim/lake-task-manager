"""Typed catalog and document contracts for the authoring-guide fallback tool."""

from typing import Literal

from app.local_mcp.schemas.common import OpenModel


class SkillFrontmatter(OpenModel):
    name: str
    description: str


class SkillFile(OpenModel):
    uri: str
    digest: str
    size: int


class SkillEntry(OpenModel):
    uri: str
    frontmatter: SkillFrontmatter
    resources: list[SkillFile]


class GuideIndex(OpenModel):
    skills: list[SkillEntry]


class GuideDocument(SkillFile):
    mimeType: Literal["text/markdown", "application/json", "text/html"]
    text: str


DATA_MODELS = {"get_content_guide": GuideIndex | GuideDocument}

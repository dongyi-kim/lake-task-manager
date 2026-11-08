"""Immutable packaged authoring guides, shared by MCP resources and the guide tool."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import yaml


SKILL_NAMES = ("ltm-task-authoring", "ltm-comment-authoring", "ltm-bulk-task-create", "ltm-confluence-authoring")
RESEARCH_SKILL_NAMES = ("ltm-work-research", "ltm-work-reporting")
_MIME_TYPES = {".md": "text/markdown", ".html": "text/html", ".json": "application/json"}
MAX_RESOURCE_BYTES = 8 * 1024  # Keep each progressive-disclosure read below the context budget.


class SkillCatalog:
    def __init__(self, root: Path | None = None, *, include_research: bool = False):
        root = (root or Path(__file__).with_name("skills")).resolve()
        self._entries, self._files = {}, {}
        shared = self._load_files(root / "_shared", root)
        research = self._load_files(root / "_research", root) if include_research else {}
        self.names = SKILL_NAMES + (RESEARCH_SKILL_NAMES if include_research else ())
        for name in self.names:
            files = self._load_files(root / name, root)
            if name in RESEARCH_SKILL_NAMES:
                for path, content in research.items():
                    destination = "references/" + path
                    if destination in files:
                        raise ValueError(f"Duplicate skill resource: {name}/{destination}")
                    files[destination] = content
            for path, content in shared.items():
                # Draft review is format-independent; Jira markup/workflow guides are not.
                if (name == "ltm-confluence-authoring" or name in RESEARCH_SKILL_NAMES) and path not in {
                        "draft-review.md", "korean-writing.md"}:
                    continue
                destination = "references/" + path
                if destination in files:
                    raise ValueError(f"Duplicate skill resource: {name}/{destination}")
                files[destination] = content
            text = files["SKILL.md"]["text"]
            lines = text.splitlines()
            if not lines or lines[0] != "---" or "---" not in lines[1:]:
                raise ValueError(f"Missing skill frontmatter: {name}")
            frontmatter = yaml.safe_load("\n".join(lines[1:lines.index("---", 1)]))
            if (not isinstance(frontmatter, dict) or frontmatter.get("name") != name
                    or not isinstance(frontmatter.get("description"), str) or not frontmatter["description"].strip()):
                raise ValueError(f"Invalid skill frontmatter: {name}")
            if len(files) > 512 or sum(file["size"] for file in files.values()) > 16 * 1024 * 1024:
                raise ValueError(f"Skill exceeds SEP-2640 limits: {name}")
            resources = []
            for path, content in sorted(files.items()):
                uri = f"skill://{name}/{path}"
                self._files[uri] = {"uri": uri, **content}
                resources.append({"uri": uri, "digest": content["digest"], "size": content["size"]})
            uri = f"skill://{name}/SKILL.md"
            self._entries[uri] = {"uri": uri, "frontmatter": frontmatter, "resources": resources}

    @staticmethod
    def _load_files(directory: Path, root: Path):
        if not directory.is_dir() or directory.is_symlink() or not directory.resolve().is_relative_to(root):
            raise ValueError("Invalid packaged skill directory")
        files = {}
        for path in sorted(directory.rglob("*")):
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError("Packaged skills cannot contain symbolic links")
            if path.is_dir():
                continue
            if not path.is_file() or path.suffix not in _MIME_TYPES:
                raise ValueError("Unsupported packaged skill resource")
            data = path.read_bytes()
            if len(data) > MAX_RESOURCE_BYTES:
                raise ValueError("Split large skill resources into smaller reference files")
            files[path.relative_to(directory).as_posix()] = {
                "mimeType": _MIME_TYPES[path.suffix], "text": data.decode("utf-8"),
                "digest": "sha256:" + sha256(data).hexdigest(), "size": len(data),
            }
        return files

    def list_skills(self):
        return deepcopy(list(self._entries.values()))

    def get_skill(self, uri: str):
        if uri not in self._entries:
            raise ValueError("Unknown skill URI")
        return deepcopy(self._entries[uri])

    def read(self, uri: str):
        # Exact catalog lookup: request text is never treated as a filesystem path.
        if uri not in self._files:
            raise ValueError("Unknown skill resource URI")
        return deepcopy(self._files[uri])

    def files(self):
        return deepcopy(list(self._files.values()))

    def guide(self, skill: str = "", path: str = "SKILL.md"):
        if not skill:
            if path != "SKILL.md":
                raise ValueError("Select a skill before requesting a resource")
            return {"skills": self.list_skills()}
        if skill not in self.names:
            raise ValueError("Unknown or unavailable packaged skill")
        return self.read(f"skill://{skill}/{path}")

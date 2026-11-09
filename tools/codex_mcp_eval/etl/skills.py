"""A/B's deployed research guides, loaded as Git blobs without a checkout."""
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import subprocess

from app.local_mcp.skill_catalog import SkillCatalog, RESEARCH_SKILL_NAMES, _MIME_TYPES

DEPLOYED_COMMIT = "9b6fde102fb8420f2fd135d72b90b25a9af666c5"
_PREFIX = "app/local_mcp/skills/"


@lru_cache(maxsize=1)
def _deployed_catalog():
    repository = Path(__file__).resolve().parents[3]
    tree = subprocess.run(["git", "ls-tree", "-rz", DEPLOYED_COMMIT, "--", _PREFIX],
                          cwd=repository, check=True, capture_output=True).stdout
    entries = []
    for item in tree.split(b"\0"):
        if not item:
            continue
        metadata, path = item.split(b"\t", 1)
        _mode, kind, object_id = metadata.split()
        path = path.decode("utf-8")
        if kind == b"blob" and Path(path).suffix in _MIME_TYPES:
            entries.append((path[len(_PREFIX):], object_id))
    if not entries:
        raise RuntimeError("The deployed research skill resources are unavailable in Git")
    objects = subprocess.run(["git", "cat-file", "--batch"], cwd=repository,
        input=b"".join(object_id + b"\n" for _, object_id in entries), check=True, capture_output=True).stdout
    files, offset = {}, 0
    for path, expected_id in entries:
        header_end = objects.index(b"\n", offset)
        object_id, kind, size = objects[offset:header_end].split()
        if object_id != expected_id or kind != b"blob":
            raise RuntimeError("Unexpected Git skill blob response")
        offset = header_end + 1
        data = objects[offset:offset + int(size)]
        offset += int(size) + 1
        files[path] = {"mimeType": _MIME_TYPES[Path(path).suffix], "text": data.decode("utf-8"),
                       "digest": "sha256:" + sha256(data).hexdigest(), "size": len(data)}

    class GitCatalog(SkillCatalog):
        @staticmethod
        def _load_files(directory, root):
            prefix = directory.relative_to(root).as_posix() + "/"
            return {path[len(prefix):]: deepcopy(content) for path, content in files.items() if path.startswith(prefix)}

    return GitCatalog(include_research=True)


def research_catalog(condition, *args, **kwargs):
    """All arms receive research skills; only research resource bytes vary.

    Authoring guides remain current and identical. Baseline A/B overlay the two
    research packages (including their remapped shared references) from deployed
    Git blobs. Candidate C keeps the current enhanced packages. Nothing is
    exported to disk or made available as source code to the subject.
    """
    if condition not in {"baseline", "candidate"}:
        raise ValueError("Invalid ETL research skill condition")
    kwargs["include_research"] = True
    catalog = SkillCatalog(*args, **kwargs)
    if condition == "baseline":
        deployed = _deployed_catalog()
        prefixes = tuple("skill://" + name + "/" for name in RESEARCH_SKILL_NAMES)
        catalog._files = {uri: value for uri, value in catalog._files.items() if not uri.startswith(prefixes)}
        catalog._files.update({uri: deepcopy(value) for uri, value in deployed._files.items() if uri.startswith(prefixes)})
        for name in RESEARCH_SKILL_NAMES:
            uri = f"skill://{name}/SKILL.md"
            catalog._entries[uri] = deployed.get_skill(uri)
    return catalog

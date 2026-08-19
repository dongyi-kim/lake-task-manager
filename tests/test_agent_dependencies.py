from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version


ROOT = Path(__file__).resolve().parents[1]


def test_jsonschema_is_a_direct_pinned_runtime_dependency():
    """Agent validation must not depend on MCP retaining an incidental dependency."""
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    pins = {
        name.strip().casefold(): version.strip()
        for raw in requirements
        for line in [raw.split("#", 1)[0].strip()]
        if line and "==" in line
        for name, version in [line.split("==", 1)]
    }

    assert pins["jsonschema"] == "4.26.0"
    assert metadata.version("jsonschema") == pins["jsonschema"]


def test_instructor_is_a_direct_pinned_compatible_mit_runtime_dependency():
    """The validated Instructor/OpenAI 2 runtime stays reproducible and compatible."""
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    pins = {
        name.strip().casefold(): version.strip()
        for raw in requirements
        for line in [raw.split("#", 1)[0].strip()]
        if line and "==" in line
        for name, version in [line.split("==", 1)]
    }

    assert pins["instructor"] == "1.15.4"
    assert pins["openai"] == "2.54.0"
    assert pins["jiter"] == "0.14.0"
    assert metadata.version("instructor") == pins["instructor"]

    instructor = metadata.metadata("instructor")
    dependencies = [Requirement(row) for row in instructor.get_all("Requires-Dist") or []]
    openai_requirement = next(row for row in dependencies if row.name == "openai" and not row.marker)
    jiter_requirement = next(row for row in dependencies if row.name == "jiter" and not row.marker)
    assert Version(metadata.version("openai")) in openai_requirement.specifier
    assert Version(metadata.version("jiter")) in jiter_requirement.specifier
    assert str(instructor.get("License") or "").strip() == "MIT"


def test_instructor_uses_the_supported_public_patch_api():
    import instructor

    assert callable(instructor.patch)
    assert instructor.Mode.JSON.value


def test_nh3_is_a_direct_pinned_compatible_mit_runtime_dependency():
    """The editor security boundary uses a maintained HTML5 sanitizer, not a transitive extra."""
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    pins = {
        name.strip().casefold(): version.strip()
        for raw in requirements
        for line in [raw.split("#", 1)[0].strip()]
        if line and "==" in line
        for name, version in [line.split("==", 1)]
    }

    assert pins["nh3"] == "0.3.6"
    assert metadata.version("nh3") == pins["nh3"]
    package = metadata.metadata("nh3")
    assert str(package.get("License") or "").strip() == "MIT"

    import nh3

    assert callable(nh3.Cleaner)

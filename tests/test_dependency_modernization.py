"""Compatibility contracts for the reproducible Python 3.11 dependency baseline."""

from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version


ROOT = Path(__file__).resolve().parents[1]

RUNTIME_PINS = {
    "fastapi": "0.141.1",
    "uvicorn": "0.52.3",
    "requests": "2.34.2",
    "pyyaml": "6.0.3",
    "python-multipart": "0.0.32",
    "pydantic": "2.13.4",
    "pywin32": "312",
    "jira820": "0.12.0",
    "langchain": "1.3.15",
    "langchain-core": "1.5.6",
    "langchain-openai": "1.5.1",
    "langchain-community": "0.4.2",
    "langgraph": "1.2.11",
    "langgraph-prebuilt": "1.1.0",
    "openai": "2.54.0",
    "instructor": "1.15.4",
    "jiter": "0.14.0",
    "faiss-cpu": "1.15.0",
    "tiktoken": "0.14.0",
    "langfuse": "4.14.4",
    "httpx": "0.28.1",
    "certifi": "2026.7.22",
    "tzdata": "2026.3",
    "jsonschema": "4.26.0",
    "nh3": "0.3.6",
    "mcp": "2.0.0",
}

SSO_PINS = {
    "playwright": "1.62.0",
    "pystray": "0.19.5",
    "pillow": "12.3.0",
}


def _direct_requirements(filename: str) -> dict[str, Requirement]:
    rows: dict[str, Requirement] = {}
    for raw in (ROOT / filename).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        req = Requirement(line)
        rows[canonicalize_name(req.name)] = req
    return rows


def _dependency(package: str, dependency: str) -> Requirement:
    wanted = canonicalize_name(dependency)
    rows = [Requirement(row) for row in metadata.requires(package) or []]
    return next(
        row for row in rows
        if canonicalize_name(row.name) == wanted and row.marker is None
    )


def test_all_direct_runtime_dependencies_are_exact_stable_pins():
    requirements = _direct_requirements("requirements.txt")
    actual = {
        name: next(iter(req.specifier)).version
        for name, req in requirements.items()
        if len(req.specifier) == 1 and next(iter(req.specifier)).operator == "=="
    }
    assert actual == RUNTIME_PINS
    assert all(not Version(version).is_prerelease for version in actual.values())


def test_sso_and_test_dependencies_are_exact_stable_pins():
    sso = _direct_requirements("requirements-sso.txt")
    assert {
        name: next(iter(req.specifier)).version for name, req in sso.items()
    } == SSO_PINS
    test = _direct_requirements("requirements-test.txt")
    assert next(iter(test["pytest"].specifier)).version == "9.1.1"


def test_installed_runtime_matches_pins_on_this_platform():
    requirements = _direct_requirements("requirements.txt")
    for name, expected in RUNTIME_PINS.items():
        marker = requirements[name].marker
        if marker is not None and not marker.evaluate():
            continue
        assert metadata.version(name) == expected


def test_ai_stack_versions_satisfy_every_direct_interpackage_constraint():
    installed = {name: Version(metadata.version(name)) for name in (
        "langchain", "langchain-core", "langchain-openai", "langchain-community",
        "langgraph", "langgraph-prebuilt", "openai", "instructor", "jiter", "pydantic",
    )}
    edges = (
        ("langchain", "langchain-core"),
        ("langchain", "langgraph"),
        ("langchain-openai", "langchain-core"),
        ("langchain-openai", "openai"),
        ("langchain-community", "langchain-core"),
        ("langgraph", "langchain-core"),
        ("langgraph", "langgraph-prebuilt"),
        ("instructor", "openai"),
        ("instructor", "jiter"),
        ("instructor", "pydantic"),
    )
    for package, dependency in edges:
        requirement = _dependency(package, dependency)
        assert installed[dependency] in requirement.specifier, (
            f"{package} requires {requirement}, installed {installed[dependency]}"
        )


def test_modernized_public_apis_used_by_ltm_are_importable():
    from langchain_community.vectorstores import FAISS
    from langchain_openai import (
        AzureChatOpenAI,
        AzureOpenAIEmbeddings,
        ChatOpenAI,
        OpenAIEmbeddings,
    )
    from langfuse.langchain import CallbackHandler
    from langgraph.graph import END, START, StateGraph
    from langgraph.graph.message import add_messages
    from langgraph.prebuilt import ToolNode
    from mcp import Client, ClientSession, StdioServerParameters
    from mcp.server import MCPServer

    assert all(value is not None for value in (
        FAISS, AzureChatOpenAI, AzureOpenAIEmbeddings, ChatOpenAI, OpenAIEmbeddings,
        CallbackHandler, END, START, StateGraph, add_messages, ToolNode, Client, ClientSession,
        StdioServerParameters, MCPServer,
    ))

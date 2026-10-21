"""Compare a validated local graph to a Dify export without exposing its contents."""
import copy
from contextlib import contextmanager
import hashlib
import json
import uuid


@contextmanager
def publication_receipt(path):
    """Reserve an attempt before network/secret access; never recycle a claim.

    A crash leaves a claim requiring manual inspection, not permission to retry.
    Ordinary exceptions and cancellation preserve a content-free failure receipt.
    """
    if path.exists():
        raise FileExistsError("publication receipt already exists")
    with path.with_suffix(path.suffix + ".claim").open("x", encoding="utf-8"):
        pass
    record = {"status": "started", "phase": "reserved"}
    try:
        yield record
    except BaseException:
        record["status"] = "failed"
        raise
    finally:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(record, stream, indent=2)


def semantic_graph(graph: dict) -> dict:
    """Ignore presentation positions and Dify-generated editor-local UUIDs only.

    This is a comparison projection, not a substitute for validate_bundle. All
    execution data, prompts, schemas, selectors, model and recovery settings stay.
    Node IDs are preserved so every selector remains bound to the same source.
    """
    nodes = {}
    for node in graph["nodes"]:
        key = node["id"]
        if not isinstance(key, str) or not key or key in nodes:
            raise ValueError("invalid or duplicate node")
        data = copy.deepcopy(node["data"])
        for field in ("selected", "desc"):
            data.pop(field, None)
        if data.get("type") == "llm":
            for row in data["prompt_template"]:
                row.pop("id", None)
        if data.get("type") == "code":
            data["code"] = data["code"].replace("\r\n", "\n").rstrip()
        if data.get("title") == "Role Knowledge":
            for condition in data["metadata_filtering_conditions"]["conditions"]:
                uuid.UUID(condition["id"])
                condition["id"] = "<editor-local-uuid>"
        nodes[key] = data
    edges = []
    seen = set()
    for edge in graph["edges"]:
        route = (edge["source"], edge["sourceHandle"], edge["target"], edge["targetHandle"])
        if route in seen or route[0] not in nodes or route[2] not in nodes:
            raise ValueError("invalid or duplicate edge")
        seen.add(route)
        # Ignore edge display style, not execution handles or iteration/loop data.
        edges.append({"route": route, "data": copy.deepcopy(edge["data"])})
    return {"nodes": nodes, "edges": sorted(edges, key=lambda row: row["route"])}


def graph_sha256(graph: dict) -> str:
    return hashlib.sha256(json.dumps(semantic_graph(graph), ensure_ascii=False,
                                    sort_keys=True, separators=(",", ":")).encode()).hexdigest()

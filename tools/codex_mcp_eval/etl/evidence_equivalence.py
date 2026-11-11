"""Narrow host-only proof equivalences; no inferred JQL or semantic claims."""
from datetime import datetime, timezone
import math
import re

from tools.codex_mcp_eval.work_evidence import document_complete, literal_source_records
from .report_checks import public_text


def full_instant(value):
    """Require full source precision and an explicit offset, never a date prefix."""
    if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})", value):
        return None
    try:
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    except ValueError:
        return None


def unchanged_status_observed(calls, fact):
    """A current status last updated before the view can prove that view's value.

    This proves a requested historical status, not the transition itself. A
    later observation cannot establish what was known before that observation.
    Resolution date is deliberately unused: resolution can precede a reopen.
    """
    if fact.get("field") != "status" or not isinstance(fact.get("valueAtView"), str):
        return False
    cutoff, known = full_instant(fact.get("viewAt")), full_instant(fact.get("knowledgeAt"))
    if cutoff is None or known is None:
        return False
    for call in calls:
        result = call.get("result") or {}
        if not isinstance(result, dict):
            continue
        cache, data = result.get("cache"), result.get("data")
        if (call.get("name") not in {"get_ticket", "get_tickets", "search_issues"}
                or call.get("isError") or result.get("ok") is not True
                or result.get("partial") is not False or type(result.get("missing")) is not int or result["missing"] != 0
                or not isinstance(cache, dict) or cache.get("available") is not True
                or cache.get("state") != "fresh" or cache.get("invalidated") is not False
                or cache.get("compliant") is not True or cache.get("upstreamValidated") is not True
                or not isinstance(data, dict) or data.get("partial") or data.get("errors")
                or data.get("missingKeys") or data.get("hasMore") or data.get("complete") is False
                or call.get("name") == "get_tickets" and data.get("complete") is not True):
            continue
        stamp = cache.get("fetchedAt")
        if type(stamp) not in (int, float) or not math.isfinite(stamp):
            continue
        try:
            observed = datetime.fromtimestamp(stamp, timezone.utc)
        except (ValueError, OSError, OverflowError):
            continue
        if not cutoff < observed <= known:
            continue
        for record in literal_source_records(call, "ticket"):
            fields = record.get("fields")
            if record.get("key") != fact["key"] or not isinstance(fields, dict):
                continue
            status = fields.get("status")
            created, updated = full_instant(fields.get("created")), full_instant(fields.get("updated"))
            if (isinstance(status, dict) and status.get("name") == fact["valueAtView"]
                    and created is not None and updated is not None and created <= updated <= cutoff):
                return True
    return False


def mapping_identity_keys(calls, contracts, public_sources):
    """Only a designated exact mapping revision can bind its literal Epic roster."""
    identities = set()
    for contract in contracts:
        source = public_sources.get(contract["source"], {})
        if (source.get("kind") != "confluence_revision" or source.get("id") != contract["documentId"]
                or source.get("version") != contract["version"]):
            continue
        trusted = []
        for call in calls:
            result = call.get("result") or {}
            if not isinstance(result, dict):
                continue
            data = result.get("data")
            if (call.get("name") == "read_confluence_document" and not call.get("isError")
                    and result.get("ok") is True and not result.get("partial") and isinstance(data, dict)
                    and data.get("id") == contract["documentId"] and isinstance(data.get("version"), dict)
                    and data["version"].get("number") == contract["version"] and not data.get("partial")
                    and not data.get("errors") and type(data.get("offset")) is int and data["offset"] >= 0
                    and type(data.get("totalCharacters")) is int and data["totalCharacters"] >= 0
                    and isinstance(data.get("content"), str)):
                trusted.append(call)
        if not document_complete(trusted, contract["documentId"], version=contract["version"]):
            continue
        chunks = {(call["result"]["data"]["offset"]): call["result"]["data"]["content"] for call in trusted}
        text = public_text("".join(chunks[offset] for offset in sorted(chunks)))
        if text != public_text(source.get("text", "")):
            continue
        # Membership is host-designated from this mapping event, never every key
        # mentioned somewhere in an arbitrary document or a caller argument.
        literals = {contract["wbs"], *contract["epicKeys"]}
        if all(re.search(r"(?<![\w-])" + re.escape(value) + r"(?![\w-])", text) for value in literals):
            identities.update(contract["epicKeys"])
    return identities

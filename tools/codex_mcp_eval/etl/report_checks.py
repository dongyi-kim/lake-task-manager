"""Host-only report matching; wording tolerance never substitutes typed evidence."""
from copy import deepcopy
from html.parser import HTMLParser
import json
import re

from tools.codex_mcp_eval.work_evidence import document_complete


def public_text(value):
    class Text(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts = []

        def handle_data(self, data):
            self.parts.append(data)

        def handle_starttag(self, tag, attrs):
            self.parts.append(" ")

        def handle_endtag(self, tag):
            self.parts.append(" ")

    parser = Text()
    parser.feed(value)
    return " ".join("".join(parser.parts).split())


def row_identity(row):
    if not isinstance(row, dict):
        return None
    row = deepcopy(row)
    for name in ("candidateKeys", "implementation", "testAccepted", "finalAccepted", "planned"):
        if name not in row:
            continue
        values = row[name]
        if (not isinstance(values, list) or any(not isinstance(value, str) for value in values)
                or len(values) != len(set(values))):
            return None
        row[name] = sorted(values)
    return json.dumps(row, ensure_ascii=False, sort_keys=True)


def merge_requirements(target, source):
    target.setdefault("requiredSources", []).extend(source.get("requiredSources", []))
    target.setdefault("requiredFieldFacts", []).extend(source.get("requiredFieldFacts", []))
    for key, fields in source.get("requiredTicketInputs", {}).items():
        target.setdefault("requiredTicketInputs", {}).setdefault(key, {}).update(fields)
    return target


def grounded_name(literal, canonical, entry, calls, sources):
    """Only a longer literal phrase from the read, numbered source section qualifies."""
    if literal == canonical:
        return True
    if not isinstance(literal, str) or not canonical or canonical not in literal:
        return False
    row = entry["row"]
    source = sources.get(entry.get("reportSource"), {})
    if not document_complete(calls, row["documentId"], version=row["version"]):
        return False
    sections = re.findall(r"<h2\b[^>]*>\s*(\d+)\..*?</h2>(.*?)(?=<h2\b|$)",
                          source.get("text", ""), flags=re.DOTALL | re.IGNORECASE)
    texts = [public_text(body) for number, body in sections if int(number) == row["section"]]
    if len(texts) != 1 or literal not in texts[0]:
        return False
    # Reconstruct an actually read revision, rather than trusting arguments,
    # search excerpts, a different revision, or the host's source catalog alone.
    groups = {}
    for call in calls:
        result = call.get("result") or {}
        data = result.get("data") or {}
        if (call.get("name") == "read_confluence_document" and not call.get("isError")
                and result.get("ok") is True and str(data.get("id")) == str(row["documentId"])
                and (data.get("version") or {}).get("number") == row["version"]
                and type(data.get("offset")) is int and type(data.get("totalCharacters")) is int
                and isinstance(data.get("content"), str)):
            group = groups.setdefault((data.get("format"), data["totalCharacters"]), {"chunks": {}, "valid": True})
            offset, text = data["offset"], data["content"]
            group["valid"] &= offset not in group["chunks"] or group["chunks"][offset] == text
            group["chunks"][offset] = text
    for (_, total), group in groups.items():
        content, offset = [], 0
        while offset in group["chunks"] and group["chunks"][offset]:
            content.append(group["chunks"][offset])
            offset += len(group["chunks"][offset])
        if (group["valid"] and offset == total
                and public_text(source.get("text", "")) == public_text("".join(content))):
            return True
    return False


def match_findings(actual, required, catalog, calls, sources):
    if not isinstance(actual, list):
        return None
    entries = {row_identity(item["row"]): item for item in catalog}
    for row in required:
        entries.setdefault(row_identity(row), {"row": row})
    selected = {}
    for row in actual:
        if row_identity(row) is None:
            return None
        matches = []
        for identity, entry in entries.items():
            canonical = entry["row"]
            candidate = {**row, "deliverable": canonical.get("deliverable")}
            if (row_identity(candidate) == identity and grounded_name(
                    row.get("deliverable"), canonical.get("deliverable"), entry, calls, sources)):
                matches.append((identity, entry))
        if len(matches) != 1 or matches[0][0] in selected:
            return None
        identity, entry = matches[0]
        selected[identity] = {**entry, "literal": row["deliverable"]}
    if not {row_identity(row) for row in required} <= selected.keys():
        return None
    return list(selected.values())


def match_progress(actual, required, catalog, findings):
    if not isinstance(actual, list):
        return None
    aliases = set()
    stages = {"implementation": "implementation", "test_acceptance": "testAccepted",
              "final_acceptance": "finalAccepted", "plan": "planned"}
    for entry in findings or []:
        claim = entry["row"]
        if (not claim.get("inScope") or claim.get("matchStatus") != "verified"
                or claim["stage"] in {"test_acceptance", "final_acceptance"} and claim["outcome"] != "accepted"):
            continue
        for key in claim["candidateKeys"]:
            aliases.add((key, stages[claim["stage"]], claim["deliverable"], entry["literal"]))
    entries = {row_identity(item["row"]): item for item in catalog}
    for row in required:
        entries.setdefault(row_identity(row), {"row": row})
    selected = {}
    for row in actual:
        if row_identity(row) is None:
            return None
        matches = []
        for identity, entry in entries.items():
            canonical = entry["row"]
            candidate = deepcopy(row)
            for field in stages.values():
                if field not in canonical:
                    continue
                resolved = []
                for literal in row.get(field, []):
                    names = [name for name in canonical[field] if literal == name or
                             (canonical.get("key"), field, name, literal) in aliases]
                    if len(names) != 1:
                        break
                    resolved.append(names[0])
                else:
                    candidate[field] = resolved
                    continue
                break
            else:
                if row_identity(candidate) == identity:
                    matches.append((identity, entry))
        if len(matches) != 1 or matches[0][0] in selected:
            return None
        identity, entry = matches[0]
        selected[identity] = entry
    if not {row_identity(row) for row in required} <= selected.keys():
        return None
    return list(selected.values())


def report_contract(answer, expected, calls, sources):
    catalog = expected.get("reportCatalog", {})
    findings = match_findings(answer.get("reportFindings", []), expected["reportFindings"],
                              catalog.get("findings", []), calls, sources)
    progress = match_progress(answer.get("reportProgress", []), expected["reportProgress"],
                              catalog.get("progress", []), findings)
    requirements = {}
    for entry in [*(findings or []), *(progress or [])]:
        merge_requirements(requirements, entry)
    return findings is not None, progress is not None, requirements

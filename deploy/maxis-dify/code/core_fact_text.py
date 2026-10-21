"""Exact record selection shared by draft assemblers; no semantic repair.

Prepend strict draft validation/context. The caller supplies _fail and must still
validate the canonical payload and pass both evidence guards.
"""


class _SourceNumber(str):
    """Keep JSON numeric lexemes exact inside selected records."""


def _source_json(value):
    if isinstance(value, _SourceNumber):
        return str(value)
    if isinstance(value, dict):
        return "{" + ",".join(_canonical(key) + ":" + _source_json(item) for key, item in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ",".join(_source_json(item) for item in value) + "]"
    return _canonical(value)


def _record_text(value):
    """Display every original field/value; never summarize, label or omit evidence."""
    rows = []

    def visit(item, path):
        if isinstance(item, dict) and item:
            for key, child in item.items():
                visit(child, path + [key])
        elif isinstance(item, list) and item:
            for index, child in enumerate(item):
                visit(child, path + [str(index)])
        else:
            # Quote boundaries as data: a delimiter inside a description must not
            # impersonate another field (e.g. "blocked; status: Done").
            text = _source_json(item)
            rows.append(" / ".join(_canonical(key) for key in path) + ": " + text)

    visit(value, [])
    return "; ".join(rows)


def _fact_text(content, pointer, payload_path, *, readable_record=False):
    """Select an original record, never infer its subject from a scalar status."""
    try:
        parsed = json.loads(content, object_pairs_hook=lambda rows: _pairs(rows, _fail),
                            parse_int=_SourceNumber, parse_float=_SourceNumber,
                            parse_constant=lambda _: _fail())
    except json.JSONDecodeError:
        if pointer:
            _fail()
        return content
    scalar_field = re.fullmatch(r"/(?:references/[0-9]+/ref|concepts/[0-9]+/term)", payload_path)
    if not pointer:
        if not scalar_field and isinstance(parsed, dict) and parsed:
            return _record_text(parsed) if readable_record else _source_json(parsed)
        _fail()
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        _fail()
    value = parsed
    tokens = pointer[1:].split("/")
    # Retain the record's subject, not a detached nested status/date fragment.
    if not scalar_field and not (
        isinstance(parsed, list) and len(tokens) == 1
        or isinstance(parsed, dict) and len(tokens) == 2
        and isinstance(parsed.get(tokens[0].replace("~1", "/").replace("~0", "~")), list)
    ):
        _fail()
    for token in tokens:
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", key) and int(key) < len(value):
            value = value[int(key)]
        else:
            _fail()
    if scalar_field and type(value) is str:
        return value
    if not scalar_field and isinstance(value, dict) and value:
        return _record_text(value) if readable_record else _source_json(value)
    _fail()

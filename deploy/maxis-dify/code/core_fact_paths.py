"""Advisory source-relative pointers, not evidence or acceptance authority.

Keep original content untouched. A bounded list helps a model avoid inventing
paths through the enclosing source object (for example /content/tickets/0).
The original assembler independently resolves every selected path.
"""
def _fact_paths(content, *, records_only):
    def reject():
        raise ValueError("invalid source JSON")

    class Number(str):
        pass

    def encode(value):
        if isinstance(value, Number):
            return str(value)
        if isinstance(value, dict):
            return "{" + ",".join(_canonical(k) + ":" + encode(v) for k, v in value.items()) + "}"
        if isinstance(value, list):
            return "[" + ",".join(encode(v) for v in value) + "]"
        return _canonical(value)

    try:
        root = json.loads(content, object_pairs_hook=lambda rows: _pairs(rows, reject),
                          parse_int=Number, parse_float=Number, parse_constant=lambda _: reject())
    except json.JSONDecodeError:
        return {"paths": [""] if content.strip() and len(content) <= 1200 else [], "complete": True}
    except (ValueError, RecursionError):
        # Malformed/ambiguous JSON has no suggested path. No repair or replacement.
        return {"paths": [], "complete": False}
    # Bound before serializing any subtree. Optional hints must not turn deeply
    # nested but otherwise admitted source text into an input-stage exception.
    pending, visited = [(root, 0)], 0
    while pending:
        value, depth = pending.pop()
        visited += 1
        if depth > 32 or visited > 4096:
            return {"paths": [], "complete": False}
        children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
        pending.extend((child, depth + 1) for child in children)
    found, complete = [], True

    def offer(path, value):
        nonlocal complete
        rendered = encode(value)
        if not value or len(rendered) > 1200:
            return False
        try:
            rendered.encode("utf-8", errors="strict")
            path.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            complete = False
            return True  # Omit the advisory path, never alter source text.
        if len(path) > 256 or len(found) >= 24:
            complete = False
        else:
            found.append(path)
        return True

    if records_only:
        if isinstance(root, dict):
            offer("", root)
            rows = ((f"/{key.replace('~', '~0').replace('/', '~1')}/{i}", value)
                    for key, items in root.items() if isinstance(items, list)
                    for i, value in enumerate(items) if isinstance(value, dict))
        else:
            rows = ((f"/{i}", value) for i, value in enumerate(root)
                    if isinstance(value, dict)) if isinstance(root, list) else ()
        for path, value in rows:
            offer(path, value)
    else:
        pending = [(root, "", 0)]
        visited = 0
        while pending:
            value, path, depth = pending.pop()
            visited += 1
            if depth > 16 or visited > 4096:
                complete = False
                break
            # Do not offer a whole JSON document, or an anonymous array/scalar.
            if path and isinstance(value, dict) and offer(path, value):
                continue
            children = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else ()
            for key, child in reversed(list(children)):
                pointer = path + "/" + str(key).replace("~", "~0").replace("/", "~1")
                if isinstance(child, (dict, list)):
                    pending.append((child, pointer, depth + 1))
                elif isinstance(value, dict):
                    offer(pointer, {key: child})
    return {"paths": found, "complete": complete}


def _encode_fact_view(view, limit):
    """Hints must never displace original evidence or make a valid view overflow."""
    encoded = _canonical(view)
    try:
        if len(encoded.encode("utf-8")) <= limit:
            return encoded
    except UnicodeEncodeError:
        pass  # Only remove optional hints; original invalid text still fails below.
    for row in view["context"]["sources"]:
        row.pop("fact_paths", None)
    # Missing advisory metadata is not a statement about source completeness.
    return _canonical(view)

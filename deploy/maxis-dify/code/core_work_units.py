"""Lossless Work generation transport; no defaults, meaning repair or authority."""
_GENERATION_SCHEMA = None


def _expand_work_units(candidate):
    def invalid():
        raise _NativeDraftIssue('schema')

    wire = _native_candidate(candidate, _GENERATION_SCHEMA, _MAX_DRAFT_UTF8_BYTES)
    units, used = {}, set()
    for unit in wire['units']:
        if unit['id'] in units:
            invalid()
        units[unit['id']] = unit['content']

    def expand(value, schema):
        if 'anyOf' in schema:
            if value not in units:
                invalid()
            used.add(value)
            return units[value]
        if schema.get('type') == 'array':
            return [expand(item, schema['items']) for item in value]
        if schema.get('type') != 'object':
            return value
        result = {}
        for key, child in schema['properties'].items():
            field = value[key]
            if key not in schema.get('required', []):
                if not field:
                    continue
                field = field[0]
            result[key] = expand(field, child)
        return result

    payload = wire['payload']
    if payload:
        payload = expand(payload, _DRAFT_SCHEMA['properties']['payload']['anyOf'][1])
    if used != set(units):
        invalid()  # Never silently discard unbound model statements.
    draft = {'contract': 'work-draft.v3', 'status': wire['status'], 'payload': payload}
    _validate(draft, _DRAFT_SCHEMA, _DRAFT_SCHEMA, invalid)
    return _canonical(draft)

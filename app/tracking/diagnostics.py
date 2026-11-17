"""Aggregate collection evidence from existing sources; never fetch extra Jira data."""
from unicodedata import normalize


def _fold(value):
    return normalize('NFKC', str(value or '')).strip().casefold()


def issue_counts(rows, voc_component):
    rows = {row['key']: row for row in rows if row.get('key')}.values()
    total, voc, unknown = 0, 0, False
    wanted = _fold(voc_component)
    for row in rows:
        total += 1
        components = (row.get('fields') or {}).get('components')
        if not isinstance(components, list):
            unknown = True
            continue
        voc += bool(wanted) and any(_fold(c.get('name') if isinstance(c, dict) else c) == wanted
                                   for c in components)
    return {'total': total, 'voc': None if unknown else voc}


def collection_funnel(checkpoint, analyzed, active, voc_component, queries_complete):
    recorded = checkpoint.get('collectionStages') or {}
    stages = []
    for name in ('discovery', 'module', 'hydrated'):
        stages.append({'id': name, 'total': None, 'voc': None, 'complete': False,
                       **recorded.get(name, {})})
    ready = 'hydrated' in recorded
    complete = bool(queries_complete and ready and len(analyzed) == len(checkpoint.get('candidateKeys', [])))
    sources = checkpoint.get('issues') or {}
    for name, keys in (('analyzed', analyzed.keys()), ('published', [issue['key'] for issue in active])):
        counts = issue_counts([sources.get(key, {'key': key}) for key in keys], voc_component) if ready or analyzed else {
            'total': None, 'voc': None}
        stages.append({'id': name, **counts, 'complete': complete})
    return {'version': 1, 'vocComponent': voc_component, 'stages': stages}

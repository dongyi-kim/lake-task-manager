"""Parent-scoped navigation cache. Snapshots read SQLite, never Confluence."""
from urllib.parse import parse_qs, quote, urlsplit

from app.auth.base import PermissionDenied, UpstreamUnavailable
from app.jira import confluence_service as reads


def prefix(client, space):
    return f"confbranch:{reads.scope_key(client)}:{quote(space, safe='')}:"


def key(client, space, parent, start):
    return prefix(client, space) + f"{parent or 'root'}:{start}"


def dirty_prefix(client, space):
    return prefix(client, space).replace('confbranch:', 'confbranchdirty:', 1)


def next_start(data, start):
    link = (data.get('_links') or {}).get('next')
    if not link:
        return None
    try:
        values = parse_qs(urlsplit(link).query).get('start', [])
        offset = int(values[0]) if len(values) == 1 else -1
        if offset <= start:
            raise ValueError()
        return offset
    except (ValueError, TypeError):
        raise UpstreamUnavailable('Confluence 문서 목록의 다음 위치를 확인하지 못했습니다.') from None


def project(row):
    child = (row.get('children') or {}).get('page')
    count = child.get('size') if isinstance(child, dict) else None
    return {'id': str(row['id']), 'title': row['title'], 'space': {'key': row['space']['key']},
            'ancestors': [{'id': str(a['id']), 'title': a.get('title', '')} for a in row['ancestors']],
            'hasChildren': bool(count or child.get('_links', {}).get('next')) if count is not None else None}


def branch(client, space, parent='', start=0):
    if parent and not str(parent).isdigit():
        raise ValueError('숫자 문서 ID가 필요합니다.')
    reads._pagination(start, 100)
    cache_key = key(client, space, parent, start)
    path = (f'/rest/api/content/{parent}/child/page' if parent
            else f'/rest/api/space/{quote(space, safe="")}/content/page')
    params = {'start': start, 'limit': 100, 'expand': 'space,version,ancestors,children.page'}
    if not parent:
        params['depth'] = 'root'
    def validate(data):
        next_start(data, start)
        return isinstance(data.get('results'), list) and all(
            isinstance(row, dict) and str(row.get('id', '')).isdigit()
            and isinstance(row.get('title'), str) and isinstance(row.get('ancestors'), list)
            and all(isinstance(a, dict) and str(a.get('id', '')).isdigit() for a in row['ancestors'])
            and (row.get('space') or {}).get('key') == space for row in data['results'])
    fence = client.cache.fence()
    try:
        if parent:
            # Navigation rows prime these heads, so traversing a known parent adds no lookup.
            current = reads.head(client, parent)
            if current['space']['key'] != space:
                raise PermissionDenied('설정된 Confluence Space 범위 밖입니다.')
        data = reads.cached_read(client, 'confbranch', path, params, validate, key=cache_key)
    except Exception as exc:
        if reads._denied(exc):
            # Cached ancestors can still reference a now-restricted subtree. Never
            # resurrect its titles on the next local snapshot after a definitive denial.
            client.cache.forget_prefixes([prefix(client, space)])
        raise
    source = client.cache.metadata(cache_key) or {}
    if source.get('state') == 'fresh':
        heads = []
        for row in data['results']:
            if not isinstance((row.get('version') or {}).get('number'), int):
                continue
            head_key = f'confhead:{reads.scope_key(client)}:{row["id"]}:current'
            old = client.cache.get_stale(head_key)
            if old is None or old.get('version', {}).get('number', 0) < row['version']['number']:
                heads.append((head_key, row))
        if heads:
            client.cache.set_many_if_fence(heads, 600, fence, fetched_at=source['fetchedAt'])
    return {'parent': parent, 'start': start, 'results': [project(row) for row in data['results']],
            'nextStart': next_start(data, start), 'cacheInfo': source}


def snapshot(client, space):
    queue, seen, batches, jobs = [('', 0, 0)], set(), [], []
    for parent in client.cache.entries_by_prefix(dirty_prefix(client, space)).values():
        # A previously leaf page may have acquired its first child locally. Keep
        # visiting its cached branch until root metadata catches up; a fresh branch
        # generates no upstream job. The hint never extends the source data's age.
        queue.append((parent, 0, 1))
    for parent, start, depth in queue:
        if (parent, start) in seen:
            continue
        seen.add((parent, start))
        cache_key = key(client, space, parent, start)
        alive = client.cache.get_alive(cache_key)
        if not alive:
            jobs.append({'parent': parent, 'start': start, 'depth': depth})
            continue
        data, _ = alive
        source = client.cache.metadata(cache_key) or {}
        rows = [project(row) for row in data['results']]
        following = next_start(data, start)
        batches.append({'parent': parent, 'start': start, 'results': rows,
                        'nextStart': following, 'cacheInfo': source})
        if source.get('state') != 'fresh':
            # Refresh each stale parent from offset zero; retain its old complete children meanwhile.
            if start == 0:
                jobs.append({'parent': parent, 'start': 0, 'depth': depth, 'refresh': True})
        if following is not None:
            queue.append((parent, following, depth))
        queue.extend((row['id'], 0, depth + 1) for row in rows if row['hasChildren'] is not False)
    return {'batches': batches, 'jobs': jobs}


def seed_complete_index(client, space):
    """A completed bulk pass also warms parent caches. No extra upstream reads."""
    import hashlib
    import json
    fence, offset, seen, rows, sources = client.cache.fence(), 0, set(), {}, []
    while offset is not None:
        if offset in seen:
            return
        seen.add(offset)
        path = f'/rest/api/space/{quote(space, safe="")}/content/page'
        params = {'start': offset, 'limit': 100, 'depth': 'all', 'expand': 'space,ancestors'}
        signature = json.dumps([path, params], sort_keys=True, ensure_ascii=False)
        cache_key = f'conftree:{reads.scope_key(client)}:' + hashlib.sha256(signature.encode()).hexdigest()
        data = client.cache.get(cache_key)
        if data is None:
            return  # Never promote an incomplete/stale pass to a complete hierarchy.
        sources.append(client.cache.metadata(cache_key)['fetchedAt'])
        rows.update((str(row['id']), row) for row in data['results'])
        offset = next_start(data, offset)
    groups = {'': []}
    for cid in rows:
        groups[cid] = []
    for row in rows.values():
        parent = str(row['ancestors'][-1]['id']) if row['ancestors'] else ''
        groups.setdefault(parent, []).append(row)
    values = []
    for parent, children in groups.items():
        for start in range(0, max(1, len(children)), 100):
            page = [{**row, 'children': {'page': {'size': len(groups.get(str(row['id']), []))}}}
                    for row in children[start:start + 100]]
            link = {'next': f'?start={start + 100}'} if start + 100 < len(children) else {}
            values.append((key(client, space, parent, start), {'results': page, '_links': link}))
    if values:
        client.cache.set_many_if_fence(values, 600, fence, fetched_at=min(sources))


def invalidate_changed_branches(client, kind, payload, result, affected_ids):
    """Keep unrelated branches warm. Body/comment/file edits do not change the tree."""
    if kind in {'restore_page', 'set_restrictions'}:
        # Permission changes and restores may arrive without a readable previous parent.
        client.cache.forget_prefixes([f'confbranch:{reads.scope_key(client)}:'])
        return
    structural = kind in {'create_page', 'move_page', 'trash_page'}
    if not structural and not (kind == 'update_page' and payload.get('title') is not None):
        return
    scope, targets = reads.scope_key(client), set()
    changed = str(result.get('id') or payload.get('pageId') or payload.get('page_id') or '')
    pages = []
    for cid in set(str(cid) for cid in affected_ids if cid):
        old = client.cache.get_stale(f'confhead:{scope}:{cid}:current')
        if old and (old.get('space') or {}).get('key'):
            pages.append(old)
            ancestors = old.get('ancestors') or []
            if str(old['id']) == changed:
                targets.add((old['space']['key'], str(ancestors[-1]['id']) if ancestors else ''))
    # A bulk metadata pass need not have fetched a full head. Also covers recovered
    # writes whose receipt has no previousParentId, without another Jira/Confluence call.
    for batch in client.cache.entries_by_prefix(f'confbranch:{scope}:').values():
        for row in batch.get('results', []):
            if str(row['id']) == changed:
                pages.append(row)
                ancestors = row.get('ancestors') or []
                targets.add((row['space']['key'], str(ancestors[-1]['id']) if ancestors else ''))
    spaces = {row['space']['key'] for row in pages}
    if isinstance(payload.get('space'), dict):
        spaces.add(payload['space'].get('key'))
    elif isinstance(payload.get('space'), str):
        spaces.add(payload['space'])
    for space in filter(None, spaces):
        if kind == 'create_page':
            targets.add((space, str(result.get('parentId') or '')))
        if kind == 'move_page':
            targets.add((space, str(result.get('parentId') or payload.get('parent_id') or '')))
            if 'previousParentId' in result:
                targets.add((space, str(result['previousParentId'] or '')))
        if kind in {'trash_page', 'restore_page'}:
            targets.add((space, str(result.get('id') or payload.get('page_id') or '')))
    for space, parent in targets:
        branch_prefix = prefix(client, space) + (parent or 'root') + ':'
        if kind == 'trash_page' and parent == str(result.get('id')):
            client.cache.forget_prefixes([branch_prefix])
        else:
            client.cache.invalidate(branch_prefix)
            client.cache.set(dirty_prefix(client, space) + (parent or 'root'), parent, 86400)

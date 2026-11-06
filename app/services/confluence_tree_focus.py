"""Read saved/recent priority paths and subtrees without walking every ancestor/parent."""
import json

from app.jira import confluence_service as reads
from app.services.confluence_tree import next_start, project


def favorite_pages(client, space, page_ids, start=0):
    ids = sorted(set(str(page_ids).split(',')))
    if not 1 <= len(ids) <= 20 or any(not cid.isdigit() for cid in ids):
        raise ValueError('한 묶음에 숫자 문서 ID 1~20개가 필요합니다.')
    reads._pagination(start, 100)
    # DC documents descendant/{type} for comments only. CQL ancestor explicitly
    # supports all page descendants; do not assume Cloud's descendant/page API.
    members = ','.join(ids)
    params = {'cql': f'type = page AND space = {json.dumps(space, ensure_ascii=False)} AND (id IN ({members}) OR ancestor IN ({members})) ORDER BY title ASC',
              'start': start, 'limit': 100, 'expand': 'space,ancestors'}

    def validate(data):
        next_start(data, start)
        return isinstance(data.get('results'), list) and all(
            isinstance(row, dict) and str(row.get('id', '')).isdigit()
            and isinstance(row.get('title'), str) and (row.get('space') or {}).get('key') == space
            and isinstance(row.get('ancestors'), list)
            and all(isinstance(a, dict) and str(a.get('id', '')).isdigit() for a in row['ancestors'])
            and (str(row['id']) in ids or any(str(a['id']) in ids for a in row['ancestors'])) for row in data['results'])

    data = reads.cached_read(client, 'conftree', '/rest/api/content/search', params, validate)
    # Search indexes can lag structural writes. These are priority display rows,
    # never proof of an empty/complete child list and never seeded into confbranch.
    return {'roots': ids, 'start': start, 'results': [project(row) for row in data['results']],
            'nextStart': next_start(data, start)}

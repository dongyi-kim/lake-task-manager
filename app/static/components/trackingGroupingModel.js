// Pure two-level projection: current Jira status is independent of period outcomes.
const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const statusRank = category => category === 'indeterminate' ? 0
  : category === 'new' || category === 'todo' ? 1 : category === 'done' ? 2 : 3;

function statuses(items) {
  const groups = new Map();
  for (const item of items) {
    const statusCategory = String(item.issue?.statusCategory || '');
    const label = item.issue?.status == null ? '' : String(item.issue.status);
    const key = 'tracking-status:' + JSON.stringify([statusCategory, label]);
    if (!groups.has(key)) groups.set(key, {key, title:label || '상태 미확인',
      kind:'status', statusCategory, items:[]});
    groups.get(key).items.push(item);
  }
  return [...groups.values()].sort((a, b) => statusRank(a.statusCategory)-statusRank(b.statusCategory)
    || compare(a.title, b.title) || compare(a.key, b.key));
}

function hierarchies(items, groups) {
  if (groups == null) {
    const fallback = new Map();
    for (const item of items) {
      const voc = !!item.issue?.voc, title = voc ? item.issue?.vocCategory || '분류 미지정' : '일반 업무';
      const key = 'tracking-hierarchy:' + JSON.stringify([voc, title]);
      if (!fallback.has(key)) fallback.set(key, {key, title, kind:'hierarchy', voc, items:[]});
      fallback.get(key).items.push(item);
    }
    return [...fallback.values()].sort((a, b) => Number(a.voc)-Number(b.voc));
  }
  const assigned = new Set();
  const result = groups.map(group => {
    const keys = new Set(group.keys || []);
    const members = items.filter(item => keys.has(item.key) && !assigned.has(item.key));
    members.forEach(item => assigned.add(item.key));
    return {...group, kind:'hierarchy', items:members};
  }).filter(group => group.items.length);
  const unlinked = items.filter(item => !assigned.has(item.key));
  if (unlinked.length) result.push({key:'tracking-grouping:unlinked', title:'연결되지 않은 업무',
    kind:'hierarchy', pathLabel:'연결되지 않은 업무', scopeId:'', voc:false, items:unlinked});
  return result;
}

// Supplied item order (filters/pins already applied by the caller) survives every group.
// A shared root belongs to the first supplied hierarchy, in either grouping direction.
export function groupTrackingReport(items, groups, mode = 'hierarchy') {
  const seen = new Set();
  const unique = (items || []).filter(item => {
    if (seen.has(item.key)) return false;
    seen.add(item.key);
    return true;
  });
  const hierarchy = hierarchies(unique, groups);
  const nested = (parent, child) => ({...child,
    key:'tracking-grouping:child:' + JSON.stringify([parent.key, child.key])});
  if (mode === 'status') {
    return statuses(unique).map(status => {
      const members = new Set(status.items);
      return {...status, children:hierarchy.map(group => nested(status, {
        ...group, items:group.items.filter(item => members.has(item)),
      })).filter(group => group.items.length)};
    });
  }
  return hierarchy.map(group => ({...group,
    children:statuses(group.items).map(status => nested(group, status))}));
}

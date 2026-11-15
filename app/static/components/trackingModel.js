// Display-only operations over an immutable, saved Tracking run. No Jira requests.
const DAY = 86400000;
export function kstToday(now = new Date()) {
  return new Date(now.getTime() + 9 * 3600000).toISOString().slice(0, 10);
}
function shift(date, days) { return new Date(Date.parse(date + 'T00:00:00Z') + days * DAY).toISOString().slice(0, 10); }
export function dateRange(preset = 'week', now = new Date()) {
  const today = kstToday(now);
  if (preset === 'today') return { startDate: today, endDate: today };
  if (preset === 'lastWeek') {
    const weekday = (new Date(today + 'T00:00:00Z').getUTCDay() + 6) % 7;
    const endDate = shift(today, -weekday - 1);
    return { startDate: shift(endDate, -6), endDate };
  }
  return { startDate: shift(today, preset === 'month' ? -29 : -6), endDate: today };
}
export function dateError(start, end, now = new Date()) {
  const valid = value => /^\d{4}-\d{2}-\d{2}$/.test(value || '') && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0,10) === value;
  if (!valid(start) || !valid(end)) return '시작일과 종료일을 확인하세요.';
  if (start > end) return '종료일은 시작일 이후여야 합니다.';
  if (end > kstToday(now)) return '종료일은 오늘까지 선택할 수 있습니다.';
  return '';
}
export const normalize = value => String(value ?? '').normalize('NFKC').toLocaleLowerCase().replace(/\s+/g, '');
export const issueKeyOf = node => node.issueKey || (/^[A-Z][A-Z0-9_]*-\d+$/i.test(node.key || '') ? node.key : '');
export function descendantKeys(node) {
  const keys = new Set();
  const walk = n => { const key = issueKeyOf(n); if (key) keys.add(key); for (const child of n.children || []) walk(child); };
  walk(node); return keys;
}
function indexes(run) {
  const issues = new Map((run?.issues || []).map(issue => [issue.key, issue]));
  const events = new Map();
  for (const event of run?.events || []) {
    if (!events.has(event.issueKey)) events.set(event.issueKey, []);
    events.get(event.issueKey).push(event);
  }
  return { issues, events };
}
function countsFor(keys, index) {
  const counts = {created:0,completed:0,reopened:0,comments:0,commentEdits:0,fieldChanges:0,uniqueIssues:0,historyComplete:true,commentsComplete:true};
  const unique = {created:new Set(),completed:new Set(),reopened:new Set()};
  const seen = new Set();
  for (const key of keys) {
    const issue = index.issues.get(key);
    // Ancestors may have no collected issue source; they are context, not observed zeroes.
    if (!issue || issue.contextOnly === true) continue;
    counts.uniqueIssues++;
    if (issue.coverage?.historyComplete !== true) counts.historyComplete = false;
    if (issue.coverage?.commentsComplete !== true) counts.commentsComplete = false;
    for (const e of index.events.get(key) || []) {
      const id = e.id || JSON.stringify(e);
      if (seen.has(id)) continue;
      seen.add(id);
      if (unique[e.kind]) unique[e.kind].add(key);
      else if (e.kind === 'comment') counts.comments++;
      else if (e.kind === 'commentEdited') counts.commentEdits++;
      else if (e.kind === 'fieldChanged') counts.fieldChanges++;
    }
  }
  for (const key of Object.keys(unique)) counts[key] = unique[key].size;
  if (!counts.uniqueIssues) counts.historyComplete = counts.commentsComplete = false;
  return counts;
}
export function nodeCounts(node, run) { return countsFor(descendantKeys(node), indexes(run)); }
export function countLabel(value, complete) {
  if (value == null || (!value && !complete)) return '미확인';
  if (!value) return '–';
  return String(value) + (complete ? '' : '+');
}
export function trackingRows(run, {query = '', highlight = null, matchedOnly = false, closed = {}} = {}) {
  const index = indexes(run), q = normalize(query), out = [];
  const active = !!q || !!highlight?.value;
  function prepare(node, path = []) {
    const key = issueKeyOf(node), issue = index.issues.get(key), events = index.events.get(key) || [];
    const context = path.map(p => p.name || p.summary || p.key || '').join(' ');
    const title = node.name || node.summary || issue?.summary || key;
    const text = [key,title,context,issue?.assigneeName].join(' ');
    const searchMatch = !q || normalize(text).includes(q);
    let highlightMatch = true;
    if (highlight?.value) {
      if (highlight.kind === 'person') highlightMatch = issue?.assigneeId === highlight.value || events.some(e => e.actorId === highlight.value);
      else if (highlight.kind === 'epic') highlightMatch = key === highlight.value || path.some(p => issueKeyOf(p) === highlight.value);
      else highlightMatch = key === highlight.value || path.some(p => issueKeyOf(p) === highlight.value);
    }
    const children = (node.children || []).map(n => prepare(n, [...path,node]));
    const matched = active && searchMatch && highlightMatch;
    const keys = new Set(key ? [key] : []);
    for (const child of children) for (const k of child.keys) keys.add(k);
    const kind = String(node.kind || node.type || 'task').toLowerCase().replace(/[-_ ]/g,'');
    const contextOnly = issue ? issue.contextOnly === true : node.contextOnly === true;
    return { ...node, kind, key, title, issue, path, children, keys, matched, contextOnly, highlighted:!!highlight?.value && highlightMatch,
      containsMatch:matched || children.some(c => c.containsMatch), counts:countsFor(keys,index) };
  }
  function flatten(node, depth = 0) {
    if (matchedOnly && active && !node.containsMatch) return;
    out.push({...node,depth});
    // Filtering reveals retained ancestor paths without destroying the user's fold state.
    if (closed[node.id] && !(matchedOnly && active)) return;
    for (const child of node.children) flatten(child, depth + 1);
  }
  for (const node of run?.tree || []) flatten(prepare(node));
  return out;
}
export function groupEvents(events) {
  const groups = new Map();
  for (const event of [...events].sort((a,b) => String(b.date).localeCompare(String(a.date)))) {
    const parsed = new Date(event.date);
    const date = Number.isFinite(parsed.getTime()) ? kstToday(parsed) : '날짜 미확인';
    if (!groups.has(date)) groups.set(date, []);
    groups.get(date).push(event);
  }
  return [...groups].map(([date, events]) => ({date,events}));
}
export const EVENT_LABELS = {created:'생성',completed:'완료',reopened:'재오픈',comment:'댓글',commentEdited:'댓글 수정',fieldChanged:'변경'};
export const RUN_LABELS = {queued:'대기 중',running:'수집 중',complete:'수집 완료',partial:'일부 확인',paused:'일시 중단',cancelled:'수집 취소'};
export function eventText(event) {
  if (event.kind === 'comment' || event.kind === 'commentEdited') return String(event.body || event.excerpt || '본문 미확인');
  const field = ({status:'상태',assignee:'담당자',duedate:'마감일',summary:'제목',description:'설명',priority:'우선순위',labels:'라벨',resolution:'해결'})[event.field] || event.field || '';
  if (event.from != null || event.to != null) return [field, String(event.from ?? '미지정'), '→', String(event.to ?? '미지정')].filter(Boolean).join(' ');
  return EVENT_LABELS[event.kind] || event.kind || '활동';
}

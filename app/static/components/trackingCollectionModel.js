// Diagnostics over the loaded result only. Frozen collection counts and local
// projections have different meanings and must never substitute for one another.
import { trackingRows } from './trackingModel.js';

const STAGES = [
  ['discovery','검색 결과'], ['module','모듈 판정 후'], ['hydrated','원본 수집'],
  ['analyzed','분석'], ['published','결과 포함'],
];
const count = value => Number.isInteger(value) && value >= 0 ? value : null;
const activeIssues = run => new Map((run?.issues || []).filter(issue => issue.key && issue.contextOnly !== true).map(issue => [issue.key,issue]));
const genuineVoc = issue => issue?.voc === true && issue.vocInherited !== true;
const population = issues => ({total:issues.size, voc:[...issues.values()].filter(genuineVoc).length});

export function buildTrackingCollectionAudit(run, {displayRun = run, scopedRun = displayRun,
  vocComponent = '사용자 VoC', query = '', highlight = null, matchedOnly = false} = {}) {
  const funnel = run?.coverage?.funnel;
  const recorded = funnel?.version === 1 && Array.isArray(funnel.stages);
  const stages = STAGES.map(([id,label]) => {
    const saved = recorded ? funnel.stages.find(stage => stage.id === id) : null;
    return {id, label, total:count(saved?.total), voc:count(saved?.voc),
      state:!recorded ? '미기록' : saved?.complete === true ? '처리 완료' : '처리 미완료'};
  });
  const issues = activeIssues(displayRun);
  const filtering = matchedOnly && (!!String(query).trim() || !!highlight?.value);
  // Leave fold state out: disclosure affects layout, not the ticket population.
  // Retained ancestor paths supply context, not extra search matches.
  const keys = new Set(trackingRows(displayRun,{query,highlight,matchedOnly})
    .filter(row => issues.has(row.key) && (!filtering || row.matched)).map(row => row.key));
  const navigation = new Map([...issues].filter(([key]) => keys.has(key)));
  const scopedIssues = activeIssues(scopedRun);
  const events = (scopedRun?.events || []).filter(event => keys.has(event.issueKey) && scopedIssues.has(event.issueKey));
  const frozenVocComponent = recorded ? funnel.vocComponent || '' : '';
  return {recorded, stages, frozenVocComponent, currentVocComponent:vocComponent,
    vocComponentChanged:!!frozenVocComponent && frozenVocComponent !== vocComponent,
    display:population(issues), navigation:population(navigation),
    activity:{issues:new Set(events.map(event => event.issueKey)).size,events:events.length}};
}

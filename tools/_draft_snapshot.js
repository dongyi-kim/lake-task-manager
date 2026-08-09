// 승인 카드의 **티켓 본문 전문**을 서버 스냅샷에서 읽는다.
// 답변 요약만 보면 본문 품질(DoD·참고·구조)을 검수할 수 없다 — 실제로 만들어질 것을 본다.
// agent_ui_probe.py 가 page.evaluate 로 실행한다.
async () => {
  const cv = JSON.parse(localStorage.getItem('agentConvos') || '[]');
  if (!cv.length) return '';
  const r = await fetch('/api/agent/snapshot/' + encodeURIComponent(cv[0].id));
  if (!r.ok) return '';
  const s = await r.json();
  // 스냅샷은 화면용 모양이다 — 승인 대기는 pending, 작성 중이면 draft_items.
  const d = (s && (s.pending || {})) || {};
  const items = d.items || (s && s.draft_items) || [];
  const kidsAll = d.children || [];
  const NL = String.fromCharCode(10);
  return items.map((it, i) => {
    const kids = (it.children || [])
      .concat(kidsAll.filter((c) => (c.parent_index || 0) === i))
      .map((c) => '    - ' + (c.summary || '') + (c.assignee ? ' (' + c.assignee + ')' : ''));
    const body = String(it.description || '(없음)')
      .replace(/<\/(p|div|h[1-6]|li|ul|ol)>/gi, ' | ')
      .replace(/<[^>]+>/g, ' ')
      .replace(/\s+/g, ' ')
      .slice(0, 1200);
    return '[항목 ' + i + '] ' + (it.summary || '') + NL
      + '  타입=' + (it.type || '') + ' 모듈=' + (it.components || []).join(',')
      + ' Epic=' + (it.epic || '') + ' 마감=' + (it.duedate || '')
      + ' 우선=' + (it.priority || '') + ' 라벨=' + (it.labels || []).join(',') + NL
      + '  본문: ' + body
      + (kids.length ? NL + '  자식:' + NL + kids.join(NL) : '');
  }).join(NL);
}

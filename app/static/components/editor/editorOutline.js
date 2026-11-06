// Read ProseMirror positions; never assign heading IDs directly into editable DOM.
const headingText = node => node.textBetween(0, node.content.size, '', leaf => {
  const attrs = leaf.attrs;
  if (leaf.type.name === 'hardBreak') return ' ';
  if (leaf.type.name === 'confluenceInline') return attrs.macro === 'anchor' ? '' : attrs.config?.label || attrs.config?.params?.title || '';
  return attrs.label || attrs.title || attrs.name || attrs.alt || attrs.href || '';
}).trim() || '제목 없음';

export function editorOutline(editor, changed) {
  let rows = [], frame = 0, mode = 'selection', disposed = false, jumpScroll = null;
  const scrollPosition = () => {
    const positions = [];
    for (let el = editor.view.dom.parentElement; el; el = el.parentElement) positions.push(el.scrollTop, el.scrollLeft);
    return positions.join(',');
  };
  const publish = () => {
    frame = 0;
    if (disposed || editor.isDestroyed) return;
    rows = [];
    editor.state.doc.descendants((node, pos) => {
      if (node.type.name === 'heading') rows.push({id: String(pos), pos, level: node.attrs.level,
        title: (node.attrs.outlineNumber ? node.attrs.outlineNumber + ' ' : '') + headingText(node), node: editor.view.nodeDOM(pos)});
    });
    let active = rows[0]?.id || '';
    if (mode === 'scroll') {
      const host = editor.view.dom.closest('.cmt-ed-host');
      const outer = editor.view.dom.closest('.conf-compose-scroll');
      const top = Math.max(host?.getBoundingClientRect().top || 0, outer?.getBoundingClientRect().top || 0) + 40;
      for (const row of rows) { if (row.node?.getBoundingClientRect().top > top) break; active = row.id; }
    } else for (const row of rows) { if (row.pos > editor.state.selection.from) break; active = row.id; }
    changed({headings: rows.map(({id,title,level}) => ({id,title,level})), active});
  };
  const schedule = source => { mode = source; if (!frame) frame = requestAnimationFrame(publish); };
  const transaction = ({transaction: tr}) => { if (tr.docChanged || tr.selectionSet || tr.getMeta('headingNumbering')) { jumpScroll = null; schedule('selection'); } };
  const scroll = event => {
    if (!event.target.contains?.(editor.view.dom)) return;
    // scrollIntoView can stop at the document bottom, before the clicked heading reaches
    // the top. Keep that selection until the user actually moves a scroll container.
    if (jumpScroll === scrollPosition()) return;
    jumpScroll = null; schedule('scroll');
  };
  editor.on('transaction', transaction);
  document.addEventListener('scroll', scroll, true);
  schedule('selection');
  return {
    jump(id) {
      const row = rows.find(item => item.id === id); if (!row) return;
      editor.commands.setTextSelection(row.pos + 1);
      editor.view.focus();
      row.node?.scrollIntoView({block:'start',behavior:'auto'});
      jumpScroll = scrollPosition();
      schedule('selection');
    },
    destroy() { disposed = true; cancelAnimationFrame(frame); editor.off('transaction',transaction); document.removeEventListener('scroll',scroll,true); },
  };
}

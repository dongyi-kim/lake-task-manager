// Numbers are presentation attributes, not editable text. Publishing materializes them once.
export function outlineNumbers(levels) {
  const stack = [];
  let roots = 0;
  return levels.map(level => {
    let removed = null;
    while (stack.length && stack.at(-1).level > level) removed = stack.pop();
    if (stack.length && stack.at(-1).level === level) stack.at(-1).count++;
    else stack.push({ level, count: stack.length ? (removed ? removed.count + 1 : 1) : ++roots });
    if (stack.length === 1) roots = stack[0].count;
    return stack.map(item => item.count).join(".");
  });
}

export function headingNumbering(T, host) {
  return T.Extension.create({
    name: "headingNumbering",
    addGlobalAttributes() { return [{ types: ["heading"], attributes: {
      outlineNumber: { default: null, parseHTML: () => null,
        renderHTML: attrs => attrs.outlineNumber ? { "data-heading-number": attrs.outlineNumber } : {} },
    } }]; },
    addProseMirrorPlugins() { return [new T.Plugin({
      appendTransaction(transactions, _old, state) {
        if (!transactions.some(tr => tr.docChanged || tr.getMeta("headingNumbering"))) return null;
        const headings = [];
        state.doc.descendants((node, pos) => { if (node.type.name === "heading") headings.push({ node, pos }); });
        const numbers = outlineNumbers(headings.map(item => item.node.attrs.level));
        const tr = state.tr;
        headings.forEach(({ node, pos }, index) => {
          const number = host.autoNumberHeadings ? numbers[index] : null;
          if (node.attrs.outlineNumber !== number) tr.setNodeMarkup(pos, undefined, { ...node.attrs, outlineNumber: number });
        });
        return tr.docChanged ? tr.setMeta("addToHistory", false) : null;
      },
    })]; },
  });
}

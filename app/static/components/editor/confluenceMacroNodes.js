import { macroDefinition, macroNodeType } from "../../lib/confluenceMacros.js";
export function confluenceMacroNodes(T, host) {
  const anchors = T.Extension.create({name: "confluenceAnchors", addGlobalAttributes() {
    return [{types: ["heading", "paragraph", "blockquote", "table", "tableCell", "tableHeader"], attributes: {
      id: {default: null, parseHTML: el => el.getAttribute("id"), renderHTML: attrs => attrs.id ? {id: attrs.id} : {}},
    }}];
  }});
  return [anchors, ...["confluenceBlock", "confluencePlain", "confluenceAtom", "confluenceInline"].map(name => {
    const inline = name === "confluenceInline", rich = name === "confluenceBlock", plain = name === "confluencePlain";
    return T.Node.create({ name, group: inline ? "inline" : "block", inline, atom: !rich && !plain,
      content: rich ? "block+" : plain ? "text*" : undefined, code: plain, whitespace: plain ? "pre" : "normal", defining: true,
      addAttributes() { return { macro: { default: "", parseHTML: el => el.dataset.confMacro, renderHTML: attrs => ({ "data-conf-macro": attrs.macro }) },
        config: { default: {}, parseHTML: el => { try { return JSON.parse(el.dataset.confConfig || "{}"); } catch (_) { return {}; } },
          renderHTML: attrs => ({ "data-conf-config": JSON.stringify(attrs.config) }) } }; },
      parseHTML() { return [{ tag: (inline ? "span" : plain ? "pre" : "div") + "[data-conf-macro]",
        getAttrs: el => macroNodeType(el.dataset.confMacro) === name ? null : false,
        ...(rich ? { contentElement: ".conf-macro-content" } : {}) }]; },
      renderHTML({ node, HTMLAttributes }) {
        if (rich) return ["div", HTMLAttributes, ["div", { class: "conf-macro-content" }, 0]];
        if (plain) return ["pre", HTMLAttributes, 0];
        return [inline ? "span" : "div", HTMLAttributes, macroDefinition(node.attrs.macro)?.label || "지원하지 않는 매크로"];
      },
      addNodeView() { return ({ node, getPos }) => {
        const dom = document.createElement(inline ? "span" : "div");
        dom.className = "conf-editor-macro" + (inline ? " conf-editor-macro-inline" : "");
        const button = document.createElement("button"); button.type = "button"; button.contentEditable = "false";
        button.className = "conf-macro-settings";
        let current = node, preview;
        const paint = () => { dom.dataset.confMacro = current.attrs.macro;
          const definition = macroDefinition(current.attrs.macro), params = current.attrs.config?.params || {};
          button.textContent = (definition?.label || current.attrs.config?.label || "미지원 매크로 · 원본 보존") + (params.title ? " · " + params.title : "") + " ⚙";
          button.setAttribute("aria-label", (definition?.label || "미지원 매크로") + " 설정"); };
        button.addEventListener("mousedown", event => event.preventDefault());
        button.addEventListener("click", () => { if (host.readOnly || host.busy) return;
          const pos = getPos(); if (typeof pos === "number") host.openConfluenceMacro(current.attrs.macro, pos); });
        dom.append(button); let contentDOM;
        if (node.attrs.macro === 'resource:image' && /^\/api\/confluence\/pages\/\d+\/image\?/.test(node.attrs.config?.preview || '')) {
          preview = document.createElement('img'); preview.src = node.attrs.config.preview; preview.alt = node.attrs.config.label || ''; preview.contentEditable = 'false'; dom.append(preview);
        }
        if (rich || plain) { contentDOM = document.createElement(plain ? "pre" : "div"); contentDOM.className = "conf-macro-content"; dom.append(contentDOM); }
        paint();
        return { dom, contentDOM, update(next) { if (next.type !== current.type) return false; current = next; paint(); return true; },
          stopEvent: event => button.contains(event.target),
          ignoreMutation: mutation => mutation.type !== "selection" && (!contentDOM || !contentDOM.contains(mutation.target)) };
      }; },
    });
  })];
}

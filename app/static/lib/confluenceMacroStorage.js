import { macroDefinition, macroNodeType } from "./confluenceMacros.js";
const AC = "http://atlassian.com/content", RI = "http://atlassian.com/resource/identifier";
const esc = value => String(value ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
const xml = node => new XMLSerializer().serializeToString(node);
const child = (node, name) => [...node.children].find(item => item.namespaceURI === AC && item.localName === name);
export function parseStorage(value) {
  if (value.length > 2000000 || /<!DOCTYPE|<!ENTITY/i.test(value.replace(/<!\[CDATA\[[\s\S]*?\]\]>/g,''))) throw new Error("지원하지 않는 Confluence 원문 형식입니다.");
  const normalized = value.replace(/<!\[CDATA\[[\s\S]*?\]\]>|&nbsp;/g, part => part === '&nbsp;' ? '&#160;' : part);
  const doc = new DOMParser().parseFromString(`<root xmlns:ac="${AC}" xmlns:ri="${RI}">${normalized}</root>`, "application/xml");
  if (doc.querySelector("parsererror")) throw new Error("Confluence 원문을 안전하게 읽지 못했습니다. 원문에서 확인하세요.");
  return doc.documentElement;
}
export function serializeMacro(node, content) {
  const name = node.dataset.confMacro, config = JSON.parse(node.dataset.confConfig || "{}");
  const definition = macroDefinition(name);
  if (!definition) {
    if (!config.raw) throw new Error("보존할 매크로 원문이 없습니다.");
    return [...parseStorage(config.raw).childNodes].map(xml).join("");
  }
  const root = parseStorage(config.raw || `<ac:structured-macro ac:name="${esc(name)}" ac:schema-version="1" />`), macro = root.firstElementChild;
  if (macro?.namespaceURI !== AC || macro.localName !== "structured-macro" || macro.getAttributeNS(AC,"name") !== name) throw new Error("매크로 종류와 원문이 일치하지 않습니다.");
  const parameter = (key, value) => {
    let item = [...macro.children].find(el => el.namespaceURI === AC && el.localName === "parameter" && el.getAttributeNS(AC,"name") === key);
    if (!item) { item = macro.ownerDocument.createElementNS(AC,"ac:parameter"); item.setAttributeNS(AC,"ac:name",key); macro.append(item); }
    item.replaceChildren();
    if (typeof value === "string") item.textContent = value;
    else item.append(macro.ownerDocument.importNode(value,true));
  };
  for (const [key,value] of Object.entries(config.params || {})) parameter(key,String(value));
  if (Object.hasOwn(definition,"page")) {
    if (config.reference) {
      const ref = config.reference;
      parameter(definition.page, parseStorage(`<ac:link><ri:page ri:content-title="${esc(ref.title)}"${ref.space ? ` ri:space-key="${esc(ref.space)}"` : ''} /></ac:link>`).firstElementChild);
    } else if (!definition.optionalPage && !config.params?.[definition.page]?.trim()) throw new Error("참조 문서를 선택하세요.");
    else if (definition.optionalPage && !config.params?.[definition.page]) [...macro.children].filter(item => item.getAttributeNS(AC,"name") === definition.page).forEach(item => item.remove());
  }
  for (const item of [...macro.children]) if (item.namespaceURI === AC && ["rich-text-body","plain-text-body"].includes(item.localName)) item.remove();
  if (definition.body) {
    const body = macro.ownerDocument.createElementNS(AC,"ac:" + (definition.body === "plain" ? "plain-text-body" : "rich-text-body"));
    if (definition.body === "plain") {
      const parts = node.textContent.split(']]>');
      parts.forEach((part,index) => body.append(macro.ownerDocument.createCDATASection((index ? '>' : '') + part + (index < parts.length-1 ? ']]' : ''))));
    }
    else for (const item of [...parseStorage(content).childNodes]) body.append(macro.ownerDocument.importNode(item,true));
    macro.append(body);
  }
  return xml(macro);
}

// Storage is the editing source. Rendered HTML cannot retain macro parameters or identities.
export function confluenceEditorHtml(storage,pageId,base = location.origin) {
  const root = parseStorage(storage), children = node => [...node.childNodes].map(render).join("");
  const block = (name,config,body = "") => {
    const kind = macroNodeType(name), tag = kind === "confluenceInline" ? "span" : kind === "confluencePlain" ? "pre" : "div";
    return `<${tag} data-conf-macro="${esc(name)}" data-conf-config="${esc(JSON.stringify(config))}">`
      + (kind === "confluenceBlock" ? `<div class="conf-macro-content">${body || "<p></p>"}</div>` : body) + `</${tag}>`;
  };
  function render(node) {
    if (node.nodeType === 3 || node.nodeType === 4) return esc(node.textContent);
    if (node.nodeType !== 1) return "";
    if (node.namespaceURI === AC && node.localName === "structured-macro") {
      const name = node.getAttributeNS(AC,"name"), definition = macroDefinition(name), config = {raw:xml(node),params:{}};
      for (const item of [...node.children].filter(item => item.namespaceURI === AC && item.localName === "parameter")) {
        const key = item.getAttributeNS(AC,"name"), ref = item.getElementsByTagNameNS(RI,"page")[0];
        if (ref && definition && key === definition.page) config.reference = {title:ref.getAttributeNS(RI,"content-title"),space:ref.getAttributeNS(RI,"space-key")};
        else if (!item.children.length) config.params[key] = item.textContent;
      }
      const body = child(node,definition?.body === "plain" ? "plain-text-body" : "rich-text-body");
      return block(name,config,definition ? (definition.body === "plain" ? esc(body?.textContent || "") : body ? children(body) : "") : "");
    }
    if (node.namespaceURI === AC && node.localName === "image") {
      const file = node.getElementsByTagNameNS(RI,"attachment")[0], filename = file?.getAttributeNS(RI,"filename") || "이미지";
      const otherPage = file?.getElementsByTagNameNS(RI,"page")[0];
      const preview = otherPage ? "" : `/api/confluence/pages/${encodeURIComponent(pageId)}/image?path=${encodeURIComponent(new URL(base).pathname.replace(/\/$/,'') + '/download/attachments/' + pageId + '/' + encodeURIComponent(filename))}`;
      return block("resource:image",{raw:xml(node),label:filename,preview});
    }
    if (node.namespaceURI === AC && node.localName === "link") {
      const file = node.getElementsByTagNameNS(RI,"attachment")[0], user = node.getElementsByTagNameNS(RI,"user")[0];
      return block("resource:link",{raw:xml(node),label:file?.getAttributeNS(RI,"filename") || (user ? "@" + user.getAttributeNS(RI,"userkey") : node.textContent || "Confluence 링크")});
    }
    if (node.namespaceURI === AC && ['task-list','task'].includes(node.localName)) return block('unknown',{raw:xml(node),label:'기존 할 일 목록 · ID와 원문 보존'});
    if (node.namespaceURI === AC || node.namespaceURI === RI) return block("unknown",{raw:xml(node)});
    const tag = node.localName.toLowerCase();
    if (!["p","div","span","h1","h2","h3","h4","h5","h6","strong","b","em","i","s","u","code","pre","ul","ol","li","table","tbody","thead","tr","th","td","blockquote","a","br","hr"].includes(tag)) return block("unknown",{raw:xml(node)});
    let attrs = "";
    for (const attr of [...node.attributes]) if (["id","style","colspan","rowspan","start"].includes(attr.name) && !/url\s*\(|expression|[<>]/i.test(attr.value)) attrs += ` ${attr.name}="${esc(attr.value)}"`;
    if (tag === "a") { const href = node.getAttribute("href") || ""; if (/^(https?:\/\/|mailto:|#)/i.test(href)) attrs += ` href="${esc(href)}"`; }
    return `<${tag}${attrs}>${children(node)}${["hr","br"].includes(tag) ? "" : `</${tag}>`}`;
  }
  return children(root);
}

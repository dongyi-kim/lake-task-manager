// New LTM documents only: existing Confluence storage/macros never pass through this adapter.
// DC 9.2 format: https://confluence.atlassian.com/conf92/confluence-storage-format-1477576006.html
import { outlineNumbers } from "../components/editor/headingNumbering.js";
const escape = text => String(text || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const cdata = text => "<![CDATA[" + String(text).replace(/]]>/g, "]]]]><![CDATA[>") + "]]>";
const allowed = new Set("p strong b em i s u code span div h1 h2 h3 h4 h5 h6 ul ol li blockquote table tbody thead tfoot tr th td br hr colgroup col".split(" "));
const voids = new Set(["br", "hr", "col"]);

export function mentionIds(html) {
  return [...new Set([...new DOMParser().parseFromString(html, "text/html").querySelectorAll('[data-type="mention"]')]
    .map(node => node.getAttribute("data-id")).filter(Boolean))];
}

export function confluenceStorage(html, { assets = [], numbered = false, users = {}, jiraBase = "" } = {}) {
  const root = new DOMParser().parseFromString(html, "text/html").body;
  const attachments = new Map(assets.map(file => [file.token, file.filename]));
  const headings = [...root.querySelectorAll("h1,h2,h3,h4,h5,h6")];
  const sequence = outlineNumbers(headings.map(node => +node.tagName[1]));
  const numbers = new Map(headings.map((node, index) => [node, sequence[index]]));
  let taskId = 0;
  const children = node => [...node.childNodes].map(serialize).join("");
  const style = node => {
    const supported = ["color", "background-color", "font-family", "text-align", "text-decoration", "border", "border-collapse", "width"];
    return supported.map(key => {
      const value = node.style.getPropertyValue(key);
      return value && !/url\s*\(|expression|[<>]/i.test(value) ? key + ":" + value : "";
    }).filter(Boolean).join(";");
  };
  const ref = name => '<ri:attachment ri:filename="' + escape(name) + '" />';
  const url = value => {
    if (/^\/browse\/[A-Za-z][A-Za-z0-9]*-\d+$/.test(value) && jiraBase) value = jiraBase.replace(/\/$/, "") + value;
    try { return ["http:", "https:", "mailto:"].includes(new URL(value).protocol) ? escape(value) : ""; } catch (_) { return ""; }
  };
  function serialize(node) {
    if (node.nodeType === 3) return escape(node.textContent);
    if (node.nodeType !== 1) return "";
    const tag = node.tagName.toLowerCase();
    if (["script", "style", "iframe", "object", "svg"].includes(tag)) return "";
    if (node.dataset.type === "mention") {
      const key = users[node.dataset.id];
      if (!key) throw new Error("Confluence에서 멘션 사용자를 확인하지 못했습니다: " + (node.dataset.label || node.dataset.id));
      return '<ac:link><ri:user ri:userkey="' + escape(key) + '" /></ac:link>';
    }
    if (tag === "img") {
      const name = attachments.get(node.getAttribute("src"));
      if (!name) throw new Error("외부 이미지 주소는 그대로 발행할 수 없습니다. 이미지를 파일로 첨부해 주세요.");
      const width = /^\d+$/.test(node.getAttribute("width") || "") ? ' ac:width="' + node.getAttribute("width") + '"' : "";
      return '<ac:image' + width + ' ac:alt="' + escape(node.getAttribute("alt")) + '">' + ref(name) + '</ac:image>';
    }
    if (tag === "a") {
      const href = node.getAttribute("href") || "", name = attachments.get(href);
      if (name) return '<ac:link>' + ref(name) + '<ac:plain-text-link-body>' + cdata(node.textContent || name) + '</ac:plain-text-link-body></ac:link>';
      if (/^(?:draft:|blob:)/.test(href)) throw new Error("첨부 원본이 없는 초안입니다. 파일을 다시 첨부해 주세요.");
      return url(href) ? '<a href="' + url(href) + '">' + children(node) + '</a>' : children(node);
    }
    if (tag === "pre") {
      const code = node.querySelector("code") || node;
      const language = (code.className.match(/language-([\w+-]+)/) || [])[1] || "none";
      return '<ac:structured-macro ac:name="code"><ac:parameter ac:name="language">' + escape(language)
        + '</ac:parameter><ac:plain-text-body>' + cdata(code.textContent) + '</ac:plain-text-body></ac:structured-macro>';
    }
    if (node.classList.contains("callout")) {
      const kind = (node.className.match(/callout-(info|note|tip|warning|success|error)/) || [])[1] || "info";
      const native = { success: "tip", error: "warning" }[kind] || kind;
      const title = { success: "성공", error: "오류" }[kind];
      return '<ac:structured-macro ac:name="' + native + '">' + (title ? '<ac:parameter ac:name="title">' + title + '</ac:parameter>' : '')
        + '<ac:rich-text-body>' + children(node) + '</ac:rich-text-body></ac:structured-macro>';
    }
    if (node.dataset.type === "taskList") return '<ac:task-list>' + children(node) + '</ac:task-list>';
    if (node.dataset.type === "taskItem") return '<ac:task><ac:task-id>' + (++taskId)
      + '</ac:task-id><ac:task-status>' + (node.dataset.checked === "true" ? "complete" : "incomplete")
      + '</ac:task-status><ac:task-body>' + children(node) + '</ac:task-body></ac:task>';
    if (tag === "label" || tag === "input") return "";
    if (!allowed.has(tag)) return children(node);
    let attrs = "", css = style(node);
    if (["table", "th", "td"].includes(tag)) css += ";border:1px solid #b6bec9";
    if (css) attrs += ' style="' + escape(css) + '"';
    for (const key of ["colspan", "rowspan", "span", "start"]) {
      if (/^\d+$/.test(node.getAttribute(key) || "")) attrs += ' ' + key + '="' + node.getAttribute(key) + '"';
    }
    if (voids.has(tag)) return '<' + tag + attrs + ' />';
    const number = numbered && numbers.has(node) ? numbers.get(node) + " " : "";
    return '<' + tag + attrs + '>' + number + children(node) + '</' + tag + '>';
  }
  return children(root);
}

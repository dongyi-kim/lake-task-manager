// Editor-only normalization between Jira-rendered HTML and TipTap's document model.

const SEC_ONELINE = /^\s*={3,}\s*(.+?)\s*={3,}\s*$/;
const P_BLOCK = /<p\b[^>]*>([\s\S]*?)<\/p>/gi;
const TAGS = /<[^>]*>/g;

export function liftSections(html) {
  if (!html || html.indexOf("===") < 0) return html;
  return html.replace(P_BLOCK, (whole, inner) => {
    if (inner.indexOf("===") < 0) return whole;
    const lines = inner.split(/<br\s*\/?>/i);
    const isSection = (line) => SEC_ONELINE.test(line.replace(TAGS, "").trim());
    if (!lines.some(isSection)) return whole;

    const output = [];
    let buffer = [];
    const flush = () => {
      const body = buffer.join("<br>");
      if (body.replace(TAGS, "").trim()) output.push("<p>" + body + "</p>");
      buffer = [];
    };
    for (const line of lines) {
      const match = SEC_ONELINE.exec(line.replace(TAGS, "").trim());
      if (match) {
        flush();
        output.push('<div class="sec-title-node">' + match[1] + "</div>");
      } else {
        buffer.push(line);
      }
    }
    flush();
    return output.join("");
  });
}

const CHECKBOX_PARAGRAPH = /<p\b[^>]*>([\s\S]*?)<\/p>/gi;
const CHECKBOX_ITEM = /<input\b([^>]*)>\s*([\s\S]*?)(?=<input\b|$)/gi;

export function liftCheckboxes(html) {
  if (!html || !/<input[^>]*type=["']?\s*checkbox/i.test(html)) return html;
  return html.replace(CHECKBOX_PARAGRAPH, (paragraph, inner) => {
    if (!/<input[^>]*type=["']?\s*checkbox/i.test(inner)) return paragraph;
    const items = [];
    let match;
    CHECKBOX_ITEM.lastIndex = 0;
    while ((match = CHECKBOX_ITEM.exec(inner)) !== null) {
      const attrs = match[1] || "";
      if (!/type=["']?\s*checkbox/i.test(attrs)) continue;
      const checked = /\bchecked\b/i.test(attrs);
      const text = (match[2] || "").replace(/(?:<br\s*\/?>|\s)+$/i, "");
      items.push('<li data-checked="' + (checked ? "true" : "false")
        + '" data-type="taskItem"><label><input type="checkbox"'
        + (checked ? ' checked="checked"' : "")
        + '><span></span></label><div><p>' + text + "</p></div></li>");
    }
    return items.length
      ? '<ul data-type="taskList">' + items.join("") + "</ul>"
      : paragraph;
  });
}

const AI_TASK_ITEM = /<li\b([^>]*?)data-checked=["']?(true|false)["']?([^>]*)>([\s\S]*?)<\/li>/gi;

export function normalizeAiHtml(html) {
  return String(html || "").replace(AI_TASK_ITEM, (match, before, checked, after, body) => {
    if (/data-type=["']?taskItem/i.test(before + after)) return match;
    const active = String(checked).toLowerCase() === "true";
    const inner = /<(p|div|ul|ol)\b/i.test(body) ? body : "<p>" + body.trim() + "</p>";
    return '<li data-checked="' + (active ? "true" : "false")
      + '" data-type="taskItem"><label><input type="checkbox"'
      + (active ? ' checked="checked"' : "")
      + "><span></span></label><div>" + inner + "</div></li>";
  });
}

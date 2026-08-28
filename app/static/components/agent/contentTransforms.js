// Pure browser-side transforms between editor HTML, model text, and safe draft previews.

export function richEditorToText(html) {
  const doc = new DOMParser().parseFromString(String(html || ""), "text/html");
  doc.querySelectorAll("[data-type='mention'],[data-id]").forEach((el) => {
    const id = el.getAttribute("data-id");
    if (id) el.replaceWith((el.textContent || "").replace(/^@?/, "@") + "(" + id + ")");
  });
  doc.querySelectorAll("a").forEach((a) => {
    const href = (a.getAttribute("href") || "").trim();
    const label = (a.getAttribute("title") || a.textContent || href).trim();
    a.replaceWith(href ? `[${label || href}](${href})` : label);
  });
  doc.querySelectorAll("p,li,h1,h2,h3,blockquote").forEach((el) => el.append("\n"));
  doc.querySelectorAll("br").forEach((el) => el.replaceWith("\n"));
  return (doc.body.textContent || "").replace(/\n{3,}/g, "\n\n").trim();
}

export function draftDescriptionText(html) {
  let s = String(html || "");
  s = s.replace(/<h3[^>]*>(.*?)<\/h3>/gi, "\n■ $1\n")
       .replace(/<li[^>]*data-checked[^>]*>(.*?)<\/li>/gi, "☐ $1\n")
       .replace(/<li[^>]*>(.*?)<\/li>/gi, "· $1\n")
       .replace(/<tr[^>]*>/gi, "\n| ").replace(/<\/t[dh]>/gi, " | ")
       .replace(/<a[^>]*href="([^"]*)"[^>]*>(.*?)<\/a>/gi, "$2 ($1)")
       .replace(/<\/p>|<br\s*\/?>/gi, "\n")
       .replace(/<[^>]+>/g, "")
       .replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">");
  return s.replace(/\n{3,}/g, "\n\n").trim();
}

export function sanitizeDraftDescription(html) {
  const doc = new DOMParser().parseFromString(String(html || ""), "text/html");
  const ALLOW = new Set(["H3", "P", "UL", "OL", "LI", "TABLE", "THEAD", "TBODY",
                         "TR", "TH", "TD", "A", "B", "STRONG", "EM", "CODE", "BR", "INPUT"]);
  const walk = (node) => {
    for (const el of [...node.children]) {
      // 실행류 태그는 **내용째** 버린다 — 언랩하면 코드 텍스트가 본문처럼 남는다.
      if (["SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "FORM"].includes(el.tagName)) {
        el.remove(); continue;
      }
      walk(el);
      if (!ALLOW.has(el.tagName)) { el.replaceWith(...el.childNodes); continue; }
      for (const a of [...el.attributes]) {
        const keep = (el.tagName === "A" && a.name === "href" && /^https?:/.test(a.value))
          || (el.tagName === "LI" && ["data-type", "data-checked"].includes(a.name))
          || (el.tagName === "UL" && a.name === "data-type")
          || (el.tagName === "INPUT" && a.name === "type" && a.value === "checkbox");
        if (!keep) el.removeAttribute(a.name);
      }
      if (el.tagName === "A") { el.setAttribute("target", "_blank"); el.setAttribute("rel", "noopener"); }
      // taskList 항목은 체크박스로 보이게
      if (el.tagName === "LI" && el.hasAttribute("data-checked")) {
        const cb = doc.createElement("input");
        cb.type = "checkbox"; cb.disabled = true;
        if (el.getAttribute("data-checked") === "true") cb.checked = true;
        el.prepend(cb);
      }
    }
  };
  walk(doc.body);
  return doc.body.innerHTML;
}

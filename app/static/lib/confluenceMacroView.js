import { createApp } from "../vendor/vue.esm-browser.prod.js";
import ConfluenceMacroTree from "../components/ui/ConfluenceMacroTree.js";
import { outlineNumbers } from "../components/editor/headingNumbering.js";
export function mountConfluenceMacros(body, headings) {
  const apps = [];
  for (const slot of [...body.querySelectorAll("[data-conf-dynamic]")].slice(0,100)) {
    try { const descriptor = JSON.parse(slot.dataset.confDynamic); const app = createApp(ConfluenceMacroTree,{descriptor}); app.mount(slot); apps.push(app); }
    catch (_) { slot.textContent = "매크로 설정을 읽지 못했습니다."; }
  }
  for (const slot of body.querySelectorAll("[data-conf-toc]")) {
    slot.replaceChildren();
    try {
      const params = JSON.parse(slot.dataset.confToc), zone = slot.closest(".conf-toc-zone") || body;
      const items = headings.filter(item => zone.contains(item.node) && item.level >= Number(params.minLevel || 1) && item.level <= Number(params.maxLevel || 6));
      const numbers = outlineNumbers(items.map(item => item.level)), list = document.createElement(params.type === "flat" ? "div" : "ul");
      if (params.include || params.exclude) { const note = document.createElement("p"); note.textContent = "이 목차의 고급 정규식 필터는 Confluence 원문에서 확인하세요."; slot.append(note); }
      items.forEach((item,index) => { const row = document.createElement(params.type === "flat" ? "span" : "li"), link = document.createElement("a");
        link.href = "#" + item.id; link.textContent = (params.outline === "true" && !/^\d+(?:\.\d+)*\s/.test(item.title) ? numbers[index] + " " : "") + item.title;
        row.style.marginLeft = Math.max(0,item.level - Number(params.minLevel || 1))*12 + "px"; row.append(link); list.append(row); });
      slot.append(list);
    } catch (_) { slot.textContent = "목차 설정을 읽지 못했습니다."; }
  }
  for (const pre of body.querySelectorAll("[data-conf-code]")) {
    pre.querySelector('.conf-code-lines')?.remove();
    try { const params = JSON.parse(pre.dataset.confCode); if (!params.numbered) continue;
      const code = pre.querySelector("code"), gutter = document.createElement("span"); gutter.className = "conf-code-lines"; gutter.setAttribute("aria-hidden","true");
      const first = Math.max(1,Number(params.firstline)||1); gutter.textContent = code.textContent.split("\n").map((_,i)=>first+i).join("\n"); pre.prepend(gutter);
    } catch (_) { /* The unmodified code remains readable. */ }
  }
  return () => apps.forEach(app => app.unmount());
}

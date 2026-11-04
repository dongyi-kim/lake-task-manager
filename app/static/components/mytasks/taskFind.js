// Search the current filtered card model, before pagination and folding.
import { STATES } from "./taskModel.js";

const normalize = (value) => String(value || "").normalize("NFKC").toLocaleLowerCase().trim();

export function taskFindRows(sections, epicTitle = (key) => key) {
  const rows = [], seen = new Set();
  const add = (card, panel, section, parentKey, entryId, category) => {
    if (!card?.key || seen.has(card.key)) return;
    seen.add(card.key);
    rows.push({ key: card.key, parentKey, entryId, category, sectionKey: section.key,
      text: normalize([card.key, card.title, card.assignee, card.status,
        card.epicKey && epicTitle(card.epicKey)].filter(Boolean).join(" ")) });
  };
  for (const section of sections || []) for (const panel of section.panels || []) {
    if (panel.kind === "task") {
      const category = panel.cards[0]?.statusCategory || panel.parentCard.statusCategory;
      add(panel.parentCard, panel, section, panel.key, "panel:" + panel.key, category);
      for (const st of STATES) for (const card of panel.cards.filter((c) => c.statusCategory === st.k)) {
        add(card, panel, section, panel.key, "panel:" + panel.key, st.k);
      }
    } else {
      for (const st of STATES) for (const card of panel.cards.filter((c) => c.statusCategory === st.k)) {
        add(card, panel, section, card.compactPanel?.key, "card:" + card.key, st.k);
        for (const child of card.compactPanel?.cards || []) {
          add(child, panel, section, card.key, "card:" + card.key, st.k);
        }
      }
    }
  }
  return rows;
}

export function findTaskCards(rows, query) {
  const text = normalize(query);
  return text ? rows.filter((row) => row.text.includes(text)) : [];
}

export function nextTaskMatch(results, key, delta) {
  if (!results.length) return "";
  const index = results.findIndex((row) => row.key === key);
  return results[(Math.max(0, index) + delta + results.length) % results.length].key;
}

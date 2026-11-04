import { categoryColor, wbsColor } from "../../lib/colors.js";

/** Group whole parent+child bundles once, before layout, folding, sorting, or paging. */
export function groupTaskSections(groups, mode, metadata = {}, epicMap = {}) {
  if (mode !== "wbs" && mode !== "epic") return [{ key: "__all__", title: "", groups }];
  const memberships = new Map(), sections = new Map();
  for (const wbs of metadata.wbs || []) for (const epic of wbs.epics) {
    if (!memberships.has(epic)) memberships.set(epic, []);
    memberships.get(epic).push(wbs);
  }
  for (const group of groups) {
    const epic = group.epic, member = memberships.get(epic) || [], wbs = member[0];
    const key = mode === "wbs" ? (wbs ? "wbs:" + wbs.id : "__unmapped__")
      : (epic || (group.voc ? "__voc__" : "__none__"));
    if (!sections.has(key)) sections.set(key, {
      key, groups: [], extra: [],
      title: mode === "wbs" ? (wbs ? wbs.name : metadata.unavailable ? "WBS 정보 확인 필요" : "WBS 없음")
        : (epic ? (epicMap[epic]?.pending ? "Epic 이름 확인 중" : epicMap[epic]?.title || epic)
          : group.voc ? "사용자 VoC" : "Epic 없음"),
      label: mode === "wbs" ? (wbs ? wbs.module + " · " + wbs.id : "") : epic || "",
      color: mode === "wbs" ? (wbs ? wbsColor(wbs.id) : null)
        : epic ? categoryColor(epic) : group.voc ? "var(--ty-story)" : null,
    });
    const section = sections.get(key);
    section.groups.push(group);
    if (mode === "wbs" && member.length > 1 && !section.extra.some((item) => item.epic === epic)) {
      section.extra.push({ epic, names: member.slice(1).map((row) => row.module + " · " + row.name) });
    }
  }
  const order = mode === "wbs" ? (metadata.wbs || []).map((row) => "wbs:" + row.id)
    : [...sections.keys()].filter((key) => !key.startsWith("__")).sort((a, b) =>
      sections.get(a).title.localeCompare(sections.get(b).title, "ko") || a.localeCompare(b));
  return [...new Set(order.concat("__voc__", "__none__", "__unmapped__"))]
    .filter((key) => sections.has(key)).map((key) => sections.get(key));
}

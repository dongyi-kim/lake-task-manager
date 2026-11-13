// A ticket navigation guard owns no drafts or mutation state. Existing editors persist them.
function refs(value) { return (Array.isArray(value) ? value : [value]).filter(Boolean); }
export function ticketEditors(dialog) {
  const all = dialog.$refs || {};
  return [...new Set([all.ded, all.newCommentEditor, all.editingCommentEditor].flatMap(refs))];
}
function fields(dialog) {
  return Object.entries(dialog.$refs || {}).filter(([name]) => name.startsWith("editField"))
    .flatMap(([, value]) => refs(value));
}
function blockedReason(dialog, editors, options) {
  if (options.isBusy?.()) return options.busyLabel?.() || "작업이 진행 중입니다.";
  if (dialog.uploading || dialog.descBusy || dialog.sumBusy || dialog.linkBusy || dialog.docBusy || dialog.pendingWrites)
    return "저장 또는 첨부 처리가 끝난 뒤 다시 선택하세요.";
  if (dialog.adding || dialog.stPick) return "열려 있는 생성·전이 창에서 작성 내용을 보관하거나 작업을 마치세요.";
  if (dialog.sumEdit && dialog.sumDraft !== (dialog.v?.summary || "")) return "수정 중인 제목을 저장하거나 취소한 뒤 다시 선택하세요.";
  if (fields(dialog).some(field => field.busy)) return "필드 저장이 끝난 뒤 다시 선택하세요.";
  if (fields(dialog).some(field => field.open)) return "열려 있는 필드 편집을 적용하거나 취소한 뒤 다시 선택하세요.";
  if ((dialog.descEdit && !dialog.$refs?.ded) || (dialog.composing && !dialog.$refs?.newCommentEditor)
      || (dialog.editingId && !refs(dialog.$refs?.editingCommentEditor).length))
    return "편집기를 준비 중입니다. 잠시 후 다시 선택하세요.";
  for (const editor of editors) {
    if (editor.busy || editor.submitting || editor.aiBusy) return "에디터 저장 또는 첨부 처리가 끝난 뒤 다시 선택하세요.";
    if (editor.draftConflict) return "초안 충돌을 확인한 뒤 다시 선택하세요.";
    // An empty current body must not clear an unresolved submission receipt in flushDraft().
    if ((editor.submitMutationId || editor.pendingSubmitHtml || editor.mutationPending) && editor.isBlank?.())
      return "반영 결과가 확인되지 않은 작성 내용이 있습니다. 에디터에서 결과를 확인하세요.";
  }
  return "";
}

export async function prepareTicketLeave(dialog, options = {}) {
  const editors = ticketEditors(dialog);
  const warn = message => { options.notify?.(message); return false; };
  const blocked = blockedReason(dialog, editors, options);
  if (blocked) return warn(blocked);
  const revisions = editors.map(editor => editor.tick);
  for (const editor of editors) {
    if (typeof editor.flushDraft !== "function") return warn("초안 보존을 확인할 수 없습니다.");
    try {
      if (await editor.flushDraft() === false) return false; // Editor retains and reports its own error.
    } catch (_) { return warn("초안을 보관하지 못했습니다. 현재 편집기에서 다시 시도하세요."); }
  }
  const current = ticketEditors(dialog);
  if (current.length !== editors.length || current.some((editor, i) => editor !== editors[i] || editor.tick !== revisions[i]))
    return warn("작성 내용이 바뀌었습니다. 보관을 위해 다시 선택하세요.");
  const changed = blockedReason(dialog, current, options);
  return changed ? warn(changed) : true;
}

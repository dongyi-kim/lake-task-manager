// A chunk's receipt ID and ordered input never change, including after a lost HTTP response.
const REPLAY_WINDOW_MS = 7 * 24 * 60 * 60 * 1000; // Server receipts outlive this by one day.

export function bulkScopeKey(scope) {
  const fields = ["environment", "jiraBase", "projectKey", "userId"];
  if (!scope || fields.some(field => !String(scope[field] || "").trim()))
    throw new Error("Jira 실행 정보를 확인하지 못했습니다. 원래 연결과 사용자로 다시 확인하세요.");
  return fields.map(field => encodeURIComponent(scope[field])).join(":");
}

export function newBulkRun(mode, items, id, scope, chunkSize = 5) {
  bulkScopeKey(scope);
  const snapshot = JSON.parse(JSON.stringify(items));
  const chunks = [];
  for (let offset = 0; offset < snapshot.length; offset += chunkSize)
    chunks.push({ offset, body: { mode, items: snapshot.slice(offset, offset + chunkSize),
      clientMutationId: id + ":" + offset, expectedScope: { ...scope } }, states: {}, attempted: false });
  return { id, mode, scope: { ...scope }, createdAt: Date.now(), firstAttemptAt: null, items: snapshot, chunks };
}

function replayBlock(run) {
  if (!run.chunks.some(chunk => chunk.body.items.some((_, i) => chunk.states[i]?.ok !== true))) return "";
  if (!run.scope) return "이전 실행의 Jira 연결 정보를 확인할 수 없습니다. 초안을 보존했으니 Jira에서 생성 여부를 직접 확인하세요.";
  if (run.chunks.some(chunk => chunk.attempted) && (!Number.isFinite(run.firstAttemptAt)
      || run.firstAttemptAt <= 0 || Date.now() < run.firstAttemptAt
      || Date.now() - run.firstAttemptAt >= REPLAY_WINDOW_MS))
    return "최초 생성 요청 후 7일이 지나 중복 방지 결과를 안전하게 재확인할 수 없습니다. 초안을 보존했으니 Jira에서 생성 여부를 직접 확인하세요.";
  return "";
}

export function bulkRunResult(run) {
  const created = [], failed = [], pending = [], notStarted = [], status = {};
  for (const chunk of run.chunks) for (let local = 0; local < chunk.body.items.length; local++) {
    const index = chunk.offset + local, state = chunk.states[local];
    if (!state) { notStarted.push(index); continue; }
    const entry = { ...state, index, summary: chunk.body.items[local].summary || "" };
    status[index] = entry;
    if (state.ok === true) created.push(entry);
    else if (state.ok === false) failed.push(entry);
    else pending.push(entry);
  }
  return { ok: !failed.length && !pending.length && !notStarted.length,
    created, failed, pending, notStarted, status, blocked: replayBlock(run) };
}

function acceptChunk(chunk, response, uncertain) {
  const count = chunk.body.items.length, seen = new Set(), invalid = new Set(), updates = {};
  const rows = [...(response?.created || []).map(row => ({ ...row, ok: true })),
    ...(response?.failed || []).map(row => ({ ...row,
      ok: row.code === "outcome_unknown" || row.repeatWrite === false ? null : false }))];
  for (const row of rows) {
    const index = row.index;
    if (!Number.isInteger(index) || index < 0 || index >= count) continue;
    if (seen.has(index) || (row.ok === true && !row.key)) { invalid.add(index); continue; }
    seen.add(index);
    updates[index] = row;
  }
  for (let index = 0; index < count; index++) {
    // Only confirmation from an earlier complete response can survive malformed replay rows.
    if (chunk.states[index]?.ok === true) continue;
    if (!seen.has(index) || invalid.has(index)) chunk.states[index] = {
      ok: null, error: "서버의 생성 결과가 불완전합니다. 같은 요청으로 결과를 확인하세요." };
    else {
      const update = updates[index];
      // An auth/permission/reconciliation error cannot disprove an earlier committed write.
      // Only a positive created key resolves an uncertain row with the current server contract.
      chunk.states[index] = uncertain.has(index) && update.ok === false ? { ...update, ok: null } : update;
    }
  }
}

export async function executeBulkRun(run, send, persist, progress = () => {}) {
  for (const chunk of run.chunks) {
    if (chunk.body.items.every((_, index) => chunk.states[index]?.ok === true)) continue;
    if (replayBlock(run)) break;
    const recovering = chunk.attempted;
    const uncertain = new Set(Object.keys(chunk.states).filter(index => chunk.states[index]?.ok === null).map(Number));
    if (!run.firstAttemptAt) run.firstAttemptAt = Date.now();
    chunk.attempted = true;
    for (let index = 0; index < chunk.body.items.length; index++)
      if (chunk.states[index]?.ok !== true) chunk.states[index] = { ok: null, error: "생성 결과 확인 중" };
    // Durably save exact inputs and IDs BEFORE the first byte of each write.
    await persist(run);
    progress(bulkRunResult(run));
    try {
      const response = await send(chunk.body);
      if (!Array.isArray(response?.created) || !Array.isArray(response?.failed)) throw new Error("불완전한 생성 응답");
      acceptChunk(chunk, response, uncertain);
    } catch (error) {
      // 400/422 are the bulk endpoint's pre-write validation rejection. Transport/auth/5xx
      // can hide a committed write and must retain the receipt, never offer a new-ID retry.
      const rejected = !recovering && [400, 422].includes(error?.status);
      for (let index = 0; index < chunk.body.items.length; index++)
        if (chunk.states[index]?.ok !== true) chunk.states[index] = {
          ok: rejected ? false : null, error: error?.message || "생성 응답을 확인하지 못했습니다." };
    }
    await persist(run);
    const result = bulkRunResult(run);
    progress(result);
    if (result.pending.length) break;
  }
  return bulkRunResult(run);
}

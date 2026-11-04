// Request activity is independent of the server's incomplete-data flags.
import { cloneTaskModel, reconcileTaskModel, TASK_RETRY_DELAYS, retryDelay,
  taskLoadErrorKind } from "./taskModel.js";

export const childSyncBusy = (state) => ["queued", "loading", "retrying"].includes(state);

export async function hydrateTaskGroups({ model, loadGroup, isCurrent, canStart, publish,
  onStates, onFailure, keys, wait = retryDelay }) {
  let working = cloneTaskModel(model);
  const syncId = working.syncId;
  let sequence = Number(working.snapshotSequence) || 0;
  const selected = keys && new Set(keys);
  const jobs = (working.groups || []).filter((g) => g.childrenPending
    && (!selected || selected.has(g.key))).map((g) => ({ key: g.key, attempt: 0 }));
  const states = Object.fromEntries(jobs.map((job) => [job.key, "queued"]));
  let cursor = 0;
  const state = (key, value) => { states[key] = value; if (isCurrent()) onStates({ ...states }); };
  if (isCurrent()) onStates({ ...states });
  const latest = (key) => (working.groups || []).find((g) => g.key === key);
  const worker = async () => {
    while (cursor < jobs.length && isCurrent() && canStart()) {
      const job = jobs[cursor++];
      state(job.key, "loading");
      let kind = "other", error;
      try {
        const result = await loadGroup(syncId, job.key);
        if (!isCurrent()) return;
        const nextSequence = Number(result?.sequence);
        if (result?.contract !== "task-snapshot.v1" || result.type !== "snapshot"
            || result.syncId !== syncId || !Number.isInteger(nextSequence) || nextSequence < 1
            || result.model?.syncId !== syncId || !Array.isArray(result.model?.groups)
            || !result.model.groups.some((g) => g.key === job.key)) {
          throw new Error("SubTask 동기화 응답을 확인할 수 없습니다.");
        }
        // An older response still owns its job outcome, even if another job already published
        // a newer cumulative snapshot. Discard only its data, never its retry decision.
        if (nextSequence > sequence) {
          sequence = nextSequence;
          working = reconcileTaskModel(working, { ...result.model, streamComplete: true });
          publish(working);
        }
        const group = latest(job.key);
        if (group?.childrenLoaded && !group.childrenPending) {
          state(job.key, "complete");
          continue;
        }
        if (!result.retryable) {
          if (group?.childrenUnavailable) { state(job.key, "unavailable"); continue; }
          throw new Error("SubTask 동기화가 완료되지 않았습니다.");
        }
        const kinds = new Set((result.missing || []).map((row) => row.kind || "other"));
        kind = kinds.has("auth") ? "auth" : "other";
        error = new Error("일부 SubTask를 불러오지 못했습니다.");
      } catch (exc) {
        if (!isCurrent()) return;
        error = exc;
        kind = taskLoadErrorKind(exc);
      }
      if (kind === "permission") { state(job.key, "unavailable"); continue; }
      const delay = TASK_RETRY_DELAYS[job.attempt];
      if (kind !== "auth" && delay !== undefined && canStart()) {
        state(job.key, "retrying");
        await wait(delay);
        if (isCurrent() && canStart()) jobs.push({ ...job, attempt: job.attempt + 1 });
        continue;
      }
      state(job.key, kind === "auth" ? "waiting-auth" : "failed");
      if (canStart()) onFailure(job.key, kind, error);
    }
  };
  await Promise.allSettled([worker(), worker()]);
  // Switching filters cancels queued work, not already completed cache writes. No idle job may
  // leave a spinner behind when that cached filter is shown again.
  for (const [key, value] of Object.entries(states)) if (childSyncBusy(value)) state(key, "paused");
  return { model: working, states };
}

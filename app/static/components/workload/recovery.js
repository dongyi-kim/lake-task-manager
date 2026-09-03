// Workload 부분 조회 복구 primitives.
// DOM/Vue에 의존하지 않아 사람 요약, 상세 컬럼, 마감 리스크가 같은 분류·큐 정책을 공유한다.

export const WORKLOAD_PERSON_RETRY_DELAYS = [800, 2400, 5000];
export const WORKLOAD_BUCKET_RETRY_DELAYS = [800, 2400, 5000];
export const WORKLOAD_REQUEST_CONCURRENCY = 3;

export function workloadErrorKind(error) {
  const explicit = String((error && (error.errorKind || error.kind)) || "").toLowerCase();
  if (explicit === "permission") return "permission";
  if (explicit === "auth") return "auth";
  const status = Number(error && error.status);
  if (status === 403) return "permission";
  if (status === 401 || (error && error.needLogin)) return "auth";
  const message = String((error && error.message) || error || "").toLowerCase();
  // prod SSO는 특정 Jira 요청의 403을 "세션은 정상"인 502로 바꿔 전달할 수 있다. 원문에
  // 남은 403/권한 표식을 인증 만료보다 먼저 가려야 불필요한 로그인 창을 띄우지 않는다.
  if (/\b403\b|forbidden|permission|not permitted|권한|거절.*세션은 정상/.test(message)) return "permission";
  if (/\b401\b|login|required|session expired|anonymous|인증|로그인/.test(message)) return "auth";
  return "other";
}

export function bucketState(status = "idle", extra = {}) {
  return Object.assign({ status, attempt: 0, kind: "", message: "", requestKey: "" }, extra);
}

/** 성공한 open/inProgress 조각만 누적한 마감 리스크 projection. 불완전 여부도 함께 반환한다. */
export function summarizeDueRiskParts({ people, parts, excludeVoc, dueRank, nameOf }) {
  const expectedKeys = new Set((people || []).flatMap((person) =>
    [person.id + "|open", person.id + "|inProgress"]));
  const over = [], soon = [], seen = new Set();
  for (const partKey of expectedKeys) {
    const part = parts[partKey];
    if (!part || part.status !== "success") continue;
    for (const ticket of (part.rows || [])) {
      if (!ticket.due || (excludeVoc && ticket.voc && !ticket.epic) || seen.has(ticket.key)) continue;
      seen.add(ticket.key);
      const row = { t: ticket, who: nameOf(part.id) };
      const days = dueRank(ticket);
      if (days < 0) over.push(row);
      else if (days <= 3) soon.push(row);
    }
  }
  over.sort((a, b) => dueRank(a.t) - dueRank(b.t));
  soon.sort((a, b) => dueRank(a.t) - dueRank(b.t));
  const values = [...expectedKeys].map((key) => parts[key]).filter(Boolean);
  return {
    over, soon, parts,
    complete: expectedKeys.size > 0 && [...expectedKeys].every(
      (key) => parts[key] && parts[key].status === "success"),
    failures: values.filter((part) => part.status === "error").length,
    permissionLimited: values.some((part) => part.status === "permission"),
    loaded: values.length,
    expected: expectedKeys.size,
  };
}

/** 제한된 동시성 + single-flight + foreground priority + 최신 필터 우선 큐. */
export class WorkloadRequestScheduler {
  constructor(limit = WORKLOAD_REQUEST_CONCURRENCY) {
    this.limit = limit;
    this.queue = [];
    this.active = 0;
    this.inFlight = new Map();
    this.sequence = 0;
  }

  schedule(key, run, priority = 0, freshness = 0) {
    const existing = this.inFlight.get(key);
    if (existing) {
      // 백그라운드 마감 집계로 대기 중인 버킷을 사용자가 펼치면 요청은 공유하고 우선순위만 올린다.
      if (!existing.started) {
        existing.priority = Math.max(existing.priority, priority);
        existing.freshness = Math.max(existing.freshness, freshness);
        this._sort();
      }
      return existing.promise;
    }
    let resolveTask, rejectTask;
    const promise = new Promise((resolve, reject) => { resolveTask = resolve; rejectTask = reject; });
    const task = {
      key, run, priority, freshness, order: this.sequence++, resolveTask, rejectTask,
      promise, started: false,
    };
    this.inFlight.set(key, task);
    this.queue.push(task);
    this._sort();
    this._pump();
    return promise;
  }

  _sort() {
    // 이전 필터 요청도 실행해 API 캐시는 데우되, 같은 우선순위에서는 최신 필터부터 보낸다.
    this.queue.sort((a, b) => b.priority - a.priority
      || b.freshness - a.freshness || a.order - b.order);
  }

  _pump() {
    while (this.active < this.limit && this.queue.length) {
      const task = this.queue.shift();
      task.started = true;
      this.active++;
      Promise.resolve().then(task.run).then(task.resolveTask, task.rejectTask).then(
        () => this._finish(task), () => this._finish(task));
    }
  }

  _finish(task) {
    if (this.inFlight.get(task.key) === task) this.inFlight.delete(task.key);
    this.active = Math.max(0, this.active - 1);
    this._pump();
  }
}

function waitForRetry(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** 상세 컬럼과 마감 리스크가 공유하는 한 bucket의 bounded retry. */
export async function fetchWorkloadBucketRows(options) {
  const {
    id, bucket, doneDays, assignedWindow, scheduler, request,
    priority = 0, freshness = 0, isCurrent, onRetry, onFailure, onPartial,
    retryDelays = WORKLOAD_BUCKET_RETRY_DELAYS,
  } = options;
  const requestKey = [id, bucket, doneDays, assignedWindow].join(":");
  for (let attempt = 0; ; attempt++) {
    if (isCurrent && !isCurrent()) return { status: "cancelled", rows: [] };
    try {
      const rows = await scheduler.schedule(
        "bucket:" + requestKey, request, priority, freshness);
      if (!Array.isArray(rows)) throw new Error("워크로드 티켓 목록 응답이 불완전합니다.");
      const unresolved = rows.find((row) => row && row.epicResolution
        && row.epicResolution.complete === false && row.epicResolution.retryable);
      if (unresolved) {
        const incomplete = new Error("일부 SubTask의 상위 Epic을 확인하지 못했습니다.");
        incomplete.errorKind = unresolved.epicResolution.kind || "other";
        incomplete.partialRows = rows;
        if (onPartial) onPartial(rows, attempt + 1, incomplete);
        throw incomplete;
      }
      return { status: "success", rows };
    } catch (error) {
      // 오래된 필터 실패는 결과 캐시만 남기고 토스트·재시도를 만들지 않는다.
      if (isCurrent && !isCurrent()) return { status: "cancelled", rows: [] };
      const kind = workloadErrorKind(error);
      if (kind === "permission") return { status: "permission", kind, rows: [], error };
      if (kind === "auth") {
        if (onFailure) onFailure(kind, true);
        if (error.partialRows) return { status: "partial", kind, rows: error.partialRows, error };
        return { status: "error", kind, rows: [], error };
      }
      const delay = retryDelays[attempt];
      if (delay !== undefined) {
        if (onFailure) onFailure(kind, false);
        if (onRetry && !error.partialRows) onRetry(attempt + 1, error);
        await waitForRetry(delay);
        continue;
      }
      if (onFailure) onFailure(kind, true);
      if (error.partialRows) return { status: "partial", kind, rows: error.partialRows, error };
      return { status: "error", kind, rows: [], error };
    }
  }
}

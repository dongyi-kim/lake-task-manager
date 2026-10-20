// Shared visible/copy text. Missing remote accounting is unknown, never free.
const count = (value) => Number.isFinite(value) && value >= 0 ? Math.floor(value) : 0;

export function usageSummary(usage) {
  if (!usage) return "";
  const calls = count(usage.calls), remote = count(usage.remoteCalls);
  const failed = count(usage.failedCalls), unknown = count(usage.unknownUsageCalls);
  const tokens = count(usage.totalTokens);
  const label = remote ? (calls > remote ? `요청 ${calls}회 (MAXIS ${remote}회 포함)` : `MAXIS 요청 ${calls}회`) : `LLM ${calls}회`;
  const partial = usage.usageComplete === false;
  const tokenText = partial
    ? (tokens ? `확인된 ${tokens.toLocaleString("ko-KR")} 토큰 (${unknown}회 미확인)` : "토큰 미확인")
    : `${tokens.toLocaleString("ko-KR")} 토큰`;
  const cost = typeof usage.costUsd === "number" && Number.isFinite(usage.costUsd)
    && usage.costUsd >= 0 && usage.costStatus !== "unknown"
    ? `$${usage.costUsd.toFixed(4)}` : "비용 미확인";
  return `${label}${failed ? ` · 실패 ${failed}회` : ""} · ${tokenText} · ${cost}`;
}

export function usageTitle(usage) {
  if (count(usage?.remoteCalls)) return "MAXIS 요청 횟수는 서버 내부 모델 호출 수와 다릅니다. 토큰은 응답에 제공된 사용량만 집계하며, 실패한 요청의 미제공 사용량과 서버 내부 추가 호출은 확인할 수 없습니다.";
  return `${usage?.model || "모델 미확인"} · 입력 ${count(usage?.promptTokens)} / 출력 ${count(usage?.completionTokens)}`;
}

export function runtimeDebugLines(turn) {
  const runtime = turn?.runtime || {}, usage = turn?.usage || {};
  const engines = [...new Set(Object.values(runtime.roleEngines || {})
    .filter((value) => value === "maxis" || value === "local"))].sort();
  const engine = engines.length > 1 ? `mixed (${engines.join(", ")})`
    : engines[0] || runtime.engine || "미기록";
  const remote = engines.includes("maxis") || runtime.engine === "maxis" || count(usage.remoteCalls) > 0;
  const localModel = usage.model && usage.model !== "maxis/dify" ? usage.model : "";
  const model = remote ? `${localModel ? `로컬 ${localModel} / ` : ""}MAXIS 서버 Workflow 모델 (Service API 미공개)`
    : localModel || "미기록";
  return [`- engine: ${engine}`, `- profile: ${runtime.profile || "해당 없음 / 미기록"}`,
    ...(runtime.profileLabel ? [`- profileLabel: ${runtime.profileLabel}`] : []),
    ...(runtime.workflowBundleVersion ? [`- workflowBundleLabel: ${runtime.workflowBundleVersion} (profile metadata)`] : []),
    ...(runtime.knowledgeVersion ? [`- knowledgeReleaseLabel: ${runtime.knowledgeVersion} (profile metadata)`] : []),
    `- model: ${model}`];
}

export function runtimeProfileLabel(turn) {
  return turn?.runtime?.profileLabel || "MAXIS 연결";
}

export function runtimeQualityStatus(turn) {
  if (turn?.ok === false || turn?.error || turn?.debug?.error || turn?.stopped)
    return {label: "실행 중단", bad: true};
  const gate = turn?.runtime?.qualityGate;
  if (gate?.ok === false) return {label: "응답 계약 차단", bad: true};
  return {label: gate?.ok === true ? "응답 계약 확인" : "응답 계약 미확인", bad: false};
}

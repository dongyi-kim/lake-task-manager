import { api } from './api.js';

function pause(signal) {
  return new Promise((resolve, reject) => {
    const cancel = () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')); };
    const timer = setTimeout(() => { signal.removeEventListener('abort',cancel); resolve(); }, 1000);
    if (signal.aborted) cancel(); else signal.addEventListener('abort',cancel,{once:true});
  });
}

// Only safe reads are retried, once. The server shares in-flight fetches and caches
// completed phases even if the first browser request exceeded its deadline.
export async function readConfluence(id, version, refresh, signal, retrying) {
  try { return await api.confluenceDocument(id, version, refresh, signal); }
  catch (error) {
    if (signal.aborted || error.name === 'AbortError' || [401,403,404].includes(error.status)
        || !(error.retryable || error instanceof TypeError || [429,502,503,504].includes(error.status))) throw error;
    retrying();
    await pause(signal);
    return api.confluenceDocument(id, version, false, signal);
  }
}

export function confluenceFailure(error) {
  if (typeof error === 'string') return {title:'문서를 표시하지 못했습니다', detail:error, retry:true};
  if (error?.status === 401) return {title:'Confluence 인증 연결을 확인해 주세요',
    detail:'자동 인증 복구를 시도했지만 연결되지 않았습니다. 다른 화면은 계속 사용할 수 있습니다.', retry:true};
  if (error?.status === 403) return {title:'이 문서에 접근할 권한이 없습니다', detail:'접근 권한이 변경되었을 수 있습니다. Confluence에서 권한을 확인해 주세요.', retry:false};
  if (error?.status === 404) return {title:'문서를 찾을 수 없습니다', detail:'문서가 삭제되었거나 더 이상 공개되지 않았을 수 있습니다.', retry:false};
  if (['request_timeout','upstream_timeout'].includes(error?.reason)) return {title:'문서 응답 대기 시간이 초과되었습니다',
    detail:'자동 재시도 후에도 응답을 받지 못했습니다. 인증 만료로 확인된 오류는 아닙니다.', retry:true};
  return {title:'Confluence 연결이 원활하지 않습니다',
    detail:'자동 재시도 후에도 문서를 불러오지 못했습니다. 잠시 후 이 문서만 다시 불러올 수 있습니다.', retry:true};
}

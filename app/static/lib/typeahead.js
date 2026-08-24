// typeahead.js — 자동완성(통합검색·티켓/문서 링크·@멘션) 공용 입력 처리.
//
// 자동완성이 버벅이는 원인은 대개 서버가 아니라 **호출 방식**이다. 셋을 한곳에서 처리한다:
//  1) 디바운스 — 타이핑이 멈춘 뒤에만 부른다. 한글 IME 는 자모마다 입력 이벤트가 나므로
//     디바운스가 없으면 '정' 한 글자에 요청이 서너 번 나간다.
//  2) 응답 역전 방어 — 늦게 도착한 옛 응답이 새 결과를 덮으면 목록이 튄다(가장 큰 체감 버벅임).
//     순번을 매겨 최신 것만 반영한다.
//  3) 같은 질의 캐시 — 지웠다 다시 치거나 커서만 움직여도 같은 요청이 또 나가는 걸 막는다.
//
// 대기 시간은 사람마다 체감이 달라 **설정에서 조절**한다(설정 메뉴 → 자동완성 반응 속도).

const KEY = "typeaheadMs";
export const TYPEAHEAD_PRESETS = [
  { ms: 120, label: "빠름", hint: "타이핑 중에도 자주 갱신 — 요청이 많아진다" },
  { ms: 280, label: "보통", hint: "기본값" },
  { ms: 500, label: "여유", hint: "잠깐 멈춰야 갱신 — 느린 망/원격에서 편하다" },
  { ms: 800, label: "아주 여유", hint: "확실히 멈춘 뒤에만 갱신" },
];
const DEFAULT_MS = 280;

export function typeaheadDelay() {
  try {
    const v = parseInt(localStorage.getItem(KEY), 10);
    if (v >= 0 && v <= 3000) return v;
  } catch (e) { /* 프라이빗 모드 등 — 기본값 */ }
  return DEFAULT_MS;
}

export function setTypeaheadDelay(ms) {
  try { localStorage.setItem(KEY, String(ms)); } catch (e) { /* noop */ }
  window.dispatchEvent(new CustomEvent("typeahead-delay", { detail: ms }));
}

/** 같은 질의 재요청 방지용 초단기 캐시(질의당 TTL). 결과가 자주 바뀌는 목록이라 짧게. */
function makeCache(ttl) {
  const m = new Map();
  return {
    get(k) {
      const e = m.get(k);
      if (!e) return undefined;
      if (Date.now() - e.t > ttl) { m.delete(k); return undefined; }
      return e.v;
    },
    set(k, v) {
      m.set(k, { v, t: Date.now() });
      if (m.size > 40) m.delete(m.keys().next().value);   // 오래된 것부터 버린다
    },
    clear() { m.clear(); },
  };
}

/**
 * 자동완성 러너.
 *   fetcher(query) -> Promise<결과>
 *   opts.minLen  최소 글자수(그보다 짧으면 부르지 않고 onEmpty). 빈 문자열은 별도 취급(allowEmpty).
 *   opts.allowEmpty  빈 질의도 의미가 있는 경우(멘션 팝업: 티켓 관련 사람 우선 표시)
 *   opts.cacheMs 같은 질의 캐시 TTL(0이면 캐시 없음)
 *   opts.shouldCache(r)  이 결과를 캐시해도 되는가(기본: 전부). 빈/에러 결과를 캐시하면
 *                        순간적으로 0건이 온 질의가 그 TTL 동안 계속 0건으로 뜬다
 *                        (같은 검색어인데 어떨 땐 뜨고 어떨 땐 안 뜨는 그것).
 * 반환: run(query) -> Promise<결과|null>. null 은 '이 호출은 낡았으니 무시하라'는 뜻.
 */
export function createTypeahead(fetcher, opts) {
  const o = opts || {};
  const minLen = o.minLen == null ? 2 : o.minLen;
  const cache = o.cacheMs === 0 ? null : makeCache(o.cacheMs || 15000);
  let timer = null;
  let seq = 0;

  function cancel() { clearTimeout(timer); timer = null; seq++; }
  function clear() { cancel(); if (cache) cache.clear(); }

  function run(query) {
    const q = (query || "").trim();
    const my = ++seq;
    clearTimeout(timer);
    if (!q && !o.allowEmpty) return Promise.resolve(o.emptyValue === undefined ? [] : o.emptyValue);
    if (q && q.length < minLen) return Promise.resolve(o.emptyValue === undefined ? [] : o.emptyValue);
    const hit = cache && cache.get(q);
    if (hit !== undefined) return Promise.resolve(hit);      // 캐시 히트는 지연 없이 즉시
    return new Promise((resolve) => {
      timer = setTimeout(() => {
        Promise.resolve(fetcher(q)).then(
          (r) => {
            // 빈/에러 결과는 캐시하지 않는다 — 순간 실패로 0건이 온 질의가 TTL 동안 굳으면
            // 같은 검색어로 돌아와도 계속 0건이다(사용자가 겪은 깜빡임).
            if (cache && r != null && (!o.shouldCache || o.shouldCache(r))) cache.set(q, r);
            resolve(my === seq ? r : null);                  // 낡은 응답은 null → 호출부가 버린다
          },
          () => resolve(my === seq ? o.errorValue : null),
        );
      }, typeaheadDelay());
    });
  }

  return { run, cancel, clear };
}

/**
 * TipTap suggestion(@멘션)용 — items() 는 반드시 Promise 를 해소해야 하므로,
 * 대기 중이던 호출을 **최신 결과로 한꺼번에** 해소한다(취소해 방치하면 팝업이 멎는다).
 */
export function debouncedItems(fetcher) {
  let timer = null, waiters = [], lastQ = null, lastR = [];
  return (query) => new Promise((resolve) => {
    const q = query || "";
    if (q === lastQ) { resolve(lastR); return; }             // 같은 질의는 즉시(요청 없음)
    waiters.push(resolve);
    clearTimeout(timer);
    timer = setTimeout(() => {
      Promise.resolve(fetcher(q)).then((r) => r || [], () => []).then((r) => {
        lastQ = q; lastR = r;
        const ws = waiters; waiters = [];
        ws.forEach((w) => w(r));
      });
    }, typeaheadDelay());
  });
}

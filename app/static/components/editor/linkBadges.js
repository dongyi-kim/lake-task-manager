// Jira/document link normalization and the atomic rich-link TipTap node.
import { api } from "../../lib/api.js";
import { typeLabel, TYPE_BG } from "../../lib/colors.js";

// 웹 링크 뱃지 — **원자적 inline 노드**(atom). 글자 단위로 커서가 들어가지 않고 한 덩어리로 다뤄진다.
// 더블클릭하면 제목/URL 을 고칠 수 있고, URL 이 바뀌면 favicon(--fav)도 즉시 갱신된다.
// 저장은 <a href>제목</a> → wiki [제목|url] (html_to_wiki 가 그대로 처리).
function favCss(href) {
  return /^https?:/i.test(href) ? "url('/api/favicon?u=" + encodeURIComponent(href) + "')" : "";
}

// Jira 티켓 링크 판별 + 티켓 요약 조회(세션 캐시) — 에디터 뱃지를 읽기 렌더와 같게 그리기 위해.
function jiraKeyOf(href) {
  const m = /\/browse\/([A-Za-z][A-Za-z0-9]*-\d+)/.exec(href || "");
  return m ? m[1].toUpperCase() : null;
}
const _tkCache = new Map();
function ticketData(key) {
  if (_tkCache.has(key)) return Promise.resolve(_tkCache.get(key));
  return api.ticketBadge(key)
    .then((b) => { _tkCache.set(key, b); return b; })
    .catch(() => null);
}

export function linkBadgeExt(T) {
  return T.Node.create({
    name: "linkBadge",
    group: "inline",
    inline: true,
    atom: true,                     // 한 덩어리 — 내부로 커서가 들어가지 않는다
    selectable: true,
    addAttributes() {
      return { href: { default: "" }, title: { default: "" } };
    },
    parseHTML() {
      return [{
        tag: "a[href]",
        getAttrs: (el) => ({
          href: el.getAttribute("href") || "",
          title: (el.textContent || "").trim(),
        }),
      }];
    },
    // atom 이라 기본적으로 getText() 에 안 잡힌다 → 뱃지만 있는 댓글이 '내용 없음'으로 오판되던 문제.
    renderText({ node }) { return node.attrs.title || node.attrs.href || ""; },
    renderHTML({ node }) {
      const href = node.attrs.href || "";
      const attrs = { href, class: jiraKeyOf(href) ? "web-badge jira-link-explicit" : "web-badge",
                      rel: "noopener" };
      const fav = favCss(href);
      if (fav) attrs.style = "--fav:" + fav;
      return ["a", attrs, node.attrs.title || href];
    },
    addNodeView() {
      return ({ node, editor, getPos }) => {
        let cur = node;
        const a = document.createElement("a");
        const paint = (n) => {
          const href = n.attrs.href || "";
          a.setAttribute("href", href);
          a.setAttribute("rel", "noopener");
          a.title = href + "  (더블클릭: 제목·링크 수정)";
          const key = jiraKeyOf(href);
          if (key) {
            // Jira 티켓 — 읽기 렌더(augmentLinks)와 **같은 구조·클래스**로 그려 모양을 일치시킨다.
            // 링크를 붙여넣거나 '/jira'로 넣은 노드이므로 기본은 Detailed다. Short는 읽기 화면이
            // 일반 텍스트의 단순 티켓 번호를 자동 링크로 받은 경우에만 쓴다.
            a.className = "jira-badge jira-badge-detail tkt";
            a.style.removeProperty("--fav");
            a.innerHTML = '<span class="tbadge v-solid jb-type"></span><b class="jb-key"></b>'
              + '<span class="jb-name"></span><span class="jb-meta"></span>';
            a.querySelector(".jb-key").textContent = key;
            ticketData(key).then((bd) => {
              if (!bd || !a.isConnected) return;
              const tb = a.querySelector(".jb-type"), nm = a.querySelector(".jb-name"),
                    mt = a.querySelector(".jb-meta");
              if (!tb || !nm || !mt) return;
              const cat = bd.statusCategory || "todo";
              tb.textContent = typeLabel(bd.type || "");
              tb.style.setProperty("--tc", TYPE_BG[bd.type] || "var(--ty-task)");
              nm.textContent = bd.summary || "";
              mt.textContent = bd.status || "";            // 담당자는 표시하지 않는다
              mt.className = "jb-meta st-" + cat;
            });
            return;
          }
          a.className = "web-badge";
          a.textContent = n.attrs.title || href;
          const fav = favCss(href);
          if (fav) a.style.setProperty("--fav", fav);
          else a.style.removeProperty("--fav");
        };
        paint(cur);
        a.addEventListener("click", (e) => e.preventDefault());     // 편집 중 이동 방지
        a.addEventListener("dblclick", (e) => {
          e.preventDefault(); e.stopPropagation();
          openBadgeEditor(a, cur.attrs, (title, href) => {
            if (typeof getPos !== "function") return;
            editor.chain().focus().command(({ tr }) => {
              tr.setNodeMarkup(getPos(), undefined, { href, title });   // href 바뀌면 favicon 도 갱신
              return true;
            }).run();
          }, () => {
            // 언링크 — 뱃지를 지우고 **일반 텍스트**로 바꾼다(제목이 있으면 제목, 없으면 URL).
            if (typeof getPos !== "function") return;
            const pos = getPos();
            const text = cur.attrs.title || cur.attrs.href || "";
            editor.chain().focus().command(({ tr, state }) => {
              tr.replaceWith(pos, pos + cur.nodeSize, text ? state.schema.text(text) : []);
              return true;
            }).run();
          });
        });
        return {
          dom: a,
          update: (n) => { if (n.type !== cur.type) return false; cur = n; paint(n); return true; },
          ignoreMutation: () => true,
        };
      };
    },
  });
}

// 뱃지 편집 패널 — 제목과 URL 을 **한 화면에서** 고친다.
// (prompt 를 두 번 연달아 띄우면 브라우저가 '추가 대화상자 차단'으로 두 번째를 막아버린다.)
export function openBadgeEditor(anchor, attrs, onSave, onUnlink) {
  document.querySelectorAll(".badge-edit").forEach((e) => e.remove());
  const box = document.createElement("div");
  box.className = "badge-edit";
  box.innerHTML =
    '<label>표시할 제목</label><input class="be-t" type="text">' +
    '<label>링크 URL</label><input class="be-h" type="text">' +
    '<div class="be-row"><button type="button" class="be-unlink" title="뱃지를 없애고 일반 텍스트로 바꿉니다">언링크</button>' +
    '<button type="button" class="be-cancel">취소</button>' +
    '<button type="button" class="be-ok">저장</button></div>';
  document.body.appendChild(box);
  const t = box.querySelector(".be-t"), h = box.querySelector(".be-h");
  t.value = attrs.title || "";
  h.value = attrs.href || "";
  // 화면 안에 들어오게 — 아래로 넘치면 앵커 위로 뒤집는다(에디터가 화면 하단에 있을 때가 많다).
  const r = anchor.getBoundingClientRect();
  const bh = box.offsetHeight || 180;
  let top = r.bottom + 6;
  if (top + bh > window.innerHeight - 8) top = Math.max(8, r.top - bh - 6);
  box.style.left = Math.round(Math.max(8, Math.min(r.left, window.innerWidth - 340))) + "px";
  box.style.top = Math.round(top) + "px";
  const close = () => { box.remove(); document.removeEventListener("mousedown", onDoc, true); };
  const onDoc = (ev) => { if (!box.contains(ev.target)) close(); };
  setTimeout(() => document.addEventListener("mousedown", onDoc, true), 0);
  const save = () => {
    const href = (h.value || "").trim();
    if (!href) { h.focus(); return; }
    onSave((t.value || "").trim() || href, href);
    close();
  };
  box.querySelector(".be-cancel").addEventListener("click", close);
  box.querySelector(".be-ok").addEventListener("click", save);
  const ul = box.querySelector(".be-unlink");
  if (onUnlink) ul.addEventListener("click", () => { onUnlink(); close(); });
  else ul.style.display = "none";
  box.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); save(); }
    else if (ev.key === "Escape") { ev.preventDefault(); close(); }
  });
  t.focus(); t.select();
}

// 뱃지 제목 교체 — href 가 같고 제목이 아직 expect(기본=href) 인 뱃지만 바꾼다(사용자가 고친 건 유지).
export function updateBadgeTitle(editor, href, title, expect) {
  if (!editor || editor.isDestroyed) return;
  const want = expect === undefined ? href : expect;
  let at = null;
  editor.state.doc.descendants((node, pos) => {
    if (at !== null) return false;
    if (node.type.name === "linkBadge" && node.attrs.href === href && node.attrs.title === want) at = pos;
  });
  if (at === null) return;
  editor.view.dispatch(editor.state.tr.setNodeMarkup(at, undefined, { href, title }));
}

// 앱 화면 이름 — app-root 의 해시 라우트/nav 라벨과 맞춘다(기본 라우트는 wbs).
const _APP_PAGES = {
  wbs: "WBS Dashboard", vit: "현안 (PMO_VIT)", workload: "인력 워크로드", devtools: "Dev Tools",
};

// 실 Jira 주소 — 모듈 단위로 **한 번만** 조회해 캐시한다. 에디터 인스턴스마다 받으면
// 붙여넣기가 응답보다 빨랐을 때 앱 주소(localhost)가 그대로 저장된다(다른 사람에겐 못 여는 링크).
let _jiraBase = "";
let _jiraBaseReq = null;
export function jiraBase() {
  if (!_jiraBaseReq) {
    _jiraBaseReq = api.health()
      .then((h) => { _jiraBase = (h && h.jiraBase) || ""; return _jiraBase; })
      .catch(() => "");
  }
  return _jiraBaseReq;
}

// 이 URL 이 '우리 앱' 주소인가. 로컬 1인 앱이라 같은 앱을 localhost/127.0.0.1 어느 쪽으로도 연다
// → origin 문자열만 비교하면 별칭으로 연 주소를 남의 사이트로 오해한다(포트가 같으면 우리 앱).
const _LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);
function isAppOrigin(u) {
  if (u.origin === location.origin) return true;
  return _LOOPBACK.has(u.hostname) && _LOOPBACK.has(location.hostname)
    && (u.port || "") === (location.port || "");
}

// 우리 앱 URL 을 붙여넣은 경우의 정규화. 외부 URL 이면 null.
//  · {앱}/browse/KEY  → **실 Jira 티켓 주소**(그게 정본) + 제목은 티켓 키
//  · 그 외 앱 URL     → 'Lake Task Manager - {화면 이름}' 뱃지 (localhost 링크로 보이지 않게)
export function normalizeAppUrl(url, jiraBase = _jiraBase) {
  try {
    const u = new URL(url, location.href);
    // /browse/KEY 는 우리 앱 주소든 실 Jira 주소든 **티켓**으로 취급한다.
    const m = /^\/browse\/([A-Za-z][A-Za-z0-9]*-\d+)/.exec(u.pathname);
    if (m) {
      const key = m[1].toUpperCase();
      // 우리 앱 주소면 실 Jira 로 바꾼다(그게 정본). 이미 Jira 주소면 그대로 둔다.
      const base = (isAppOrigin(u) && jiraBase) ? jiraBase.replace(/\/+$/, "") : u.origin;
      return { href: base + "/browse/" + key, title: key, key };
    }
    if (!isAppOrigin(u)) return null;                   // 외부 일반 URL → og:title 조회
    const route = (u.hash || "").replace(/^#\/?/, "").split(/[?/]/)[0] || "wbs";
    const page = _APP_PAGES[route] || route || "";
    return { href: url, title: "Lake Task Manager" + (page ? " - " + page : "") };
  } catch (e) { return null; }
}

// Confluence 문서 URL 의 **슬러그**에서 제목을 뽑는다 — 백엔드(og:title/문서조회) 응답 전에
// 즉시 라벨로 쓰려는 것. 신형 /pages/{id}/{slug}, 구형 /display/{space}/{slug}.
// pathname 만 보므로 #heading 앵커·?쿼리는 자동으로 빠진다(그게 raw url 로 새던 버그의 방지책).
export function confTitleFromUrl(u) {
  try {
    const path = new URL(u, location.href).pathname;
    const m = path.match(/\/pages\/\d+\/([^/]+)\/?$/) || path.match(/\/display\/[^/]+\/([^/]+)\/?$/);
    if (m && m[1]) return decodeURIComponent(m[1].replace(/\+/g, " ")).trim();
  } catch (e) { /* noop */ }
  return null;
}

// 티켓 뱃지 라벨 — [타입] [번호] [제목] - [상태]
export function ticketLabel(key, bd) {
  const parts = [];
  if (bd && bd.type) parts.push(bd.type);
  parts.push(key);
  if (bd && bd.summary) parts.push(bd.summary);
  let s = parts.join(" ");
  if (bd && bd.status) s += " - " + bd.status;
  return s;
}

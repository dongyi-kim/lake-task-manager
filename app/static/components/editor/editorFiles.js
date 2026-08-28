// Attachment lifecycle and the TipTap nodes used for files and resizable images.
import { extOf } from "../../lib/filetype.js";

// 첨부 업로드 재시도 — prod 는 SSO 세션/사내망 탓에 첨부가 간헐적으로 삐끗한다. 한 번 실패했다고
// 파일을 버리지 않고 최대 이만큼 **다시** 올려 본다(총 시도 횟수).
export const UPLOAD_TRIES = 3;
export function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

// 끝내 못 올린 파일의 본문 참조(blob objectURL)를 통째로 걷어낸다 — 안 지우면 저장된 본문에
// 죽은 blob: 링크나 깨진 이미지가 남는다. 이미지 <img>·파일 뱃지 <a> 를 태그째 제거한다.
export function stripPendingRef(html, url) {
  const u = url.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return (html || "")
    .replace(new RegExp('<img\\b[^>]*\\bsrc="' + u + '"[^>]*>', "gi"), "")
    .replace(new RegExp('<a\\b[^>]*\\bhref="' + u + '"[^>]*>.*?<\\/a>', "gi"), "")
    .split(url).join("");                 // 혹시 남은 raw 참조까지
}

// 이미지 삽입 시 세로가 너무 길지 않도록 기본 높이 상한(px). 원본이 이보다 작으면 원본 유지.
const IMG_MAX_H = 320;

// 이미지의 자연 크기를 재서, 높이가 상한을 넘으면 비율 유지한 width(px)를 돌려준다(아니면 null).
export function fitWidth(url) {
  return new Promise((resolve) => {
    const im = new Image();
    im.onload = () => {
      const h = im.naturalHeight || 0, w = im.naturalWidth || 0;
      resolve(h > IMG_MAX_H && w ? Math.round((w * IMG_MAX_H) / h) : null);
    };
    im.onerror = () => resolve(null);
    im.src = url;
  });
}

// 크기 조절 가능한 이미지 — width 속성(→ wiki !파일|width=N!) + 모서리 드래그 핸들 NodeView.
export function imageResizeExt(T) {
  return T.Image.extend({
    addAttributes() {
      const parent = this.parent ? this.parent() : {};
      return Object.assign({}, parent, {
        width: {
          default: null,
          parseHTML: (el) => el.getAttribute("width") || null,
          renderHTML: (attrs) => (attrs.width ? { width: attrs.width } : {}),
        },
      });
    },
    addNodeView() {
      return ({ node, editor, getPos }) => {
        const wrap = document.createElement("span");
        wrap.className = "img-wrap";
        const img = document.createElement("img");
        img.src = node.attrs.src;
        if (node.attrs.alt) img.alt = node.attrs.alt;
        if (node.attrs.width) img.setAttribute("width", node.attrs.width);
        wrap.appendChild(img);
        const handle = document.createElement("span");
        handle.className = "img-resize";
        handle.title = "드래그해서 크기 조절";
        wrap.appendChild(handle);
        handle.addEventListener("mousedown", (e) => {
          e.preventDefault(); e.stopPropagation();
          const startX = e.clientX, startW = img.getBoundingClientRect().width;
          const move = (ev) => {
            img.setAttribute("width", String(Math.max(48, Math.round(startW + ev.clientX - startX))));
          };
          const up = () => {
            document.removeEventListener("mousemove", move);
            document.removeEventListener("mouseup", up);
            const w = parseInt(img.getAttribute("width") || "0", 10);
            if (w && typeof getPos === "function") {
              editor.chain().focus().command(({ tr }) => {
                tr.setNodeMarkup(getPos(), undefined, Object.assign({}, node.attrs, { width: w }));
                return true;
              }).run();
            }
          };
          document.addEventListener("mousemove", move);
          document.addEventListener("mouseup", up);
        });
        return {
          dom: wrap,
          ignoreMutation: () => true,
          update: (n) => {
            if (n.type !== node.type) return false;
            img.src = n.attrs.src;
            if (n.attrs.width) img.setAttribute("width", n.attrs.width);
            else img.removeAttribute("width");
            return true;
          },
        };
      };
    },
  });
}

export function fileBadgeExt(T) {
  return T.Node.create({
    name: "fileBadge",
    group: "inline",
    inline: true,
    atom: true,                     // 한 덩어리 — 내부로 커서가 들어가지 않는다
    selectable: true,
    addAttributes() {
      return { href: { default: "" }, name: { default: "" }, size: { default: 0 } };
    },
    parseHTML() {
      return [{ tag: "a.file-badge", getAttrs: (el) => ({
        href: el.getAttribute("href") || "",
        name: el.getAttribute("data-file") || (el.textContent || "").trim(),
      }) }];
    },
    // atom 은 기본적으로 getText() 에 안 잡힌다 → 파일만 넣은 댓글이 '내용 없음' 으로 오판된다.
    renderText({ node }) { return node.attrs.name || ""; },
    renderHTML({ node }) {
      return ["a", { href: node.attrs.href || node.attrs.name || "",
                     class: "file-badge", "data-file": node.attrs.name || "",
                     "data-ext": extOf(node.attrs.name), rel: "noopener" },
              node.attrs.name || ""];
    },
    addNodeView() {
      return ({ node }) => {
        const a = document.createElement("a");
        a.className = "file-badge";
        a.setAttribute("data-file", node.attrs.name || "");
        a.setAttribute("href", node.attrs.href || "");
        a.setAttribute("rel", "noopener");
        a.title = node.attrs.name + (node.attrs.size ? "  (" + fmtSize(node.attrs.size) + ")" : "");
        // 아이콘·색은 filebadge.css 한 곳이 data-ext 로 정한다 — 서버가 렌더한 코멘트와 같은
        // 규칙이라야 작성 중 화면과 등록 뒤 화면이 같아 보인다.
        a.setAttribute("data-ext", extOf(node.attrs.name));
        a.innerHTML = '<i class="fb-ext"></i><span class="fb-n"></span>';
        a.querySelector(".fb-n").textContent = node.attrs.name || "";
        // 편집 중에는 링크를 따라가지 않는다 — 아직 올라가지도 않은 파일이다.
        a.addEventListener("click", (e) => e.preventDefault());
        return { dom: a };
      };
    },
  });
}

export function fmtSize(n) {
  if (!n) return "";
  if (n < 1024) return n + "B";
  if (n < 1024 * 1024) return Math.round(n / 1024) + "KB";
  return (n / 1024 / 1024).toFixed(1) + "MB";
}

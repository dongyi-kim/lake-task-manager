"""Milkdown Crepe CSS 를 @import 전부 해소한 **자체완결 파일**로 벤더링.

Crepe 의 배포 CSS 는 `@import '@milkdown/kit/...'` / `@import 'katex/...'` 같은 node_modules
경로를 담아 번들러를 전제한다. raw <link> 로 로드하면 그 경로가 CDN 에 없어 HTML 404 → ORB
차단 → 코어(prosemirror/table/cursor) CSS 누락 → 렌더 깨짐. 그래서 @import 를 재귀적으로
해소(의존은 jsdelivr npm 에서 fetch)하고 url() 을 절대경로로 바꿔 한 파일로 인라인한다.

latex/katex 는 쓰지 않으므로 드롭(폰트 다수 회피). 결과:
  app/static/vendor/crepe-light.css  (common + frame 라이트)
  app/static/vendor/crepe-dark.css   (common + frame-dark)

재생성:  python tools/build_crepe_css.py
"""
import re
import sys
import urllib.request
from pathlib import Path

VER = "7.21.2"
CDN = "https://cdn.jsdelivr.net/npm"
CREPE = f"{CDN}/@milkdown/crepe@{VER}/lib/theme"
OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "vendor"

_IMPORT_RE = re.compile(r"""@import\s+(?:url\(\s*)?['"]?([^'")]+)['"]?\s*\)?\s*;""")
_URL_RE = re.compile(r"""url\(\s*['"]?([^'")]+)['"]?\s*\)""")

_cache = {}


def fetch(url):
    if url in _cache:
        return _cache[url]
    req = urllib.request.Request(url, headers={"User-Agent": "crepe-css-builder"})
    with urllib.request.urlopen(req, timeout=30) as r:
        txt = r.read().decode("utf-8", "replace")
    _cache[url] = txt
    return txt


def resolve_spec(spec, base_url):
    """@import 대상 spec → 절대 URL. 상대(./x) 는 base 기준, bare 는 jsdelivr npm."""
    if spec.startswith(("http://", "https://")):
        return spec
    if spec.startswith(("./", "../")):
        base_dir = base_url.rsplit("/", 1)[0]
        # ../ 처리
        parts = (base_dir + "/" + spec).split("/")
        out = []
        for p in parts:
            if p == "..":
                out.pop()
            elif p in ("", "."):
                continue
            else:
                out.append(p)
        # 스킴 복원 (https:)
        return out[0] + "//" + "/".join(out[1:]) if out[0].endswith(":") else "/".join(out)
    # @milkdown/kit 은 prosemirror-* 를 재수출 — 코어 CSS 는 원본 패키지에 있다(kit 경로엔 404).
    m = re.match(r"@milkdown/kit/prose/(view|gapcursor|tables)/style/(.+)$", spec)
    if m:
        spec = f"prosemirror-{m.group(1)}/style/{m.group(2)}"
    # bare specifier → npm
    return f"{CDN}/{spec}"


def absolutize_urls(css, base_url):
    """url(...) 상대경로 → 절대(jsdelivr). data:/절대/#앵커 는 그대로."""
    base_dir = base_url.rsplit("/", 1)[0]

    def repl(m):
        u = m.group(1).strip()
        if u.startswith(("data:", "http://", "https://", "#")):
            return m.group(0)
        if u.startswith("/"):
            return m.group(0)
        # 상대 → base_dir 기준 절대
        parts = (base_dir + "/" + u).split("/")
        out = []
        for p in parts:
            if p == "..":
                out.pop()
            elif p in ("", "."):
                continue
            else:
                out.append(p)
        abs_u = out[0] + "//" + "/".join(out[1:]) if out[0].endswith(":") else "/".join(out)
        return f"url('{abs_u}')"

    return _URL_RE.sub(repl, css)


def inline(url, seen):
    if url in seen:
        return ""
    seen.add(url)
    if "katex" in url or "latex" in url:      # latex 미사용 → 드롭(폰트 회피)
        return f"/* dropped: {url} */\n"
    try:
        css = fetch(url)
    except Exception as e:
        print(f"  WARN skip {url}: {e}", file=sys.stderr)
        return f"/* skip(fetch fail): {url} */\n"
    out = []

    def sub_import(m):
        spec = m.group(1).strip()
        target = resolve_spec(spec, url)
        return inline(target, seen)

    body = _IMPORT_RE.sub(sub_import, css)
    body = absolutize_urls(body, url)
    out.append(f"/* --- {url} --- */\n")
    out.append(body)
    return "".join(out)


def build(theme_file):
    seen = set()
    common = inline(f"{CREPE}/common/style.css", seen)
    theme = inline(f"{CREPE}/{theme_file}/style.css", seen)
    return common + "\n" + theme


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, theme in [("crepe-light.css", "frame"), ("crepe-dark.css", "frame-dark")]:
        print(f"building {name} (theme={theme}) …")
        css = build(theme)
        (OUT / name).write_text(css, encoding="utf-8")
        print(f"  -> {OUT / name}  ({len(css)} bytes, @import left: {css.count('@import')})")


if __name__ == "__main__":
    main()

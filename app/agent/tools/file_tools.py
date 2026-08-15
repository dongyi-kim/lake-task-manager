"""agent/tools/file_tools.py — 티켓에 붙은 **첨부파일**을 인지하고, 필요하면 읽는다.

**① 목록은 늘 본다.** 어떤 파일이 붙어 있는지(이름·확장자·크기·올린 사람)는 그 자체가
맥락이다 — "재현 로그 첨부했습니다" 라는 코멘트 옆에 `error.log 12KB` 가 있는 것과 없는
것은 판단이 다르다. 목록은 싸다(이미 티켓 조회에 실려 온다).

**② 내용은 종류에 맞게 읽는다.** "읽는다"가 파일마다 다른 일이다:

  · **글**(log/txt/md/설정) 과 **소스코드**(sql/py/js/java/sh/go/…) → 앞부분을 읽는다.
    찾는 말이 있으면 그 줄과 앞뒤 두 줄만 — 3만 줄 로그를 통째로 실을 이유가 없다
  · **표**(csv/tsv/xlsx) → **컬럼이 무엇인지**와 **찾는 대상과 관련된 행**만. 표를 통째로
    프롬프트에 부으면 토큰도 판단도 망가진다
  · **트리**(json/yaml/ndjson) → 키 구조와 일치하는 요소만
  · **문서**(pdf/docx) → 본문 텍스트. 스캔 이미지 PDF 는 글자가 없으므로 그렇게 말한다
  · **parquet** → pyarrow 가 있으면 스키마·관련 행, 없으면 무엇이 필요한지 말한다
  · 이미지·동영상·압축·구버전 오피스 → **읽지 않는다.** 붙어 있다는 사실만 말하고,
    내용이 필요하면 사람에게 요청한다(추측으로 내용을 지어내는 것이 가장 나쁘다)

엑셀·워드는 zip+XML 이라 **표준 라이브러리로** 연다 — 이것 때문에 배포 무게를 늘리지 않는다.
PDF 는 이미 들어와 있는 pypdf 를 쓰고, parquet 만 선택 의존(pyarrow)이다.
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile

from langchain_core.tools import tool

from app.agent.tools._ctx import client, compact, trim

# 상한은 종류별로 다르다 — 글은 앞부분만 읽으면 되고, 표는 훑어야 컬럼·행을 뽑는다.
MAX_TEXT_BYTES = 2 * 1024 * 1024      # 글(로그·마크다운·설정): 2MB
MAX_TABLE_BYTES = 8 * 1024 * 1024     # 표·트리(csv/tsv/xlsx/json/yaml): 8MB
MAX_DOC_BYTES = 12 * 1024 * 1024      # 문서(docx/pdf): 페이지가 많아도 앞부분만 읽는다
MAX_TEXT = 6000                       # 프롬프트에 실을 글자 수
MAX_ROWS_SCAN = 50000                 # 훑을 행 수 상한(그 이상은 앞부분만 봤다고 말한다)
MAX_ROWS_OUT = 25                     # 돌려줄 행 수

# ── 무엇을 어떻게 읽나 ──────────────────────────────────────────────
# 글(코드 포함)·표·트리·문서·불투명. 종류가 정해지면 읽는 방식과 상한이 따라온다.
CODE_EXT = {"sql", "py", "js", "ts", "tsx", "jsx", "java", "kt", "scala", "go", "rs",
            "c", "h", "cpp", "hpp", "cs", "rb", "php", "swift", "m", "r", "pl",
            "sh", "bash", "zsh", "bat", "ps1", "gradle", "tf", "hcl", "dockerfile",
            "makefile", "cmake", "proto", "graphql", "vue", "svelte", "css", "scss"}
DOCTEXT_EXT = {"txt", "log", "md", "markdown", "rst", "adoc", "text",
               "ini", "cfg", "conf", "properties", "env", "toml", "html", "htm", "xml"}
TEXT_EXT = CODE_EXT | DOCTEXT_EXT
TABLE_EXT = {"csv", "tsv", "xlsx", "xlsm"}
TREE_EXT = {"json", "yaml", "yml", "ndjson", "jsonl"}
# 라이브러리가 있어야 읽히는 것 — 없으면 "무엇을 설치하면 되는지"까지 말한다
BINDOC_EXT = {"docx", "pdf"}
COLUMNAR_EXT = {"parquet", "pq"}
# 끝내 읽지 않는다 — 붙어 있다는 사실만. 추측으로 내용을 지어내는 것이 가장 나쁘다.
OPAQUE = {
    "png": "이미지", "jpg": "이미지", "jpeg": "이미지", "gif": "이미지", "bmp": "이미지",
    "webp": "이미지", "svg": "이미지", "heic": "이미지", "ico": "이미지",
    "mp4": "동영상", "mov": "동영상", "avi": "동영상", "mkv": "동영상", "webm": "동영상",
    "mp3": "음성", "wav": "음성", "m4a": "음성",
    "zip": "압축", "7z": "압축", "tar": "압축", "gz": "압축", "rar": "압축", "tgz": "압축",
    "ppt": "슬라이드", "pptx": "슬라이드", "doc": "구버전 워드", "hwp": "한글 문서",
    "hwpx": "한글 문서", "xls": "구버전 엑셀",
    "exe": "실행 파일", "dll": "바이너리", "bin": "바이너리", "jar": "바이너리",
    "so": "바이너리", "dmg": "바이너리", "iso": "이미지 파일",
}


def _ext(name: str) -> str:
    return (name or "").rsplit(".", 1)[-1].lower() if "." in (name or "") else ""


def _kb(n: int) -> str:
    n = int(n or 0)
    return f"{n / 1024 / 1024:.1f}MB" if n >= 1024 * 1024 else f"{max(1, n // 1024)}KB"


def _cap(ext: str) -> int:
    if ext in TABLE_EXT or ext in TREE_EXT or ext in COLUMNAR_EXT:
        return MAX_TABLE_BYTES
    if ext in BINDOC_EXT:
        return MAX_DOC_BYTES
    return MAX_TEXT_BYTES


def _kind(name: str, mime: str = "") -> str:
    e = _ext(name)
    if e in OPAQUE:
        return f"{OPAQUE[e]}(내용은 읽을 수 없음)"
    if (mime or "").startswith("image/"):
        return "이미지(내용은 읽을 수 없음)"
    if e in TABLE_EXT:
        return "표 데이터(컬럼·관련 행 조회 가능)"
    if e in COLUMNAR_EXT:
        return "컬럼형 데이터(parquet — 스키마·관련 행 조회)"
    if e in TREE_EXT:
        return "구조화 데이터(키·관련 요소 조회 가능)"
    if e in BINDOC_EXT:
        return {"pdf": "PDF 문서(본문 추출 가능)", "docx": "워드 문서(본문 추출 가능)"}[e]
    if e in CODE_EXT:
        return f"소스코드({e})"
    if e in DOCTEXT_EXT:
        return {"log": "로그", "md": "문서(마크다운)", "markdown": "문서(마크다운)",
                "xml": "구조화 텍스트"}.get(e, "텍스트")
    return f"{e or '확장자 없음'} 파일(내용은 읽을 수 없음)"


def _supported(ext: str) -> bool:
    return (ext in TEXT_EXT or ext in TABLE_EXT or ext in TREE_EXT
            or ext in BINDOC_EXT or ext in COLUMNAR_EXT)


def _readable(name: str, size: int) -> bool:
    e = _ext(name)
    if e in OPAQUE or not _supported(e):
        return False
    return 0 < int(size or 0) <= _cap(e)


@tool
def list_attachments(ticket_key: str) -> dict:
    """List files attached to one verified ticket, including name, kind, size, author, creation date, and readability.

    Use this before claiming that a referenced file exists or before calling `read_attachment`. Returns
    `{"key", "files": [{"id", "name", "kind", "size", "author", "created", "readable"}]}`.
    A file listing is evidence of presence only, never evidence of its content.
    """
    key = (ticket_key or "").strip().upper()
    try:
        rows = client().ticket_attachments(key) or []
    except Exception as e:
        return {"error": str(e)[:200]}
    files = []
    for a in rows:
        name, size = a.get("filename") or "", int(a.get("size") or 0)
        files.append(compact({
            "id": a.get("id"), "name": name, "kind": _kind(name, a.get("mime") or ""),
            "size": _kb(size), "author": a.get("author"),
            "created": str(a.get("created") or "")[:10],
            "readable": _readable(name, size),
        }))
    return {"key": key, "files": files,
            "note": ("readable=true 인 것만 read_attachment 로 열 수 있다. 표·구조화 데이터는 "
                     "find 인자로 찾는 대상을 주면 관련 행·요소만 뽑아 준다. 이미지·동영상·"
                     "압축·PDF·워드는 붙어 있다는 사실만 말하고, 내용이 필요하면 사람에게 "
                     "요청하라 — 열지 못한 파일의 내용을 지어내지 마라."
                     if files else "첨부파일이 없다.")}


@tool
def read_attachment(ticket_key: str, filename: str, find: str = "") -> dict:
    """Read a supported attachment from one verified ticket.

    Pass `find` for a table, error code, ID, or other target to return only matching rows, structured elements,
    or nearby text. Tables return columns and matched rows or a sample; JSON/YAML/NDJSON return keys and matched
    elements or a sample; text and source files return text; PDF and DOCX return extracted text. Only files marked
    `readable=true` by `list_attachments` can be opened. Never infer content from an unreadable file.
    """
    key, want = (ticket_key or "").strip().upper(), (filename or "").strip()
    try:
        rows = client().ticket_attachments(key) or []
    except Exception as e:
        return {"error": str(e)[:200]}
    hit = next((a for a in rows if (a.get("filename") or "") == want), None) \
        or next((a for a in rows if want.lower() in (a.get("filename") or "").lower()), None)
    if not hit:
        names = ", ".join(a.get("filename") or "" for a in rows[:8])
        return {"error": f"{key} 에 '{want}' 첨부가 없습니다." + (f" 있는 것: {names}" if names else "")}

    name, size = hit.get("filename") or "", int(hit.get("size") or 0)
    ext = _ext(name)
    if ext in OPAQUE or not _supported(ext):
        return {"error": f"'{name}' 은 {_kind(name)} 입니다 — 내용을 읽을 수 없습니다. "
                         "필요하면 사람에게 내용을 물어보세요(추측해서 쓰지 마세요)."}
    if size > _cap(ext):
        return {"error": f"'{name}' 은 {_kb(size)} 로 너무 큽니다(이 형식의 상한 {_kb(_cap(ext))}). "
                         "필요한 부분을 사람이 발췌해 주어야 합니다."}

    data = _fetch(key, hit)
    if data is None:
        return {"error": f"'{name}' 을 내려받지 못했습니다."}

    find = (find or "").strip()
    if ext in ("xlsx", "xlsm"):
        return _read_xlsx(name, size, data, find)
    if ext in ("csv", "tsv"):
        return _read_csv(name, size, data, find, delim="\t" if ext == "tsv" else ",")
    if ext in ("ndjson", "jsonl"):
        return _read_ndjson(name, size, data, find)
    if ext in TREE_EXT:
        return _read_tree(name, size, data, find, ext)
    if ext in COLUMNAR_EXT:
        return _read_parquet(name, size, data, find)
    if ext == "docx":
        return _read_docx(name, size, data, find)
    if ext == "pdf":
        return _read_pdf(name, size, data, find)
    return _read_text(name, size, data, find)


# ── 내려받기 ────────────────────────────────────────────────────────
def _fetch(key: str, hit: dict):
    """★ 목록의 `url` 은 화면용 **프록시 경로**(/api/file?u=…)다 — 그걸 그대로 받으려 하면
    404 본문을 파일 내용으로 착각한다(실측). 원본 content URL 은 이슈 필드에만 있고,
    절대 URL 은 SSRF 허용 호스트 검사에 걸리므로 **경로 형태**로 넘긴다(provider 가 base·
    인증을 붙여 mock/local/prod 가 같은 길을 탄다)."""
    try:
        from urllib.parse import urlparse
        c = client()
        raw = (c.get_issue(key).get("fields") or {}).get("attachment") or []
        src = next((a.get("content") for a in raw
                    if str(a.get("id") or "") == str(hit.get("id"))), "")
        path = urlparse(src).path if str(src).startswith(("http://", "https://")) else src
        data, _ctype = c.fetch_media(path or "")
        return data or None
    except Exception:
        return None


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "cp949"):     # 사내 로그·엑셀 CSV 는 종종 cp949 다
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return ""


def _match(needle: str, cells) -> bool:
    n = needle.lower()
    return any(n in str(c or "").lower() for c in cells)


# ── 표: 컬럼이 무엇이고, 찾는 대상과 관련된 행이 있나 ────────────────
def _table_result(name, size, columns, rows, find, total, truncated):
    out = {"name": name, "size": _kb(size), "columns": columns, "rows_total": total}
    if truncated:
        out["note"] = f"앞 {MAX_ROWS_SCAN:,}행만 훑었다 — 그보다 뒤는 확인하지 못했다."
    if find:
        hits = [r for r in rows if _match(find, r)]
        out["matched_count"] = len(hits)
        out["matched"] = [dict(zip(columns, r)) for r in hits[:MAX_ROWS_OUT]]
        if not hits:
            out["note"] = (out.get("note", "") + f" '{find}' 와 일치하는 행은 없다 — "
                           "컬럼 목록을 보고 다른 이름으로 다시 찾아보라.").strip()
    else:
        out["sample"] = [dict(zip(columns, r)) for r in rows[:3]]
        out["note"] = (out.get("note", "") + " find 인자에 찾는 대상을 주면 관련 행만 뽑는다.").strip()
    return compact(out)


def _read_csv(name, size, data, find, delim=","):
    text = _decode(data)
    if not text:
        return {"error": f"'{name}' 의 문자 인코딩을 해석하지 못했습니다."}
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    try:
        columns = [str(c).strip() for c in next(reader)]
    except StopIteration:
        return {"name": name, "size": _kb(size), "columns": [], "rows_total": 0,
                "note": "빈 파일이다."}
    rows, total = [], 0
    for r in reader:
        total += 1
        if total > MAX_ROWS_SCAN:
            break
        rows.append(r)
    return _table_result(name, size, columns, rows, find, total, total > MAX_ROWS_SCAN)


_XL_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _read_xlsx(name, size, data, find):
    """엑셀은 zip 안의 XML 이다 — 컬럼과 행을 뽑는 데는 표준 라이브러리로 충분하다.
    (openpyxl 을 넣지 않는 이유: 이 기능 하나 때문에 배포 무게를 늘릴 이유가 없다.)"""
    import xml.etree.ElementTree as ET
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception:
        return {"error": f"'{name}' 을 엑셀로 열지 못했습니다(손상되었거나 형식이 다릅니다)."}
    try:
        shared = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in si.iter(_XL_NS + "t")) for si in root]
        sheets = [n for n in zf.namelist() if n.startswith("xl/worksheets/sheet")]
        if not sheets:
            return {"error": f"'{name}' 에 시트가 없습니다."}
        root = ET.fromstring(zf.read(sorted(sheets)[0]))
    except Exception as e:
        return {"error": f"'{name}' 을 읽는 중 실패: {str(e)[:120]}"}

    grid, total, truncated = [], 0, False
    for row in root.iter(_XL_NS + "row"):
        total += 1
        if total > MAX_ROWS_SCAN:
            truncated = True
            break
        cells = []
        for c in row.iter(_XL_NS + "c"):
            v = c.find(_XL_NS + "v")
            txt = ""
            if c.get("t") == "s" and v is not None and (v.text or "").isdigit():
                idx = int(v.text)
                txt = shared[idx] if 0 <= idx < len(shared) else ""
            elif c.get("t") == "inlineStr":
                txt = "".join(t.text or "" for t in c.iter(_XL_NS + "t"))
            elif v is not None:
                txt = v.text or ""
            cells.append(txt)
        grid.append(cells)
    if not grid:
        return {"name": name, "size": _kb(size), "columns": [], "rows_total": 0,
                "note": "빈 시트다."}
    columns = [str(c).strip() for c in grid[0]]
    return _table_result(name, size, columns, grid[1:], find, max(0, total - 1), truncated)


# ── 트리: 키 구조와 관련 요소 ────────────────────────────────────────
def _read_tree(name, size, data, find, ext):
    text = _decode(data)
    if not text:
        return {"error": f"'{name}' 의 문자 인코딩을 해석하지 못했습니다."}
    try:
        if ext == "json":
            obj = json.loads(text)
        else:
            import yaml
            obj = yaml.safe_load(text)
    except Exception as e:
        return {"error": f"'{name}' 을 해석하지 못했습니다: {str(e)[:120]}"}

    out = {"name": name, "size": _kb(size)}
    if isinstance(obj, dict):
        out["keys"] = list(obj)[:40]
    elif isinstance(obj, list):
        out["keys"] = sorted({k for x in obj[:200] if isinstance(x, dict) for k in x})[:40]
        out["elements_total"] = len(obj)
    if find:
        hits = _find_in(obj, find)
        out["matched_count"] = len(hits)
        out["matched"] = [trim(json.dumps(h, ensure_ascii=False), 600) for h in hits[:MAX_ROWS_OUT]]
        if not hits:
            out["note"] = f"'{find}' 와 일치하는 요소는 없다 — 키 목록을 보고 다시 찾아보라."
    else:
        out["sample"] = trim(json.dumps(obj, ensure_ascii=False)[:1200], 1200)
        out["note"] = "find 인자에 찾는 대상을 주면 관련 요소만 뽑는다."
    return compact(out)


def _find_in(obj, needle, depth=0):
    """찾는 말이 들어간 **가장 작은 덩어리**를 모은다 — 뿌리째 돌려주면 요약이 안 된다."""
    n = needle.lower()
    hits = []
    if depth > 6:
        return hits
    if isinstance(obj, dict):
        if any(n in str(k).lower() or n in str(v).lower()
               for k, v in obj.items() if not isinstance(v, (dict, list))):
            return [obj]
        for v in obj.values():
            hits += _find_in(v, needle, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            hits += _find_in(v, needle, depth + 1)
    elif n in str(obj).lower():
        hits.append(obj)
    return hits


# ── 글(로그·코드·설정) ──────────────────────────────────────────────
def _read_text(name, size, data, find):
    text = _decode(data)
    if not text:
        return {"error": f"'{name}' 의 문자 인코딩을 해석하지 못했습니다."}
    return _text_result(name, size, text, find)


# ── 줄 단위 JSON(ndjson/jsonl) — 로그·이벤트 덤프의 기본 형식 ────────
def _read_ndjson(name, size, data, find):
    """한 줄에 객체 하나. 표처럼 **키 목록 + 관련 요소**로 다룬다(통째로 싣지 않는다)."""
    text = _decode(data)
    if not text:
        return {"error": f"'{name}' 의 문자 인코딩을 해석하지 못했습니다."}
    keys, hits, total, bad = [], [], 0, 0
    seen = set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        total += 1
        if total > MAX_ROWS_SCAN:
            break
        try:
            obj = json.loads(line)
        except Exception:
            bad += 1
            continue
        if isinstance(obj, dict):
            for k in obj:
                if k not in seen:
                    seen.add(k)
                    keys.append(k)
        if find and find.lower() in line.lower():
            hits.append(obj)
    out = {"name": name, "size": _kb(size), "keys": keys[:40], "elements_total": total}
    if bad:
        out["note"] = f"JSON 으로 못 읽은 줄 {bad}개는 건너뛰었다."
    if find:
        out["matched_count"] = len(hits)
        out["matched"] = [trim(json.dumps(h, ensure_ascii=False), 500) for h in hits[:MAX_ROWS_OUT]]
        if not hits:
            out["note"] = (out.get("note", "") + f" '{find}' 와 일치하는 줄은 없다.").strip()
    else:
        out["sample"] = [trim(json.dumps(json.loads(x), ensure_ascii=False), 300)
                         for x in text.splitlines()[:3] if x.strip()]
        out["note"] = (out.get("note", "") + " find 인자를 주면 관련 줄만 뽑는다.").strip()
    return compact(out)


# ── parquet — 라이브러리가 있어야 읽힌다 ─────────────────────────────
def _read_parquet(name, size, data, find):
    """pyarrow 가 있으면 스키마와 관련 행을, 없으면 **무엇을 설치하면 되는지**를 말한다.

    바이너리 컬럼 포맷이라 표준 라이브러리로는 열 수 없다. 못 읽는 것을 못 읽는다고
    말하는 것이, 파일명만 보고 내용을 짐작해 쓰는 것보다 언제나 낫다.
    """
    try:
        import pyarrow.parquet as pq          # noqa: F401  (설치돼 있으면 쓴다)
    except Exception:
        return {"error": f"'{name}' 은 parquet 이라 이 환경에서는 열 수 없습니다"
                         "(pyarrow 미설치). 스키마나 값이 필요하면 사람에게 요청하거나, "
                         "CSV 로 내려받은 파일을 첨부해 달라고 하세요."}
    try:
        import pyarrow.parquet as pq
        tbl = pq.read_table(io.BytesIO(data))
    except Exception as e:
        return {"error": f"'{name}' 을 parquet 으로 열지 못했습니다: {str(e)[:120]}"}
    columns = list(tbl.column_names)
    rows = [[str(v) for v in row] for row in zip(*[c.to_pylist() for c in tbl.columns])][:MAX_ROWS_SCAN]
    return _table_result(name, size, columns, rows, find, tbl.num_rows,
                         tbl.num_rows > MAX_ROWS_SCAN)


# ── 문서 — docx 는 zip+XML, pdf 는 pypdf ─────────────────────────────
def _read_docx(name, size, data, find):
    """워드도 zip 안의 XML 이다 — 문단 텍스트만 뽑으면 판단 재료로 충분하다."""
    import xml.etree.ElementTree as ET
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        root = ET.fromstring(zf.read("word/document.xml"))
    except Exception as e:
        return {"error": f"'{name}' 을 워드 문서로 열지 못했습니다: {str(e)[:120]}"}
    paras = []
    for p in root.iter(W + "p"):
        txt = "".join(t.text or "" for t in p.iter(W + "t")).strip()
        if txt:
            paras.append(txt)
    if not paras:
        return {"name": name, "size": _kb(size), "note": "본문 텍스트가 없다(표·이미지만 있을 수 있다)."}
    return _text_result(name, size, "\n".join(paras), find, extra={"paragraphs": len(paras)})


def _read_pdf(name, size, data, find):
    """PDF 는 pypdf 로 텍스트를 뽑는다. 스캔본(이미지 PDF)은 글자가 없으므로 그렇게 말한다."""
    try:
        from pypdf import PdfReader
    except Exception:
        return {"error": f"'{name}' 은 PDF 인데 이 환경에서는 열 수 없습니다(pypdf 미설치). "
                         "필요한 부분을 사람에게 요청하세요."}
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = reader.pages[:30]            # 앞 30쪽이면 판단 재료로 충분하다
        text = "\n".join((pg.extract_text() or "") for pg in pages)
    except Exception as e:
        return {"error": f"'{name}' 을 읽지 못했습니다: {str(e)[:120]}"}
    if not text.strip():
        return {"name": name, "size": _kb(size),
                "note": "글자가 추출되지 않았다 — 스캔 이미지 PDF 로 보인다. 내용이 필요하면 "
                        "사람에게 요청하라(추측하지 마라)."}
    return _text_result(name, size, text, find,
                        extra={"pages_read": len(pages), "pages_total": len(reader.pages)})


def _text_result(name, size, text, find, extra=None):
    """글에서 결과를 만든다 — find 가 있으면 그 줄 주변만(문서·코드·로그 공통)."""
    out = {"name": name, "size": _kb(size)}
    out.update(extra or {})
    if not find:
        out["text"] = trim(text, MAX_TEXT)
        return compact(out)
    lines = text.splitlines()
    pat = re.compile(re.escape(find), re.I)
    picked, seen = [], set()
    for i, ln in enumerate(lines):
        if not pat.search(ln):
            continue
        for j in range(max(0, i - 2), min(len(lines), i + 3)):
            if j not in seen:
                seen.add(j)
                picked.append(f"{j + 1}: {lines[j]}")
        if len(picked) > 200:
            break
    if picked:
        out["matched_lines"] = len(picked)
        out["text"] = trim("\n".join(picked), MAX_TEXT)
    else:
        out["note"] = f"'{find}' 가 이 파일에 없다."
        out["text"] = trim(text, 1500)
    return compact(out)

"""agent/tools/file_tools.py — 티켓에 붙은 **첨부파일**을 인지하고, 필요하면 읽는다.

두 단계로 나눈 이유가 있다.

**① 목록은 늘 본다.** 어떤 파일이 붙어 있는지(이름·확장자·크기·올린 사람)는 그 자체가
맥락이다 — "재현 로그 첨부했습니다" 라는 코멘트 옆에 `error.log 12KB` 가 있는 것과 없는
것은 판단이 다르다. 목록은 싸다(이미 티켓 조회에 실려 온다).

**② 내용은 필요할 때만, 작은 것만.** 첨부는 수십 MB 짜리 덤프일 수 있고, 그걸 통째로
프롬프트에 실으면 토큰도 시간도 감당이 안 된다. 그래서 **크기 상한**을 코드가 걸고,
텍스트로 읽을 수 있는 형식만 연다(이미지·바이너리는 "읽을 수 없다"고 정직하게 말한다).
"""

from __future__ import annotations

from langchain_core.tools import tool

from app.agent.tools._ctx import client, compact, trim

MAX_READ_BYTES = 256 * 1024      # 이보다 크면 읽지 않는다 — 판단 재료는 대개 앞부분에 있다
MAX_TEXT = 6000                  # 프롬프트에 실을 상한

# 텍스트로 열어도 되는 것. 확장자로 판단한다 — mime 은 사내 Jira 에서 자주 octet-stream 이다.
_TEXT_EXT = {"txt", "log", "md", "csv", "tsv", "json", "yaml", "yml", "xml", "html", "htm",
             "sql", "py", "js", "ts", "java", "sh", "bat", "ini", "cfg", "conf", "properties",
             "diff", "patch", "env", "toml"}


def _ext(name: str) -> str:
    return (name or "").rsplit(".", 1)[-1].lower() if "." in (name or "") else ""


def _kb(n: int) -> str:
    n = int(n or 0)
    return f"{n / 1024 / 1024:.1f}MB" if n >= 1024 * 1024 else f"{max(1, n // 1024)}KB"


def _kind(name: str, mime: str) -> str:
    """무슨 파일인지 한 마디 — 모델이 '열어 볼 가치가 있나'를 판단하는 근거."""
    e = _ext(name)
    if (mime or "").startswith("image/"):
        return "이미지(내용은 읽을 수 없음 — 첨부됐다는 사실만)"
    return {
        "log": "로그", "txt": "텍스트", "md": "문서(마크다운)", "csv": "표 데이터",
        "tsv": "표 데이터", "json": "구조화 데이터", "yaml": "설정", "yml": "설정",
        "xml": "구조화 데이터", "sql": "SQL", "py": "코드(Python)", "js": "코드(JS)",
        "sh": "스크립트", "diff": "패치", "patch": "패치", "zip": "압축(열 수 없음)",
        "xlsx": "엑셀(열 수 없음 — 표 내용은 붙여넣기를 요청하라)",
        "pdf": "PDF(열 수 없음 — 요약이 필요하면 사람에게 요청하라)",
        "pptx": "슬라이드(열 수 없음)", "docx": "워드(열 수 없음)",
    }.get(e, f"{e or '확장자 없음'} 파일")


@tool
def list_attachments(ticket_key: str) -> dict:
    """티켓에 **어떤 파일이 붙어 있는지** 본다. 이름·종류·크기·올린 사람·읽을 수 있는지.

    첨부 목록 자체가 맥락이다 — "로그 첨부했습니다" 라는 코멘트 옆에 실제로 `error.log`
    가 있는지, 크기가 12KB 인지 300MB 인지에 따라 다음 행동이 달라진다.
    티켓을 조사할 때 함께 보고, 필요하면 `read_attachment` 로 내용을 연다.

    돌려주는 것: {"key", "files": [{id, name, kind, size, author, created, readable}]}
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
            "readable": bool(_ext(name) in _TEXT_EXT and 0 < size <= MAX_READ_BYTES),
        }))
    return {"key": key, "files": files,
            "note": ("readable=true 인 것만 read_attachment 로 열 수 있다. 이미지·PDF·엑셀은 "
                     "붙어 있다는 사실만 말하고, 내용이 필요하면 사람에게 요청하라."
                     if files else "첨부파일이 없다.")}


@tool
def read_attachment(ticket_key: str, filename: str) -> dict:
    """첨부파일의 **내용을 읽는다**(작은 텍스트 파일만). 로그·CSV·설정에서 사실을 확인할 때.

    `list_attachments` 가 readable=true 로 표시한 것만 열린다 — 큰 파일과 이미지·PDF·
    엑셀 같은 바이너리는 거부된다(사유가 돌아온다). 앞부분 일부만 돌려주므로, 긴 로그는
    "무슨 오류가 났는가" 정도를 확인하는 용도다.

    돌려주는 것: {"name", "size", "text"} 또는 {"error"}
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
    if _ext(name) not in _TEXT_EXT:
        return {"error": f"'{name}' 은 텍스트로 읽을 수 없는 형식입니다({_kind(name, hit.get('mime') or '')}). "
                         "내용이 필요하면 사람에게 요약을 요청하세요."}
    if size > MAX_READ_BYTES:
        return {"error": f"'{name}' 은 {_kb(size)} 로 너무 큽니다(상한 {_kb(MAX_READ_BYTES)}). "
                         "필요한 부분을 사람이 발췌해 주어야 합니다."}
    # ★ 목록의 `url` 은 화면용 **프록시 경로**(/api/file?u=…)다 — 그걸 그대로 받으려 하면
    #   404 본문("{\"detail\":\"Not Found\"}")을 파일 내용으로 착각한다(실측). 원본 content
    #   URL 은 이슈 필드에만 있다.
    try:
        c = client()
        raw = (c.get_issue(key).get("fields") or {}).get("attachment") or []
        src = next((a.get("content") for a in raw if str(a.get("id") or "") == str(hit.get("id"))), "")
        # **경로 형태로 넘긴다** — 절대 URL 은 SSRF 허용 호스트 검사에 걸린다(mock 의 호스트는
        # 목록에 없다). 경로로 주면 provider 가 base·인증을 붙이므로 mock/local/prod 가 같은
        # 길을 탄다(첨부는 언제나 우리 Jira 에 있다).
        from urllib.parse import urlparse
        path = urlparse(src).path if str(src).startswith(("http://", "https://")) else src
        data, _ctype = c.fetch_media(path or "")
    except Exception as e:
        return {"error": str(e)[:200]}
    if not data:
        return {"error": f"'{name}' 을 내려받지 못했습니다."}
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("cp949")          # 사내 로그는 종종 cp949 다
        except UnicodeDecodeError:
            return {"error": f"'{name}' 의 문자 인코딩을 해석하지 못했습니다."}
    return compact({"name": name, "size": _kb(size), "text": trim(text, MAX_TEXT)})

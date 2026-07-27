"""
JiraClient — AuthProvider 를 주입받아 REST 호출 (어떤 인증인지 모름). 캐시 경유.
세 환경 모두 동일 REST 경로: mock=jira820 in-process(world 주입) / local=jira820 실HTTP / prod=사내 Jira.

Phase A 범위: Epic 자식 SP 롤업. (기능2·3 의 검색/활동은 후속 Phase 에서 확장)
"""

import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from html import escape, unescape
from urllib.parse import quote, unquote, urlparse

from . import progress
from .auth.base import SessionExpired, background_upstream, write_upstream
from .htmlsafe import (_CONF_RE, proxy_attachment_images, proxy_attachment_links,
                       proxy_images, sanitize_html,
                       shorten_mention_names, text_to_html, tidy_html)
from .names import real_name
from .sections import split_sections


# 실 Jira DC statusCategory.key → 내부 vocab (new=todo, indeterminate=inprogress, done=done)
# 설명·코멘트 HTML 안의 Jira 티켓 링크 → 언급된 티켓 추출용
_BROWSE_KEY_RE = re.compile(r"/browse/([A-Z][A-Z0-9]+-\d+)")
# 설명·코멘트 안의 링크 href (관련 문서 추출용)
_ANCHOR_RE = re.compile(r'<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
# Confluence URL 에서 문서 제목 — /pages/{id}/{slug} 또는 /display/{space}/{slug}
_CONF_TITLE_RE = re.compile(r"/pages/\d+/([^/?#]+)|/display/[^/]+/([^/?#]+)")
#: Confluence 페이지 id — 옛 링크(`/pages/viewpage.action?pageId=…`)엔 제목이 없고 이것만 있다.
_CONF_PAGEID_RE = re.compile(r"[?&]pageId=(\d+)|/pages/(\d+)(?:[/?#]|$)")


# 이슈 링크 관계 문구 — 사내 Jira 는 inward/outward 에 서술형 장문을 넣기도 한다
# (예: "Linked issue cannot finish until this issue finishes"). 타입 이름으로 짧은 표준어를 쓴다.
_LINK_LABEL = {                       # type.name → (outward, inward)
    "blocks": ("blocks", "is blocked by"),
    "duplicate": ("duplicates", "is duplicated by"),
    "cloners": ("clones", "is cloned by"),
    "relates": ("relates to", "relates to"),
    "dependency": ("depends on", "is depended on by"),
}
_REL_MAXLEN = 22                      # 이보다 길면 서술형으로 보고 잘라낸다


def _rel_label(type_obj, outward):
    """관계 라벨 — 타입 이름 매핑 우선, 없으면 Jira 문구(길면 축약)."""
    name = (type_obj.get("name") or "").strip().lower()
    pair = _LINK_LABEL.get(name)
    if pair:
        return pair[0] if outward else pair[1]
    txt = ((type_obj.get("outward") if outward else type_obj.get("inward")) or "").strip()
    if not txt:
        return (type_obj.get("name") or "관련").strip()
    if len(txt) > _REL_MAXLEN:        # 서술형 장문 → 타입 이름으로 대체, 그것도 없으면 축약
        return (type_obj.get("name") or txt[:_REL_MAXLEN - 1] + "…").strip()
    return txt


def _conf_key(url):
    """같은 문서를 가리키는 서로 다른 URL 을 하나로 묶는 키.
    Confluence 는 같은 페이지가 여러 형태로 링크된다:
      /spaces/DL/pages/123/제목 · /pages/viewpage.action?pageId=123 · /display/DL/제목
      (+ ?src=... 쿼리, #앵커, 끝 슬래시, 제목이 바뀌어도 id 는 그대로)
    우선순위: 페이지 id > (space, 제목슬러그) > 정규화 URL."""
    u = (url or "").split("#")[0].rstrip("/")
    m = re.search(r"[?&]draftId=(\d+)", u)     # 편집(초안) 모드 — resumedraft.action?draftId=...
    if m:
        return "draft:" + m.group(1)
    m = re.search(r"[?&]pageId=(\d+)", u)
    if m:
        return "id:" + m.group(1)
    u = u.split("?")[0].rstrip("/")
    m = re.search(r"/pages/(\d+)", u)
    if m:
        return "id:" + m.group(1)
    m = re.search(r"/display/([^/]+)/([^/]+)$", u)
    if m:
        sp = unquote(m.group(1)).strip().lower()
        ti = unquote(m.group(2)).replace("+", " ").strip().lower()
        return f"sp:{sp}/{ti}"
    return "u:" + u.lower()


def _abs_url(url, base):
    """상대경로면 base 를 붙여 절대 URL 로.

    사내 Confluence 링크는 본문에 "/display/DL/문서" 처럼 **상대경로**로 들어오는 경우가 있다.
    그대로 두면 브라우저가 우리 앱(localhost) 기준으로 해석해 엉뚱한 곳(404)으로 가고,
    같은 호스트로 보이니 run.py 의 외부링크 훅도 타지 않아 시스템 브라우저가 아예 안 뜬다.
    """
    u = (url or "").strip()
    if not u or "://" in u or u.startswith("//") or u.startswith("mailto:"):
        return u
    if not u.startswith("/"):
        return u                                   # 앵커(#…) 등은 손대지 않는다
    return (base or "").rstrip("/") + u if base else u


#: 제목이라 부르기 어려운 값들 — 이게 나오면 URL 슬러그도 링크 텍스트도 쓸모가 없었다는 뜻이다.
#: (Confluence 의 옛 링크 `/pages/viewpage.action?pageId=…` 에는 제목이 아예 없다.)
_WEAK_TITLES = {"", "page", "pages", "viewpage.action", "confluence 문서", "문서"}


def _weak_title(t, url=""):
    tt = (t or "").strip().lower()
    if tt in _WEAK_TITLES:
        return True
    # URL 을 그대로 제목처럼 쓰고 있으면 그것도 제목이 아니다
    return bool(url) and tt == (url or "").strip().lower()


#: Confluence 의 'Jira 이슈' 매크로가 자동으로 다는 링크 관계. 사람이 붙인 참고 문서가 아니다.
_MENTION_RELS = ("mentioned in", "mentioned on", "is mentioned in", "wiki page")


def _is_mention_link(r):
    rel = (r.get("rel") or "").strip().lower()
    if rel:
        return any(k in rel for k in _MENTION_RELS)
    return False


def _conf_title(url, text=None):
    """문서 제목 — URL 슬러그 우선, 없으면 **링크 텍스트**로 폴백.
    편집(초안) 모드 URL(/pages/resumedraft.action?draftId=...)에는 제목이 없어서
    링크 텍스트가 유일한 단서다."""
    m = _CONF_TITLE_RE.search(url or "")
    slug = (m.group(1) or m.group(2)) if m else None
    if slug:
        t = unquote(slug).replace("+", " ").strip()
        if t:
            return t
    t = re.sub(r"<[^>]+>", "", text or "").strip()      # 앵커 안 텍스트(태그 제거)
    t = unescape(t)
    return t or "Confluence 문서"

_CAT_MAP = {"new": "todo", "indeterminate": "inprogress", "done": "done", "undefined": "todo"}


from .progress import VOC_COMPONENT   # 컴포넌트명 = VoC 판정 기준(워크로드·계보 공용, 단일 소스)


def _wl_category(component, itype, is_subtask=None):
    """워크로드 카테고리 — VoC성 / Sub-Task / Task. is_subtask=issuetype.subtask(로케일 무관)."""
    if component == VOC_COMPONENT:
        return "voc"
    if is_subtask if is_subtask is not None else (itype == "Sub-Task"):
        return "subtask"
    if itype == "Task":
        return "task"
    return None


def _norm_cat(key):
    return _CAT_MAP.get((key or "").lower(), "todo")


def _started_from(created, updated, cat):
    """착수일(Started) 추정 — 진행중/완료면 created~updated 사이 1/3 지점, To Do 면 None. (결정적)"""
    if cat == "todo" or not created or not updated:
        return None
    try:
        da, db = date.fromisoformat(created[:10]), date.fromisoformat(updated[:10])
        return (da + timedelta(days=max((db - da).days, 0) // 3)).isoformat()
    except Exception:
        return None


def _comp_of(f):
    """대표 Component 이름. VoC 성 판정을 위해 '사용자 VoC' 가 있으면 그걸, 없으면 첫 컴포넌트."""
    comps = [c.get("name") for c in (f.get("components") or [])]
    return VOC_COMPONENT if VOC_COMPONENT in comps else (comps[0] if comps else None)


def _normalize_issue(raw, sp_field):
    f = raw.get("fields", {}) or {}
    status = (f.get("status") or {})
    cat = _norm_cat((status.get("statusCategory") or {}).get("key"))
    return {
        "key": raw.get("key"),
        "type": (f.get("issuetype") or {}).get("name", ""),
        "sp": f.get(sp_field),          # None 이면 누락 → progress.sp_of 가 기본값 적용
        "statusCategory": cat,          # todo | inprogress | done
        "labels": f.get("labels", []) or [],
        "component": _comp_of(f),       # VoC 성 판정용 (progress 에서 항상 제외)
        "assignee": ((f.get("assignee") or {}).get("name")),
        "updated": f.get("updated"),
    }


def _display_node(raw, sp_field, with_subs=False):
    """WBS Gantt 트리 표시용 상세 노드(요약·타입·상태·일정·SP + Sub-Task). progress 계산과 별개."""
    f = raw.get("fields", {}) or {}
    st = f.get("status") or {}
    node = {
        "key": raw.get("key"),
        "type": (f.get("issuetype") or {}).get("name", ""),
        "summary": f.get("summary") or raw.get("key"),
        "statusName": st.get("name", ""),
        "statusCat": _norm_cat((st.get("statusCategory") or {}).get("key")),
        "start": (f.get("created") or "")[:10] or None,
        "end": (f.get("duedate") or f.get("resolutiondate") or f.get("updated") or "")[:10] or None,
        "sp": f.get(sp_field),
        "component": _comp_of(f),        # Bug/VoC '보지 않기' 시각 토글용
    }
    if with_subs:
        subs = []
        for s in (f.get("subtasks") or []):
            sf = s.get("fields", {}) or {}
            sst = sf.get("status") or {}
            subs.append({
                "key": s.get("key"),
                "type": (sf.get("issuetype") or {}).get("name", "Sub-Task"),
                "summary": sf.get("summary") or s.get("key"),
                "statusName": sst.get("name", ""),
                "statusCat": _norm_cat((sst.get("statusCategory") or {}).get("key")),
                "component": _comp_of(sf),
            })
        node["children"] = subs
    return node


# 원문이 HTML 인지 — **블록 태그**가 보일 때만 True.
# 보수적으로 보는 이유 두 가지:
#   · "a < b" 같은 평문을 HTML 로 오인하면 sanitize 가 내용을 잘라먹는다.
#   · 평문 설명에 <b>x</b> 처럼 인라인 태그를 그냥 적어 두는 경우가 있다(그대로 보여야 한다).
# WYSIWYG 로 저장된 HTML 이면 <p> 같은 블록 태그가 반드시 하나는 있다.
_HTMLISH_RE = re.compile(
    r"</(?:p|div|ul|ol|li|table|tr|td|h[1-6]|blockquote|pre)>"
    r"|<br\s*/?>|<(?:p|div|table|ul|ol|blockquote|pre|h[1-6])[\s>]", re.I)


# 우선순위 '미지정' — 사내 Jira 는 미설정을 'Unclassified' 로 준다.
# 화면엔 '미지정' 으로 보여야 하므로 여기서 None 으로 정규화하고, 표기는 프론트가 정한다.
_UNSET_PRIORITY = {"unclassified", "none", "not set", "undefined", "-", ""}


def _prio(name):
    n = (name or "").strip()
    return None if n.lower() in _UNSET_PRIORITY else n



def _looks_like_html(s):
    return bool(s) and bool(_HTMLISH_RE.search(str(s)))


def _log_sections(key, view):
    """영역 분할/표 판정 결과를 콘솔(stderr)에 한 줄로.

    prod 데이터는 반출할 수 없으니 화면 대신 **로그로** 원인을 본다.
    구분선이 하나도 없는 평범한 티켓에서는 조용하다(불필요한 잡음 방지).
    """
    secs = view.get("descriptionSections") or []
    titled = [s for s in secs if s.get("title")]
    if not titled:
        # 구분선이 있을 법한데 못 잘랐다면 그것도 알려준다
        if "===" in (view.get("descriptionHtml") or ""):
            print("[sections] %s: 구분선 미검출 (=== 은 있으나 영역으로 안 갈림)" % key,
                  file=sys.stderr)
        return
    parts = []
    for s in secs:
        name = s.get("title") or "(머리말)"
        if s.get("kv"):
            parts.append("'%s' 표%d행" % (name, len(s["kv"])))
        else:
            why = s.get("kvSkip") or "-"
            parts.append("'%s' 본문(%s)" % (name, why))
    # (dev 진단용 — prod 로그를 채워 제거. 필요하면 LAKE_DEBUG 로 되살릴 것.)


def _pri_rank(name):
    from .mytasks import pri_rank           # 등급 판정은 mytasks 가 단일 소스(P{n} 접두사 우선)
    return pri_rank(name)


_CB_INPUT_RE = re.compile(r"<input\b[^>]*>", re.I)
_CB_TYPE_RE = re.compile(r"""type\s*=\s*['"]?\s*checkbox""", re.I)
_CB_CHECKED_RE = re.compile(r"""\s+checked(\s*=\s*(?:"[^"]*"|'[^']*'|[^\s/>]+))?""", re.I)
_CB_ID_RE = re.compile(r"""\bid\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s/>]+))""", re.I)


def _apply_checked(tag, checked):
    """`<input …>` 태그 문자열의 checked 만 설정/해제(나머지 속성 보존)."""
    tag = _CB_CHECKED_RE.sub("", tag)                 # 기존 checked 제거(중복 방지)
    if not checked:
        return tag
    if tag.endswith("/>"):
        return tag[:-2].rstrip() + ' checked="checked" />'
    return tag[:-1].rstrip() + ' checked="checked">'


def _tag_id(tag):
    m = _CB_ID_RE.search(tag)
    return (m.group(1) or m.group(2) or m.group(3)) if m else None


def _set_checkbox(html, index, checked, cbid=None):
    """HTML 원문에서 대상 `<input type=checkbox>` 의 checked 만 뒤집어 돌려준다.

    **id 우선, index 폴백**: cbid 가 주어지고 그 id 를 가진 체크박스가 있으면 그걸 짚는다
    (체크박스가 추가/삭제·순서변경돼도 정확). 없으면 index 번째(0-based)로 짚는다
    (prod 가 상태변경마다 id 를 재발급해 id 가 안 맞을 때의 폴백). 못 찾으면 None.
    나머지 속성(id·dir 등)과 문서의 다른 부분은 그대로 둔다 — 체크 상태 하나만 바꾼다."""
    boxes = [m for m in _CB_INPUT_RE.finditer(html) if _CB_TYPE_RE.search(m.group(0))]
    m = None
    if cbid:
        cbid = str(cbid)
        for b in boxes:
            if _tag_id(b.group(0)) == cbid:
                m = b
                break
    if m is None:                                     # id 없거나 못 찾음 → index 폴백
        if index is None or index < 0 or index >= len(boxes):
            return None
        m = boxes[index]
    tag = _apply_checked(m.group(0), checked)
    return html[:m.start()] + tag + html[m.end():]


#: 우리가 저장한 체크박스 HTML(`<p dir="auto"><input … type="checkbox">…</p>`)이 **이스케이프된 채**
#: 렌더돼 온 것을 되살린다. 사내 prod(JEDITOR)는 이 HTML 을 네이티브 체크박스로 렌더하지만,
#: 로컬 mock(jira820)의 표준 wiki 렌더러는 raw HTML 을 글자로 이스케이프한다(`&lt;input …&gt;`가
#: 화면에 그대로 보임). 그 이스케이프된 체크박스 문단만 실제 HTML 로 되돌려 로컬도 prod 처럼 렌더.
_ESC_CB_PARA = re.compile(
    r'&lt;p dir="auto"&gt;\s*&lt;input\b([^&]*?)/?\s*&gt;(.*?)&lt;/p&gt;', re.I | re.S)


def _revive_checkboxes(html):
    if not html or "&lt;input" not in html:
        return html

    def one(m):
        attrs, text = m.group(1), m.group(2)
        if "checkbox" not in attrs.lower():
            return m.group(0)          # 체크박스가 아닌 이스케이프는 건드리지 않는다
        # ★ `<p>` 로 다시 감싸지 않는다 — 이 조각은 이미 jira820 이 만든 바깥 `<p>` 안이라,
        #    `<p>` 를 넣으면 p 안의 p 가 돼(브라우저가 빈 p 로 쪼갬) 줄간격이 크게 벌어졌다.
        #    체크박스를 **인라인**으로 두면 바깥 p 안에서 줄바꿈(<br>)만으로 깔끔히 나열된다.
        return '<input' + attrs + '>' + text

    return _ESC_CB_PARA.sub(one, html)


def _checkbox_states(raw_field):
    """raw 필드(원문 HTML/wiki)에서 체크박스의 (checked, id) 를 **문서 순서대로** 뽑는다.

    저장은 우리가 raw 에 하므로(우리 _set_checkbox 가 checked 를 정확히 남긴다) raw 가 상태의
    진실이다. prod 의 renderedFields(JEDITOR 서버 렌더)는 checked 를 input 에 안 싣고 je_rdata
    JSON 에 두기도 해, 렌더만 보면 늘 해제로 보인다 → raw 에서 상태를 읽어 렌더에 덧씌운다."""
    out = []
    for m in _CB_INPUT_RE.finditer(raw_field or ""):
        tag = m.group(0)
        if _CB_TYPE_RE.search(tag):
            out.append((bool(_CB_CHECKED_RE.search(tag)), _tag_id(tag)))
    return out


_SANITIZED_CB_RE = re.compile(r'<input\b[^>]*\btkt-cb\b[^>]*>', re.I)


def _sync_checkboxes(rendered_html, raw_field):
    """정화된 렌더 HTML 의 체크박스들에 raw 의 상태(checked)·id 를 **순서대로 덧씌운다**.
    렌더 순서 = 원문 순서라 index 로 매칭. raw 에 체크박스가 없으면 그대로 둔다(무영향)."""
    states = _checkbox_states(raw_field)
    if not states or "tkt-cb" not in (rendered_html or ""):
        return rendered_html
    i = [0]

    def repl(m):
        n = i[0]
        i[0] += 1
        if n >= len(states):
            return m.group(0)
        checked, cid = states[n]
        tag = re.sub(r'\s+checked(="[^"]*")?', "", m.group(0), flags=re.I)
        tag = re.sub(r'\s+data-cb-id="[^"]*"', "", tag, flags=re.I)
        ins = (" checked" if checked else "") + (' data-cb-id="%s"' % escape(cid, quote=True) if cid else "")
        return (tag[:-2].rstrip() + ins + " />") if tag.endswith("/>") else (tag[:-1].rstrip() + ins + ">")

    return _SANITIZED_CB_RE.sub(repl, rendered_html)


def _build_ticket_view(raw, sp_field, jira_base="", epic_field=None):
    """티켓 상세 다이얼로그용 리치 뷰(순수 함수 — 테스트 용이).
    description: prod 의 renderedFields.description(HTML)이 있으면 **sanitize**, 없으면 평문→escape+nl2br.
    """
    f = raw.get("fields", {}) or {}
    rendered = raw.get("renderedFields") or {}
    rhtml = rendered.get("description")
    raw_desc = f.get("description")
    if rhtml and str(rhtml).strip():
        # 렌더 HTML 로 리치 내용을 그리되, 체크박스의 상태·id 는 **raw 원문**에서 덧씌운다
        # (prod renderedFields 는 checked 를 input 에 안 실어 늘 해제로 보이던 문제).
        desc = _sync_checkboxes(tidy_html(sanitize_html(_revive_checkboxes(rhtml))), raw_desc)
        desc, fmt = shorten_mention_names(desc), "html"
    elif _looks_like_html(raw_desc):
        # 사내 인스턴스는 WYSIWYG 에디터(Jira Editor 계열 — 'jePanel_*' class)를 써서
        # fields.description **원문 자체가 HTML** 이다. 이때 평문 취급하면 태그가
        # 글자로 보인다(<p>안녕하세요</p>). renderedFields 가 빌 때의 방어. (raw 라 상태 이미 정확)
        desc, fmt = shorten_mention_names(tidy_html(sanitize_html(raw_desc))), "html"
    else:
        desc, fmt = tidy_html(text_to_html(raw_desc or "")), "text"
    st = f.get("status") or {}
    itype = f.get("issuetype") or {}

    def _rn(u):
        u = u or {}
        return real_name(u.get("displayName") or u.get("name")) if u else None

    key = raw.get("key", "")
    return {
        "key": key,
        "summary": f.get("summary", ""),
        "type": itype.get("name", ""),
        "subtask": bool(itype.get("subtask")),
        "status": st.get("name", ""),
        "statusCategory": _norm_cat((st.get("statusCategory") or {}).get("key")),
        "priority": _prio((f.get("priority") or {}).get("name")),
        # 등급(0=가장 급함)은 '내 Task' 와 **같은 표**를 쓴다. 화면마다 따로 매기면 같은 티켓이
        # 화면에 따라 다른 아이콘으로 보인다(Blocker 가 여기선 보통, 저기선 최우선).
        "priRank": _pri_rank((f.get("priority") or {}).get("name")),
        "assignee": _rn(f.get("assignee")),
        "reporter": _rn(f.get("reporter")),
        # 프로필 이미지 조회용 사용자 id (displayName 이 아니라 Jira username)
        "assigneeId": (f.get("assignee") or {}).get("name"),
        "reporterId": (f.get("reporter") or {}).get("name"),
        "created": f.get("created") or None,
        "updated": f.get("updated") or None,
        # 시작일 — 실 Jira 에 필드가 없어 현안 화면과 동일한 파생 규칙(_started_from)을 쓴다
        "started": _started_from((f.get("created") or "")[:10],
                                 f.get("updated"),
                                 _norm_cat((st.get("statusCategory") or {}).get("key"))),
        "due": f.get("duedate") or None,
        "resolved": f.get("resolutiondate") or None,
        "labels": f.get("labels") or [],
        "components": [c.get("name") for c in (f.get("components") or []) if c.get("name")],
        "sp": f.get(sp_field),
        # 소속 Epic — 편집(Epic Link)과 표시에 함께 쓴다. 계보 패널은 별도 조회지만,
        # 이 값이 있어야 "지금 무엇에 속해 있나" 를 한 번의 응답으로 알 수 있다.
        "epicKey": (f.get(epic_field) if epic_field else None) or None,
        "descriptionHtml": desc,           # 항상 안전(정화됨). 프론트는 그대로 v-html.
        "descriptionFormat": fmt,          # 'html'(정화됨) | 'text'(평문→nl2br)
        # '=== 제목 ===' 구분선으로 나눈 영역. 구분선이 없으면 1개(title=None)라
        # 프론트는 항상 이것만 그리면 된다(descriptionHtml 은 검색·언급스캔용으로 유지).
        "descriptionSections": split_sections(desc),
        "url": (jira_base.rstrip("/") + "/browse/" + key) if jira_base else "",
    }


class JiraClient:
    def __init__(self, settings, cache):
        self.s = settings
        self.cache = cache
        self.env = settings.jira_env
        # provider 는 lazy 생성 — 임포트/기동 시 Chrome 을 띄우거나 세션 없음으로 크래시하지 않는다.
        self._provider = None
        self._provider_built = False
        # 세션 사용자 캐시도 함께 버린다. 안 그러면 로그인 직후에도 옛 판정(빈 사용자)이
        # TTL 동안 남아 매니저 여부·본인 댓글 판정이 계속 틀린다.
        try:
            self.cache.invalidate(f"myself:{self.env}")
        except Exception:
            pass

    @property
    def provider(self):
        if not self._provider_built:
            self._provider = self._make_provider()
            self._provider_built = True
        return self._provider

    def _make_provider(self):
        if self.env == "local":
            from .auth.basic import BasicAuthProvider
            return BasicAuthProvider(self.s.jira_base, self.s.jira_user,
                                     self.s.jira_token, self.s.jira_auth)
        if self.env == "prod":
            # 세션 없으면 SsoSessionProvider 가 LoginRequired 를 던짐 → 라우트가 needLogin 처리.
            from .auth.sso_session import SsoSessionProvider
            return SsoSessionProvider(self.s.jira_base, self.sso_store())
        # mock: jira820 을 in-process(ASGI)로 — 이 프로젝트 world 주입. HTTP 소켓/run_fake 불필요.
        from .auth.inprocess import InProcessProvider
        return InProcessProvider()

    def sso_store(self):
        """SSO 세션 저장소 — 서비스별 파일. 한 서비스를 갱신해도 나머지가 안 날아간다."""
        from .auth.sso_store import SsoStore
        if getattr(self, "_sso_store", None) is None:
            self._sso_store = SsoStore(self._state_path(), {
                "jira": self.s.jira_base,
                "confluence": self.s.confluence_base,
                "bitbucket": self.s.bitbucket_base,
            })
        return self._sso_store

    def _state_path(self):
        """세션 파일 절대 경로 (상대면 APP_ROOT 기준 — run.py 의 존재검사와 일치)."""
        from pathlib import Path
        from .settings import APP_ROOT
        p = Path(self.s.jira_state_path)
        return str(p if p.is_absolute() else APP_ROOT / p)

    def needs_login(self):
        """prod 이고 저장된 세션이 하나도 없으면 True (mock/local 은 항상 False)."""
        if self.env != "prod":
            return False
        return not self.sso_store().any_exists()

    def reset_provider(self):
        """세션 갱신 후 다음 호출 때 새 세션으로 provider 를 재생성하도록 초기화."""
        if self._provider is not None:
            try:
                self._provider.close()
            except Exception:
                pass
        self._provider = None
        self._provider_built = False
        # 세션 사용자 캐시도 함께 버린다. 안 그러면 로그인 직후에도 옛 판정(빈 사용자)이
        # TTL 동안 남아 매니저 여부·본인 댓글 판정이 계속 틀린다.
        try:
            self.cache.invalidate(f"myself:{self.env}")
        except Exception:
            pass

    def login(self, timeout=300):
        """[prod] 설치된 Chrome 을 띄워 SSO 로그인(폴링 감지) 후 세션 저장. 성공 시 provider 재설정."""
        if self.env != "prod":
            return True
        from .auth.sso_session import login_wait
        # Jira 만 다시 로그인 — 저장은 jira 파일에만 되므로 Confluence/Bitbucket 세션은 그대로 산다.
        ok = login_wait(self.s.jira_base, self.sso_store(), service="jira", timeout=timeout)
        if ok:
            self.reset_provider()
        return ok

    def _resolve(self, epic_key):
        """epic 키 그대로 (wbs_config 가 실 티켓 DL-xxxx 사용 — 논리 id 매핑 없음)."""
        return epic_key

    # ── 티켓 단위 캐시 레이어 (모든 이슈/하위이슈를 key 단위로 캐싱) ──
    #: prod 에 **없는 커스텀 필드**. 인스턴스마다 필드 id 가 달라, config 에 잘못 적힌 값을
    #: fields= 로 요청하면 Jira 가 "field 'customfield_X' does not exist" 로 **400** 을 내고
    #: 그 이슈/검색 조회가 통째로 실패한다(로그 폭주·화면 빈 채로). 한 번 걸리면 그 필드를
    #: 여기 담아 두고 다음부터 요청에서 뺀다 — 나머지 필드는 정상적으로 온다.
    _bad_fields = None

    #: 목록/롤업/워크로드/VIT 어디에서도 안 읽는 **무거운 필드**. 이것만 빼면 페이로드가 확 준다
    #: (description 이 압도적으로 크다). 이 셋은 티켓 **다이얼로그**에서만 쓰는데, 다이얼로그는
    #: 별도 경로로 각각 받는다: 본문=issueview(renderedFields), 관련=issuelinks, 첨부=attachment
    #: (모두 get_issue/전용 조회). 그래서 목록 검색에선 안전하게 뺀다.
    _HEAVY_FIELDS = frozenset({"description", "issuelinks", "attachment"})

    def _issue_fields(self, light=False):
        base = ["summary", "description", "issuetype", "status", "assignee", "reporter",
                "components", "created", "duedate", "resolutiondate", "updated", "labels",
                "parent", "subtasks", "timespent", "issuelinks", "attachment",
                "priority",                     # '내 Task' 정렬 축
                self.s.sp_field_id, self.s.epic_link_field_id, self.s.epic_name_field_id]
        bad = self._bad_fields or set()
        drop = (bad | self._HEAVY_FIELDS) if light else bad
        return ",".join(f for f in base if f and f not in drop)

    _FIELD_ERR = re.compile(r"field '([^']+)' does not exist|'([^']+)'.*cannot be set", re.I)

    @staticmethod
    def _looks_anonymous(data):
        """응답이 **익명(비인증) 상태**를 가리키는가. Jira DC 는 세션이 없으면 401 대신
        200 + "... cannot be viewed by anonymous user" 로 답할 때가 있다 — 그걸 '없는 필드'
        로 오해하면 멀쩡한 필드를 빼 버리고, 진짜 원인(세션 끊김)을 놓친다."""
        if not isinstance(data, dict):
            return False
        blob = " ".join(data.get("errorMessages") or [])
        for v in (data.get("errors") or {}).values():
            blob += " " + str(v)
        return "anonymous" in blob.lower()

    def _note_bad_field(self, data):
        """검색/이슈 응답이 '없는 필드' 에러면 그 필드를 기억한다 → 다음 요청에서 뺀다.
        새로 알게 됐으면 True(호출부가 재시도)."""
        if not isinstance(data, dict):
            return False
        # 익명(세션 끊김)으로 필드가 안 보이는 것을 '없는 필드' 로 착각하면 안 된다 — 그건
        # 필드 문제가 아니라 인증 문제다(아래 조회 경로가 SessionExpired 로 올려 재인증한다).
        if self._looks_anonymous(data):
            return False
        msgs = " ".join(data.get("errorMessages") or [])
        for k, v in (data.get("errors") or {}).items():
            msgs += " " + str(v)
        found = set()
        for m in self._FIELD_ERR.finditer(msgs):
            f = m.group(1) or m.group(2)
            if f and f.startswith("customfield_"):
                found.add(f)
        if not found:
            return False
        if self._bad_fields is None:
            self._bad_fields = set()
        new = found - self._bad_fields
        if new:
            self._bad_fields |= new
            self._log_once("badfield", f"[fields] 없는 커스텀 필드 제외: {sorted(new)}")
            return True
        return False

    #: 같은 로그를 초당 몇 번씩 찍지 않게 — prod 는 상류가 단일 큐라 같은 실패가 연달아 온다.
    _log_seen = None

    def _log_once(self, key, msg, every=30.0):
        import time as _t
        if self._log_seen is None:
            self._log_seen = {}
        now = _t.monotonic()
        last = self._log_seen.get(key, 0)
        if now - last < every:
            return
        self._log_seen[key] = now
        print(msg, file=sys.stderr, flush=True)

    def get_issue(self, key):
        """단일 티켓 원본(fields 포함) — `issue:{env}:{key}` 로 티켓 단위 캐시."""
        ck = f"issue:{self.env}:{key}"

        def fetch():
            d = self.provider.get_json(f"/rest/api/2/issue/{key}",
                                       params={"fields": self._issue_fields()})
            if self._looks_anonymous(d):
                # 세션이 익명이 됐다 — 필드가 아니라 인증 문제다. 재인증이 돌게 올린다.
                raise SessionExpired("익명 응답 — 세션 끊김(재인증 필요).")
            if self._note_bad_field(d):
                d = self.provider.get_json(f"/rest/api/2/issue/{key}",
                                           params={"fields": self._issue_fields()})
            return d

        data, _ = self.cache.get_or_set(ck, self.s.cache_ttl_seconds, fetch)
        return data

    def get_issue_light(self, key):
        """단일 티켓 — **경량 필드만**(무거운 description·issuelinks·attachment 제외).

        뱃지·형제·자식키처럼 가벼운 정보만 필요할 때 쓴다. **포함관계 + 최신성**으로 캐시를 고른다:
        전체(issue:)는 경량의 상위집합이라 내용은 늘 충분하므로, 전체·경량 중 **TTL 이내이면서 더
        최근에 받아온 쪽**을 쓴다(전체가 더 최신이면 전체, 경량이 더 최신이면 경량). 둘 다 만료/없으면
        경량으로 받아 issueL 에 담는다(전체 캐시와 분리 — 관련/첨부가 이걸 읽고 깨지지 않게)."""
        ttl = self.s.cache_ttl_seconds
        now = time.time()
        fk, ck = f"issue:{self.env}:{key}", f"issueL:{self.env}:{key}"
        full = self.cache.get_stale_at(fk)        # (값, 받아온 시각) | None
        light = self.cache.get_stale_at(ck)
        full_ok = full is not None and (now - full[1]) <= ttl
        light_ok = light is not None and (now - light[1]) <= ttl
        # 전체가 살아 있고(TTL내) 경량보다 더(또는 같이) 최신이면 전체를 쓴다 — 굳이 다시 안 받는다.
        if full_ok and (not light_ok or full[1] >= light[1]):
            return full[0]
        # 그 외(경량이 더 최신이거나 전체가 만료) → issueL 경유(SWR). 만료면 여기서 경량 조회.

        def fetch():
            d = self.provider.get_json(f"/rest/api/2/issue/{key}",
                                       params={"fields": self._issue_fields(light=True)})
            if self._looks_anonymous(d):
                raise SessionExpired("익명 응답 — 세션 끊김(재인증 필요).")
            if self._note_bad_field(d):
                d = self.provider.get_json(f"/rest/api/2/issue/{key}",
                                           params={"fields": self._issue_fields(light=True)})
            return d

        data, _ = self.cache.get_or_set(ck, self.s.cache_ttl_seconds, fetch)
        return data

    ISSUE_BATCH = 50                  # JQL "key in (...)" 한 번에 담을 최대 개수

    # 매번 재검증하는 캐시 키 = **사람이 방금 바꿨을 수 있고, 틀리면 바로 곤란한 것**뿐이다.
    #   issue/issueview  티켓 본문 + 상태·담당·마감(같은 응답에 들어 있다)
    #   comments         댓글
    # 여기까지가 "지금 이 티켓이 어떤 상태이고 무슨 얘기가 오갔나" 다. TTL 이 15분이면 내가
    # 방금 쓴 댓글이 15분 동안 안 보이는데, 그건 캐시가 아니라 버그로 보인다.
    #
    # ★ 나머지는 일부러 뺐다. 첨부·링크·이력(changelog/timeline)·계보(ancestors/siblings/
    #   children/related)는 자주 바뀌지 않고, 조금 낡아도 판단이 틀어지지 않는다. 반면 갱신
    #   비용은 크다 — 계보 하나가 상류를 여러 번 타고(실측 3건), prod 는 상류가 단일 큐라
    #   그게 쌓이면 정작 본문·댓글이 밀린다. 강제 갱신은 값싼 것에만 건다.
    #   (내가 첨부·링크를 직접 바꾼 경우는 쓰기 직후 해당 키를 invalidate 하므로 이미 최신이다.)
    REVALIDATE_PREFIXES = ("issue:", "issueL:", "issueview:", "comments:")

    #: 요청 한 건이 도는 동안의 '못 가져온 개수'(스레드별)
    _miss = threading.local()

    def _wire_cache(self):
        """캐시에 '항상 재검증' 규칙과 스케줄러를 물린다. 호출부를 안 건드리려고 여기 한 곳에서."""
        self.cache.always_revalidate = self.REVALIDATE_PREFIXES
        self.cache.revalidator = self._refresh_bg
        self.cache.skip_producer = self.upstream_down

    # ── 못 가져온 것 세기 ──────────────────────────────────────────────
    # 트리·목록 조립은 티켓을 하나씩 받아 붙이는데, 그중 몇 개가 실패해도 조립은 계속된다
    # (하나 못 받았다고 화면 전체를 죽일 이유는 없다). 문제는 **그 사실이 사라진다**는 것이다 —
    # 화면에는 '하위 티켓 없음' 으로 뜨고, 보는 사람은 진짜 없는 줄 안다.
    # 그래서 요청 하나가 도는 동안 실패 건수를 세어 두고, 라우트가 응답에 실어 보낸다.
    # 스레드별로 센다 — FastAPI 동기 라우트는 스레드풀에서 돌아 요청끼리 섞이면 안 된다.
    def miss_begin(self):
        self._miss.n = 0

    def miss_add(self, n=1):
        self._miss.n = getattr(self._miss, "n", 0) + n

    def miss_count(self):
        return getattr(self._miss, "n", 0)

    def _swr(self, key, producer):
        """stale-while-revalidate — 만료돼도 남은 값이 있으면 **즉시** 주고 뒤에서 갱신.

        재방문 체감 대기를 없애는 게 목적이다. 갱신은 워커 1개로, 상류 호출은
        백그라운드 우선순위(사용자 요청이 항상 앞지름)로 돈다 — prod 는 상류가
        단일 큐라 이걸 안 하면 갱신이 사용자 조회를 밀어낸다.
        같은 키의 갱신이 이미 돌고 있으면 새로 띄우지 않는다.
        """
        value, _how = self.cache.get_or_set_swr(
            key, self.s.cache_ttl_seconds, producer, self._refresh_bg)
        return value

    def _refresh_bg(self, key, ttl, producer):
        self._ensure_bg()
        with self._bg_lock:
            if key in self._bg_inflight:
                return
            self._bg_inflight.add(key)

        def job():
            try:
                with background_upstream():
                    self.cache.set(key, producer(), ttl)
            except Exception:
                pass                     # 갱신 실패는 조용히 — stale 값이 계속 쓰인다
            finally:
                with self._bg_lock:
                    self._bg_inflight.discard(key)

        self._bg_pool.submit(job)

    def _ensure_bg(self):
        """백그라운드 갱신 자원 — 워커 1개(직렬 상류를 더 붐비게 하지 않는다)."""
        if getattr(self, "_bg_pool", None) is None:
            self._bg_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="swr")
            self._bg_lock = threading.Lock()
            self._bg_inflight = set()

    # ── '내 Task' 용 얇은 조회 헬퍼 (app/mytasks.py 가 쓴다) ──
    def search_issues(self, jql, max_results=200):
        """JQL → 원본 이슈 리스트(캐시 write-through). 목록 자체는 캐시하지 않는다 —
        담당/마감이 자주 바뀌는 화면이라 낡은 목록이 더 해롭다."""
        return self._search(jql, max_results=max_results)

    def issues_by_keys(self, keys):
        """키 목록 → 원본 이슈들. 캐시에 있으면 그대로 쓰고 없는 것만 한 번에 받는다."""
        keys = [k for k in dict.fromkeys(keys) if k]
        if not keys:
            return []
        self.prefetch_issues(keys)
        out = []
        for k in keys:
            try:
                raw = self.get_issue(k)
            except Exception:
                raw = None
            if isinstance(raw, dict) and raw.get("key"):
                out.append(raw)
            else:
                # 못 받은 티켓은 목록에서 그냥 빠진다 — 그 사실을 세어 두지 않으면
                # 화면은 '원래 그만큼뿐' 이라고 말하게 된다(WBS 트리가 그렇게 비었다).
                self.miss_add()
        return out

    def s_today(self):
        """오늘 — mock/local 은 world 기준일이 '오늘'이라 실제 날짜와 다르다.
        마감 D-day 계산이 어긋나지 않게 서버가 기준일을 정해 준다."""
        from datetime import date
        if self.env == "prod":
            return date.today()
        try:
            from .world import get_world
            return get_world().today
        except Exception:
            return date.today()

    def prefetch_issues(self, keys, light=False):
        """여러 티켓 원본을 **검색 한 번**으로 받아 티켓 단위 캐시에 채운다.

        prod(SSO)는 모든 호출이 단일 큐로 직렬화되므로 낱개 GET N 번 = N × 왕복이다.
        미리 채워두면 이후 get_issue() 들이 전부 캐시 히트라 왕복이 0 이 된다.
        실패해도 조용히 넘어간다 — 뒤따르는 get_issue 가 개별 조회로 정상 동작한다.
        """
        env = self.env
        # light=True 면 경량 필드로 받아 issueL 에 채운다(뱃지·경량 노드용). 이미 전체(issue:)나
        # 경량(issueL:) 캐시가 있으면 그 티켓은 커버된 것 — 다시 안 받는다(포함관계).
        if light:
            miss = [k for k in dict.fromkeys(keys)
                    if k and self.cache.get(f"issue:{env}:{k}") is None
                    and self.cache.get(f"issueL:{env}:{k}") is None]
        else:
            miss = [k for k in dict.fromkeys(keys)
                    if k and self.cache.get(f"issue:{env}:{k}") is None]
        if len(miss) < 2:                 # 1건이면 배치 이득 없음(그냥 개별 조회에 맡긴다)
            return
        fields = self._issue_fields(light=light)
        pfx = "issueL" if light else "issue"
        for i in range(0, len(miss), self.ISSUE_BATCH):
            chunk = miss[i:i + self.ISSUE_BATCH]
            try:
                data = self.provider.get_json("/rest/api/2/search", params={
                    "jql": "key in (%s)" % ",".join(chunk),
                    "fields": fields, "maxResults": len(chunk)})
            except SessionExpired:
                raise
            except Exception:
                return
            for it in (data.get("issues") or []):
                k = it.get("key")
                if k:
                    self.cache.set(f"{pfx}:{env}:{k}", it, self.s.cache_ttl_seconds)

    def _search(self, jql, cache_key=None, max_results=200, light=True):
        """JQL 검색 → 원본 이슈 리스트. 결과를 티켓 단위 캐시에 write-through.
        (검색 자체도 cache_key 주면 캐시 — 같은 목록 재조회 절약).

        **기본이 light** 다 — 목록/롤업/워크로드/VIT 어디도 무거운 필드를 안 읽으므로 항상 경량으로
        받는다(가장 큰 절감은 description). full 이 정말 필요한 자리는 없다(있으면 명시적으로 끈다).
        light=True 면 **무거운 필드(description·issuelinks·attachment)를 빼고** 받는다. 캐시는 **포함관계**로 재사용한다:
          · light 요청은 full 캐시(상위집합)가 있으면 그걸 쓴다(cache_key). 없으면 light 캐시.
          · full 요청은 full 캐시만 쓴다(light 는 필드가 모자라 full 을 만족 못 시킴).
        write-through 도 분리한다: full 은 issue:{key}(전체), light 는 issueL:{key}(경량) 로 —
        경량 이슈가 전체 이슈 캐시를 오염시켜 관련/첨부(issuelinks·attachment 사용)를 깨지 않게."""
        fields = self._issue_fields(light=light)

        def do():
            issues, start = [], 0
            while True:
                data = self.provider.get_json("/rest/api/2/search", params={
                    "jql": jql, "fields": fields,
                    "startAt": start, "maxResults": 100})
                # 검색 응답이 아니면(예: 인증 끊긴 Jira 의 로그인 HTML) 그 자리에서 멈춘다.
                # ★ 예전엔 여기서 곧장 SessionExpired 를 올렸는데, 검색은 VIT·WBS·워크로드·
                #   내 Task 에서 쉴 새 없이 돈다 — 응답이 조금만 예상과 달라도 그때마다 세션을
                #   '죽음' 으로 표시하고 로그인 창이 떴다(어제부터 '인증 계속 풀림' 의 정체).
                #   대신 **빈 목록은 캐시하지 않는** 것으로 0건 오염을 막고(아래), 여기서는
                #   그 페이지만 버리고 지금까지 모은 것을 돌려준다. 세션이 진짜 죽었으면
                #   provider 가 401 에서 이미 SessionExpired 를 던졌을 것이다.
                if not isinstance(data, dict) or "issues" not in data:
                    if self._looks_anonymous(data):
                        raise SessionExpired("익명 응답(검색) — 세션 끊김(재인증 필요).")
                    # 없는 커스텀 필드 때문이면 그 필드 빼고 이 페이지만 다시(로그는 _note 가 1회).
                    if self._note_bad_field(data):
                        data = self.provider.get_json("/rest/api/2/search", params={
                            "jql": jql, "fields": self._issue_fields(light=light),
                            "startAt": start, "maxResults": 100})
                        if isinstance(data, dict) and "issues" in data:
                            batch = data.get("issues", [])
                            issues.extend(batch); start += 100
                            if start >= data.get("total", 0) or not batch or start >= max_results:
                                break
                            continue
                    # 자식 없는 Epic 은 Jira DC 가 400 에러로 답한다 — 정상적인 '0건' 이라 조용히.
                    msgs = " ".join((data or {}).get("errorMessages") or []) if isinstance(data, dict) else ""
                    if "parent epic" not in msgs.lower():
                        self._log_once("search-odd",
                                       f"[search] 검색 응답 아님(무시): {str(data)[:120]}")
                    break
                batch = data.get("issues", [])
                issues.extend(batch)
                start += 100
                if start >= data.get("total", 0) or not batch or start >= max_results:
                    break
            return issues
        if cache_key:
            # ★ **빈 목록은 캐시하지 않는다.** 인증이 끊긴 순간에 한 번 0건이 잡히면 그 0건이
            #   TTL 동안(그리고 SWR 로 그 뒤로도) 사실처럼 서빙된다 — PMO_VIT 가 계속 '현안 없음'
            #   이었던 이유다. 목록 조회는 다시 받으면 그만이라 빈 값을 굳힐 이유가 없다.
            #   (진짜로 0건인 프로젝트에서는 매번 한 번 더 묻는 것뿐이다.)
            # 포함관계 재사용: light 요청도 full 캐시(cache_key)가 있으면 그걸 쓴다(상위집합).
            hit = self.cache.get(cache_key)
            if hit is None and light:
                hit = self.cache.get(cache_key + ":light")
            if hit is not None:
                issues = hit
            else:
                issues = do()
                if issues:
                    self.cache.set(cache_key + (":light" if light else ""),
                                   issues, self.s.cache_ttl_seconds)
        else:
            issues = do()
        # write-through: full 은 issue:{key}(전체), light 는 issueL:{key}(경량) 로 분리한다.
        # 경량 이슈로 전체 이슈 캐시를 덮으면 관련(issuelinks)·첨부(attachment)가 빈 채로 보인다.
        pfx = "issueL" if light else "issue"
        for it in issues:                       # write-through: 각 티켓 개별 캐시
            if it.get("key"):
                self.cache.set(f"{pfx}:{self.env}:{it['key']}", it, self.s.cache_ttl_seconds)
        return issues

    # ── Epic 자식 조회 ──
    def epic_issues(self, epic_key):
        real = self._resolve(epic_key)
        cache_key = f"epic_issues:{self.env}:{real}"
        data, _hit = self.cache.get_or_set(
            cache_key, self.s.cache_ttl_seconds, lambda: self._fetch_epic_children(real)
        )
        return data

    def _fetch_epic_children(self, epic_key):
        """Epic 자식 조회 → 정규화. 검색 결과는 티켓 단위 캐시로 write-through."""
        sp = self.s.sp_field_id
        raw = self._search(f'"Epic Link" = {epic_key}',
                           cache_key=f"epic_children:{self.env}:{epic_key}")
        if not raw:
            try:  # 폴백: Agile API
                data = self.provider.get_json(
                    f"/rest/agile/1.0/epic/{epic_key}/issue",
                    params={"fields": self._issue_fields(), "maxResults": 100})
                raw = data.get("issues", [])
                for it in raw:
                    if it.get("key"):
                        self.cache.set(f"issue:{self.env}:{it['key']}", it, self.s.cache_ttl_seconds)
            except Exception:
                pass
        return [_normalize_issue(it, sp) for it in raw]

    def epic_tree(self, epic_key):
        """Epic 자식(Story/Task/Bug) + 각 자식의 Sub-Task — WBS Gantt 다계층 트리(표시용).
        같은 티켓 단위 캐시(epic_children/issue)를 재사용하므로 추가 상위호출 거의 없음."""
        real = self._resolve(epic_key)
        return self.cache.get_or_set(
            f"epic_tree:{self.env}:{real}", self.s.cache_ttl_seconds,
            lambda: self._build_epic_tree(real))[0]

    def _build_epic_tree(self, epic_key):
        sp = self.s.sp_field_id
        raws = self._search(f'"Epic Link" = {epic_key}',
                            cache_key=f"epic_children:{self.env}:{epic_key}")
        if not raws:
            try:  # 폴백: Agile API
                data = self.provider.get_json(
                    f"/rest/agile/1.0/epic/{epic_key}/issue",
                    params={"fields": self._issue_fields(), "maxResults": 100})
                raws = data.get("issues", [])
            except Exception:
                raws = []
        return [_display_node(it, sp, with_subs=True) for it in raws]

    def epic_name(self, epic_key):
        """Epic 이름(summary) — Jira 에서. (config 엔 이름 없음)"""
        try:
            it = self.get_issue_light(self._resolve(epic_key))   # summary 만 → 경량
            return (it.get("fields") or {}).get("summary") or epic_key
        except Exception:
            return epic_key

    def _pmap(self, items, fn):
        """items 를 fn 으로 매핑 — provider 가 병렬 안전(basic/PAT)이면 스레드풀, 아니면 순차.
        (SSO/mock 은 순차. cache.get_or_set 은 producer 중 lock 을 안 잡아 네트워크 병렬 유효.)"""
        items = list(items)
        prov = self.provider
        if prov is not None and getattr(prov, "supports_parallel", False) and len(items) > 1:
            with ThreadPoolExecutor(max_workers=min(8, len(items))) as ex:
                return list(ex.map(fn, items))
        return [fn(x) for x in items]

    def epic_progress_map(self, plan):
        """plan 의 모든 Epic 에 대해 진척률 dict 반환 {key -> {...,name}}. Epic 단위 병렬 조회."""
        keys, seen = [], set()
        for w in plan["wbs"]:
            for e in w["epics"]:
                if e["key"] not in seen:
                    seen.add(e["key"]); keys.append(e["key"])
        self.prefetch_issues([self._resolve(k) for k in keys])   # epic 원본을 한 번에
        results = self._pmap(keys, self.epic_progress_one)
        return {k: r for k, r in zip(keys, results)}

    # ── 기능2: PMO_VIT 현안 ──
    # 진척 = Root 현안의 자손 티켓 "개수 기반"(done/total). 시작·마감·소식·코멘트 포함.
    # (local/prod 경로는 라이브 Jira 대상 Phase B 에서 검증/심화 — 자손 깊이 등)
    def vit_issues(self, plan, people, epic_prog=None):
        # 조립 결과를 캐시 → /api/vit·/api/vit/{key} 가 매번 forest 를 재조립하지 않음
        data, _ = self.cache.get_or_set(f"vit_build:{self.env}", self.s.cache_ttl_seconds, self._fetch_vit)
        return data

    def vit_bases(self):
        """PMO_VIT 루트의 **경량 base 목록**(트리 없음) — 모듈 분류·중복제거용.
        모듈별 병렬 조회의 진입점: 이것만 있으면 각 모듈이 자기 루트만 조립할 수 있다."""
        def do():
            roots = self._search(
                f'project={self.s.project_key} AND labels="PMO_VIT" ORDER BY updated DESC',
                cache_key=f"vit_list:v2:{self.env}")
            return [self._vit_base(it) for it in roots]
        # 키에 v2 를 붙여 **예전에 박힌 빈 목록을 통째로 버린다**. 규칙을 고쳐도 이미 캐시에
        # 들어간 0건은 그대로 남아, 고친 코드가 도는데도 화면은 계속 비어 있었다.
        return self._swr(f"vit_bases:v2:{self.env}", do)

    def vit_tree_of(self, key, itype):
        """루트 하나의 자손 트리 — 루트 단위 캐시(모듈별 조회끼리 공유·재사용)."""
        return self._swr(f"vit_tree:{self.env}:{key}", lambda: self._vit_tree(key, itype))

    def vit_with_trees(self, bases):
        """주어진 base 들에 트리를 붙인다(루트 단위 병렬). 모듈별 엔드포인트가 사용."""
        def build(b):
            return dict(b, tree=self.vit_tree_of(b["key"], b["type"]))
        return self._pmap(bases, build)

    def _fetch_vit(self):
        # 전체(비분할) 경로 — 모듈별 경로와 동일한 캐시(vit_bases/vit_tree)를 재사용한다.
        return self.vit_with_trees(self.vit_bases())

    def _vit_base(self, issue):
        f = issue.get("fields", {}) or {}
        comps = f.get("components") or []
        status = f.get("status") or {}
        assignee = f.get("assignee") or {}
        cat = _norm_cat((status.get("statusCategory") or {}).get("key"))
        created = (f.get("created") or "")[:10] or None
        updated = f.get("updated") or None          # 전체 datetime 유지 → Updated At 시간표시
        ancestors = []
        if f.get("parent"):
            ancestors.append(f["parent"].get("key"))
        if f.get(self.s.epic_link_field_id):
            ancestors.append(f[self.s.epic_link_field_id])
        return {
            "key": issue.get("key", ""), "summary": f.get("summary", ""),
            "type": (f.get("issuetype") or {}).get("name", ""),
            "module": comps[0]["name"] if comps else "Module 미지정",
            "assignee": assignee.get("displayName") or assignee.get("name"),
            "start": created, "created": created,
            "started": _started_from(created, updated, cat), "updated": updated,
            "due": f.get("duedate"),
            "statusCategory": cat, "status": status.get("name", ""),
            "ancestors": [a for a in ancestors if a],
        }

    def _node_from_issue(self, issue):
        f = issue.get("fields", {}) or {}
        node = {
            "key": issue.get("key", ""),
            "summary": f.get("summary", ""),
            "type": (f.get("issuetype") or {}).get("name", ""),
            "assignee": real_name((f.get("assignee") or {}).get("displayName")
                                  or (f.get("assignee") or {}).get("name")),
            "statusCategory": _norm_cat(((f.get("status") or {}).get("statusCategory") or {}).get("key")),
            "status": ((f.get("status") or {}).get("name") or ""),
            "created": f.get("created") or None,          # 전체 datetime 유지(뉴스 시간표시)
            "resolved": f.get("resolutiondate") or None,
            "children": [],
        }
        # 하위(Sub-task)도 개별 티켓으로 조회 → 티켓 단위 캐시. 노드는 경량 필드만 쓰므로 경량 조회.
        # (개별 실패는 여기서 miss 로 세므로 경량 조회여도 부분실패 집계는 그대로 동작한다.)
        for s in (f.get("subtasks") or []):
            skey = s.get("key")
            if not skey:
                continue
            try:
                node["children"].append(self._node_from_issue(self.get_issue_light(skey)))
            except Exception:
                # 삼키되 **세어 둔다** — 안 그러면 못 받은 하위가 '없는 하위' 가 된다.
                self.miss_add()
        return node

    def _vit_tree(self, key, itype):
        """Root 자손 트리 — 모든 노드를 get_issue_light/_search(티켓 단위 캐시)로 조회.
        노드는 경량 필드(_node_from_issue)만 쓰므로 전 구간 경량으로 받는다(Epic 자식 검색·서브태스크 모두)."""
        try:
            if itype == "Epic":
                children = self._search(f'"Epic Link" = {key}',
                                        cache_key=f"epic_children:{self.env}:{key}")
                return [self._node_from_issue(c) for c in children]
            root = self.get_issue_light(key)
            subs = ((root.get("fields") or {}).get("subtasks")) or []
            self.prefetch_issues([s.get("key") for s in subs], light=True)   # 자식 원본을 한 번에(경량)
            return [self._node_from_issue(self.get_issue_light(s["key"])) for s in subs if s.get("key")]
        except Exception:
            # 트리를 통째로 못 만들었다 — 빈 트리로 내려가되 '못 만들었다' 는 남긴다.
            self.miss_add()
            return []

    def _issue_comments(self, key, limit=5):
        """코멘트 — `comments:{env}:{key}` 로 티켓 단위 캐시."""
        def do():
            data = self.provider.get_json(
                f"/rest/api/2/issue/{key}/comment",
                params={"maxResults": limit, "orderBy": "-created", "expand": "renderedBody"})
            out = []
            for c in data.get("comments", [])[:limit]:
                rb = c.get("renderedBody")
                html = sanitize_html(_revive_checkboxes(rb)) if rb and str(rb).strip() else text_to_html(c.get("body") or "")
                html = tidy_html(html)              # 빈 문단·앞뒤 공백 정리(과도 여백 제거)
                html = _sync_checkboxes(html, c.get("body"))   # 체크박스 상태·id 는 raw 원문 기준
                html = shorten_mention_names(html)  # 맨션은 본명만(에디터 표기와 일치)
                html = self._proxy_media(html)      # prod: 코멘트 내 이미지도 프록시
                out.append({
                    "id": str(c.get("id") or ""),       # 수정/삭제용
                    "date": c.get("created") or "",     # 전체 datetime(프론트에서 yy.mm.dd hh:mm 포맷)
                    "updated": c.get("updated") or "",  # 수정 표시용(created 와 다르면 '수정됨')
                    "author": real_name((c.get("author") or {}).get("displayName")
                                        or (c.get("author") or {}).get("name")),
                    "authorId": (c.get("author") or {}).get("name"),   # 프로필 이미지·본인 판정용
                    "html": html,       # 정화된 코멘트 HTML (맨션·링크·서식 포함)
                })
            return out
        return self.cache.get_or_set(f"comments:{self.env}:{key}", self.s.cache_ttl_seconds, do)[0]

    # ── 범용 단일 리소스 (env 무관 — /api/issue·/api/epic 리소스 엔드포인트용) ──
    def issue_detail(self, key):
        """단일 티켓 상세 노드(요약·타입·상태·일정·SP + Sub-Task). 없으면 None."""
        raw = self.get_issue(key)
        return _display_node(raw, self.s.sp_field_id, with_subs=True) if raw else None

    def issue_comments(self, key, limit=5):
        """단일 티켓 코멘트 — mock/local/prod 동일 형태."""
        return self._issue_comments(key, limit)

    # ── 쓰기(편집) — 코멘트 작성/수정/삭제 + 첨부 업로드 ────────────────────
    # 앱 최초의 쓰기 경로. body 는 **Jira wiki markup**(라우트에서 markdown→wiki 변환 후 전달).
    # 성공 시 해당 티켓 캐시를 비워 다음 조회가 최신을 읽게 한다. XSRF 는 provider 가 처리.
    def _reprime(self, key, *, comments=False):
        """비운 캐시를 **쓰기 우선순위로 즉시 다시 채운다**.

        비우기만 하면 다음 조회가 일반 우선순위로 줄을 서고, 앞에 읽기가 밀려 있으면 글은
        올라갔는데 화면이 한참 그대로다 — 사용자에겐 실패한 것과 같다. 쓰기와 그 반영은
        한 묶음으로 다뤄야 한다. 실패해도 조용히 넘어간다(다음 조회가 어차피 받아 온다).
        """
        def job():
            try:
                with write_upstream():
                    self.get_issue(key)
                    self._get_issue_view(key)
                    if comments:
                        self._issue_comments(key)
            except Exception:
                pass
        self._ensure_bg()
        self._bg_pool.submit(job)

    def invalidate_ticket_all(self, key):
        """이 티켓과 관련된 **모든 파생 캐시**를 비운다 — 좌하단 강제 새로고침용.
        (출력 형태가 바뀌는 배포 뒤엔 SWR 이 옛 결과를 계속 내주므로, 사용자가 이걸로 한 번에
        털 수 있어야 한다. 예: 하위 표시 상한 20→300 배포 후에도 옛 20 개 캐시가 남아 보이던 문제.)
        prefix 삭제라 epic_children:{key} 는 :light 변형까지 함께 지워진다."""
        env = self.env
        for pfx in ("issue", "issueL", "issueview", "comments", "children", "siblings",
                    "ancestors", "related", "timeline", "attachments", "documents",
                    "remotelinks", "epic_children", "changelog", "editmeta"):
            self.cache.invalidate(f"{pfx}:{env}:{key}")
        self._reprime(key, comments=True)
        return {"ok": True}

    def _invalidate_ticket(self, key, *, comments=False, attachments=False,
                           links=False, documents=False):
        if comments:
            self.cache.invalidate(f"comments:{self.env}:{key}")
        if attachments:
            self.cache.invalidate(f"issue:{self.env}:{key}")        # attachment 필드가 여기 캐시됨
            self.cache.invalidate(f"attachments:{self.env}:{key}")
        if links:
            self.cache.invalidate(f"issue:{self.env}:{key}")        # issuelinks 필드가 여기 캐시됨
            self.cache.invalidate(f"related:{self.env}:{key}")
        if documents:
            self.cache.invalidate(f"remotelinks:{self.env}:{key}")
            self.cache.invalidate(f"documents:{self.env}:{key}")
        # 본문/상태/댓글은 화면이 곧바로 다시 그린다 → 미리 채워 둔다(위 주석 참고).
        self.cache.invalidate(f"issueview:{self.env}:{key}")
        self.cache.invalidate(f"issueL:{self.env}:{key}")           # 경량 이슈 캐시도 함께(뱃지·형제·자식)
        self._reprime(key, comments=comments)

    # ── 편집 ──────────────────────────────────────────────────────────
    # **무엇을 고칠 수 있는지는 Jira 가 정한다.** 우리가 추측하면(예: 담당자면 다 된다) 화면은
    # 열어 놓고 저장에서 거절당한다. editmeta 는 "지금 이 사용자가 이 이슈에서 편집 가능한
    # 필드" 만 돌려주므로, 그 목록에 없는 필드는 아예 편집 UI 를 열지 않는다.
    # 캐시하지 않는다 — 상태가 바뀌면(워크플로 속성) 편집 가능 필드도 바뀐다.
    EDITABLE = {"summary", "description", "priority", "assignee", "reporter", "duedate",
                "labels", "components", "issuetype", "epic"}

    def editmeta(self, key):
        """편집 가능한 필드 → {필드id: {name, type, required, operations, allowedValues}}.
        실패하면 빈 dict(=아무것도 못 고침). 조용히 열어 주는 것보다 조용히 닫는 게 안전하다."""
        try:
            data = self.provider.get_json(f"/rest/api/2/issue/{key}/editmeta") or {}
        except SessionExpired:
            raise
        except Exception:
            return {}
        out = {}
        for fid, f in (data.get("fields") or {}).items():
            sch = f.get("schema") or {}
            typ = (sch.get("type") or "").lower()
            if typ == "array":
                typ = (sch.get("items") or "").lower() + "[]"
            out[fid] = {
                "id": fid, "name": f.get("name") or fid, "type": typ,
                "required": bool(f.get("required")),
                "operations": f.get("operations") or [],
                "allowedValues": [{"id": str(v.get("id") or ""),
                                   "name": v.get("name") or v.get("value") or ""}
                                  for v in (f.get("allowedValues") or [])],
            }
        return out

    def update_fields(self, key, fields):
        """필드 수정(PUT /issue). fields 는 Jira 형식 그대로 — 변환은 라우트가 한다."""
        self.provider.put_json(f"/rest/api/2/issue/{key}", {"fields": fields})
        self._invalidate_ticket(key)
        return {"ok": True}

    def toggle_description_checkbox(self, key, index, checked, cbid=None):
        """본문(description)의 대상 체크박스를 checked 상태로 토글하고 저장한다.

        사내 Jira(JEDITOR)는 체크박스를 원문에 `<input … type="checkbox">` HTML 로 저장한다.
        그래서 wiki 변환을 거치지 않고 **원문을 그대로 받아 그 체크박스 하나만 뒤집어** PUT 한다
        (나머지 서식은 손대지 않는다 — 최소 변경). id 우선 + index 폴백으로 대상을 짚는다.
        ★ 캐시 아닌 **최신 원문**을 provider 로 직접 읽는다(description 만) — 그 사이 다른 변경을 덮어쓰지 않게."""
        raw = self.provider.get_json(f"/rest/api/2/issue/{key}", params={"fields": "description"}) or {}
        body = (raw.get("fields") or {}).get("description") or ""
        new_body = _set_checkbox(body, index, checked, cbid=cbid)
        if new_body is None:
            raise ValueError("본문에서 해당 체크박스를 찾지 못했습니다.")
        self.provider.put_json(f"/rest/api/2/issue/{key}",
                               {"fields": {"description": new_body}})
        self._invalidate_ticket(key)
        return {"ok": True}

    def toggle_comment_checkbox(self, key, comment_id, index, checked, cbid=None):
        """코멘트의 대상 체크박스를 토글하고 저장한다(본문과 같은 원리 — id 우선 + index 폴백).
        ★ 단일 코멘트 GET 은 일부 서버(fake 포함)에서 405 → **리스트에서 id 로** 최신 원문을 찾는다."""
        data = self.provider.get_json(f"/rest/api/2/issue/{key}/comment",
                                      params={"maxResults": 1000, "orderBy": "-created"}) or {}
        body = ""
        for c in data.get("comments", []):
            if str(c.get("id")) == str(comment_id):
                body = c.get("body") or ""
                break
        new_body = _set_checkbox(body, index, checked, cbid=cbid)
        if new_body is None:
            raise ValueError("코멘트에서 해당 체크박스를 찾지 못했습니다.")
        self.provider.put_json(f"/rest/api/2/issue/{key}/comment/{comment_id}",
                               {"body": new_body})
        self._invalidate_ticket(key, comments=True)   # 코멘트 캐시까지 비워야 다시 읽을 때 반영
        return {"ok": True}

    OPTIONS_TTL = 1800          # 우선순위·컴포넌트는 거의 안 바뀐다

    def priorities(self):
        def do():
            return [{"id": str(x.get("id")), "name": x.get("name") or ""}
                    for x in (self.provider.get_json("/rest/api/2/priority") or [])]
        try:
            return self.cache.get_or_set(f"priorities:{self.env}", self.OPTIONS_TTL, do)[0]
        except Exception:
            return []

    def issue_types(self):
        """이 인스턴스의 **전역** 이슈 타입 — [{name, subtask}]. 이름은 인스턴스마다 다르므로
        **subtask 플래그**로 갈라야 한다(우리 world 는 'Sub-Task', 다른 곳은 '하위 작업').
        ※ 전역 목록이라 prod 에선 전사 타입이 다 섞인다 — 생성 UI 는 project_issue_types 를 써라."""
        def do():
            return [{"name": x.get("name") or "", "subtask": bool(x.get("subtask"))}
                    for x in (self.provider.get_json("/rest/api/2/issuetype") or [])]
        try:
            return self.cache.get_or_set(f"issuetypes:{self.env}", self.OPTIONS_TTL, do)[0]
        except Exception:
            return []

    def project_issue_types(self):
        """이 **프로젝트에서 실제로 만들 수 있는** 이슈 타입 — createmeta 기반.
        전역 /issuetype 은 전사 타입까지 섞여 나와(prod), DL 에 없는 낯선 타입이 생성창에 뜬다.
        createmeta 가 비었거나 막히면(구버전·권한·mock) 전역 목록으로 폴백한다."""
        def do():
            pk = self.s.project_key
            try:
                data = self.provider.get_json(
                    "/rest/api/2/issue/createmeta",
                    params={"projectKeys": pk, "expand": "projects.issuetypes"}) or {}
                projs = data.get("projects") or []
                for pr in projs:
                    if (pr.get("key") or "") == pk or len(projs) == 1:
                        its = pr.get("issuetypes") or []
                        if its:
                            return [{"name": x.get("name") or "", "subtask": bool(x.get("subtask"))}
                                    for x in its]
            except Exception:
                pass
            return [{"name": x.get("name") or "", "subtask": bool(x.get("subtask"))}
                    for x in (self.provider.get_json("/rest/api/2/issuetype") or [])]
        try:
            return self.cache.get_or_set(f"projtypes:{self.env}", self.OPTIONS_TTL, do)[0]
        except Exception:
            return self.issue_types()

    def child_types(self, parent_key):
        """이 티켓 **밑에** 만들 수 있는 타입.

        Jira 의 계층은 Epic → 일반 이슈 → Sub-Task 셋뿐이다. 그래서 부모가 무엇인지가 곧
        답이다: Epic 밑은 일반 이슈(Sub-Task·Epic 제외), 일반 이슈 밑은 Sub-Task,
        Sub-Task 밑은 없다. 이름으로 판정하지 않는다 — 로케일·인스턴스마다 다르다.
        """
        b = self.ticket_badge(parent_key) or {}
        types = self.project_issue_types()          # 전사 타입이 아니라 **이 프로젝트** 것만
        if not types:
            return []
        raw = self.get_issue(parent_key) or {}
        it = ((raw.get("fields") or {}).get("issuetype") or {})
        if it.get("subtask"):
            return []                                   # Sub-Task 밑은 없다
        if (b.get("type") or "") == "Epic":
            return [t["name"] for t in types if not t["subtask"] and t["name"] != "Epic"]
        return [t["name"] for t in types if t["subtask"]]

    def desc_field_value(self, html):
        """에디터 HTML → description 필드에 저장할 값(형식이 환경마다 다르다).

        사내 **prod** 은 description 이 JEditor(HTML) 필드라 HTML 을 그대로(정화해) 넣어야 한다 —
        wiki 로 넣으면 화면에서 'h3.' 이 글자로 남고, 줄바꿈이 뭉치고, '<' 뒤가 태그로 먹혀 유실된다
        (실제 리포트된 버그). **mock/local(jira820)** 은 wiki 필드라 html_to_wiki 로 변환한다.
        settings.description_format('html'|'wiki')로 강제 지정 가능. 빈 입력은 None(설명 지움).
        """
        if not html:
            return None
        from .wikihtml import html_to_wiki
        fmt = (getattr(self.s, "description_format", "") or "").lower()
        if fmt not in ("html", "wiki"):
            fmt = "html" if self.env == "prod" else "wiki"
        return sanitize_html(html) if fmt == "html" else html_to_wiki(html)

    def create_child(self, parent_key, itype, summary, priority=None,
                     duedate=None, assignee=None, components=None, description=None):
        """하위/독립 티켓 생성. 부모가 Epic 이면 Epic Link 로, 일반 이슈면 parent(Sub-Task)로 잇는다.
        **parent_key 가 없으면(=None) Epic 없이 최상위 Task** 로 만든다('Epic 없음'/'사용자 VoC').

        생성 결과 키를 돌려준다. 실패는 그대로 올린다 — 조용히 삼키면 사용자는 만들어진 줄 안다.
        description 은 wiki 문자열(라우트가 html_to_wiki 로 변환해 넘긴다).
        """
        fields = {
            "project": {"key": self.s.project_key},
            "issuetype": {"name": itype},
            "summary": summary,
        }
        if description:
            fields["description"] = description
        if priority:
            fields["priority"] = {"name": priority}
        if duedate:
            fields["duedate"] = duedate
        if assignee:
            fields["assignee"] = {"name": assignee}
        if components:
            # 컴포넌트는 곧 **모듈**이다(이 프로젝트의 롤업 축). 비워 두면 새 티켓이 어느 모듈에도
            # 안 잡혀 WBS·워크로드에서 사라진다 — 화면이 부모 것을 기본값으로 채워 보낸다.
            # ('사용자 VoC' 는 모듈이 아닌 컴포넌트로, Epic 없이 이 값만 넣어 구분한다.)
            fields["components"] = [{"name": c} for c in components if c]
        if parent_key:
            b = self.ticket_badge(parent_key) or {}
            if (b.get("type") or "") == "Epic":
                fields[self.s.epic_link_field_id] = parent_key
            else:
                fields["parent"] = {"key": parent_key}
        # 쓰기는 큐 맨 앞으로 — 뒤에 밀리면 사용자는 '만들어졌나?' 하고 또 누른다.
        with write_upstream():
            res = self.provider.post_json("/rest/api/2/issue", {"fields": fields})
        key = (res or {}).get("key")
        if parent_key:
            # 부모의 자식 목록이 낡았다 — 새로 만든 게 바로 안 보이면 만들어졌는지 알 수 없다.
            # ★ children 만 지워선 안 된다. 하위 목록의 출처는 부모 원본의 subtasks 필드(Epic 이면
            #   Epic Link 검색)라, issue: 캐시가 남아 있으면 새 자식이 **영영 안 보인다**.
            self.cache.invalidate(f"issue:{self.env}:{parent_key}")
            self.cache.invalidate(f"issueL:{self.env}:{parent_key}")     # 경량 캐시(형제·자식키 출처)도 함께
            self.cache.invalidate(f"children:{self.env}:{parent_key}")
            # Epic 은 자식을 JQL 로 찾는다 — 그 결과도 캐시라 함께 버려야 새 Task 가 보인다.
            # (prefix 삭제라 epic_children:{parent} 와 :light 변형까지 같이 지워진다.)
            self.cache.invalidate(f"epic_children:{self.env}:{parent_key}")
            self._invalidate_ticket(parent_key)
        else:
            # 독립 Task — 부모가 없다. 대신 이 Task 를 담을 목록/검색 캐시를 넓게 버린다
            # (워크로드·내Task·현안·검색이 낡은 채로 새 Task 를 안 보여 주지 않게).
            for pfx in ("wbs_build", "vit_bases", "vit_list", "workload", "mytasks", "search"):
                self.cache.invalidate(f"{pfx}:")
        return {"key": key}

    def task_types(self):
        """Epic 없이 만들 수 있는 **최상위 티켓 타입** — Sub-Task·Epic 을 뺀 일반 이슈들.
        ('Epic 없음' Task 생성용. 프로젝트 createmeta 기반이라 전사 타입이 새지 않는다.)"""
        return [t["name"] for t in self.project_issue_types()
                if not t.get("subtask") and (t.get("name") or "") != "Epic"]

    def create_epic(self, summary, priority=None, duedate=None, assignee=None,
                    components=None, description=None, epic_name=None):
        """최상위 Epic 생성(부모 없음). Epic Name(단축어) 커스텀 필드가 있으면 채운다.
        생성 후 목록/검색 캐시가 낡으므로 관련 캐시를 넓게 버린다."""
        etype = next((t["name"] for t in self.issue_types()
                      if (t.get("name") or "").lower() == "epic"), "Epic")
        fields = {
            "project": {"key": self.s.project_key},
            "issuetype": {"name": etype},
            "summary": summary,
        }
        if description:
            fields["description"] = description
        if priority:
            fields["priority"] = {"name": priority}
        if duedate:
            fields["duedate"] = duedate
        if assignee:
            fields["assignee"] = {"name": assignee}
        if components:
            fields["components"] = [{"name": c} for c in components if c]
        # Epic Name(단축어) — 검색·뱃지가 요약이 아니라 이 이름을 쓴다. 없으면 요약으로.
        enf = getattr(self.s, "epic_name_field_id", None)
        if enf:
            fields[enf] = epic_name or summary
        with write_upstream():
            res = self.provider.post_json("/rest/api/2/issue", {"fields": fields})
        key = (res or {}).get("key")
        # WBS/현안/에픽 목록이 이 Epic 을 담으려면 관련 목록 캐시를 비운다(넓게).
        for pfx in ("wbs_build", "vit_bases", "vit_list", "epic_options"):
            self.cache.invalidate(f"{pfx}:")
        return {"key": key}

    def set_epic_link(self, epic_key, task_keys):
        """기존 Task 들을 Epic 에 넣는다(Epic Link 설정). 각자 PUT — 하나 실패해도 나머지는 진행.
        반환: {ok, linked:[…], failed:[{key,error}]}."""
        enf = self.s.epic_link_field_id
        linked, failed = [], []
        for k in task_keys or []:
            k = (k or "").strip()
            if not k:
                continue
            try:
                self.provider.put_json(f"/rest/api/2/issue/{k}", {"fields": {enf: epic_key}})
                self._invalidate_ticket(k)
                self.cache.invalidate(f"issue:{self.env}:{k}")
                self.cache.invalidate(f"issueL:{self.env}:{k}")
                linked.append(k)
            except Exception as e:
                failed.append({"key": k, "error": str(e)[:200]})
        # 이 Epic 의 자식 목록 캐시 무효화(새로 든 Task 가 보이게)
        self.cache.invalidate(f"epic_children:{self.env}:{epic_key}")
        self.cache.invalidate(f"children:{self.env}:{epic_key}")
        return {"ok": not failed, "linked": linked, "failed": failed}

    def epic_candidates(self, q="", limit=20):
        """Epic 에 넣을 **기존 Task 후보** — 일반 이슈(Epic·Sub-Task 제외). q 로 키/제목 필터.
        JQL 을 단순하게(project + 최신순) 두고 타입/검색은 파이썬에서 거른다 — mock/prod 공통 안전."""
        pk = self.s.project_key
        raws = self._search(f"project = {pk} ORDER BY updated DESC", max_results=200) or []
        enf = self.s.epic_link_field_id
        ql = (q or "").strip().lower()
        out = []
        for it in raws:
            f = it.get("fields") or {}
            itype = f.get("issuetype") or {}
            tname = itype.get("name") or ""
            if tname == "Epic" or itype.get("subtask"):
                continue
            key = it.get("key") or ""
            summ = f.get("summary") or key
            if ql and ql not in key.lower() and ql not in summ.lower():
                continue
            out.append({
                "key": key, "summary": summ, "type": tname,
                "statusCategory": _norm_cat(((f.get("status") or {}).get("statusCategory") or {}).get("key")),
                "epicKey": f.get(enf) or None,
            })
            if len(out) >= max(1, min(limit, 100)):
                break
        return {"items": out}

    def components(self):
        def do():
            path = f"/rest/api/2/project/{self.s.project_key}/components"
            return [{"id": str(x.get("id")), "name": x.get("name") or ""}
                    for x in (self.provider.get_json(path) or [])]
        try:
            return self.cache.get_or_set(f"components:{self.env}", self.OPTIONS_TTL, do)[0]
        except Exception:
            return []

    def epic_options(self, q=""):
        """Epic 후보 — **요약·Epic Name·키** 무엇으로 쳐도 찾는다(대소문자 무시). 편집 드롭다운용.

        Epic 목록 전체(수십 개)를 한 번 받아 **파이썬에서** 거른다. JQL 의 `"Epic Name" ~` 절은
        구버전/mock 에서 파싱이 안 될 수 있어, 매칭을 서버에 맡기면 Epic Name 으로만 맞는 항목이
        통째로 빠진다(요약만 검색됨). 목록은 캐시하므로 매 타건마다 prod 를 왕복하지 않는다.
        """
        q = (q or "").strip().lower()
        jql = f'project = {self.s.project_key} AND issuetype = Epic ORDER BY key'
        try:
            # q 와 무관한 고정 JQL → 한 번 받아 캐시(create_epic 이 'epic_options:' 프리픽스로 무효화).
            raws = self._search(jql, cache_key=f"epic_options:all:{self.env}", max_results=200) or []
        except Exception:
            return []
        nf = self.s.epic_name_field_id
        out = []
        for r in raws:
            f = r.get("fields") or {}
            key = r.get("key") or ""
            summ = f.get("summary") or ""
            # name = 단축어(Epic Name), summary = 요약. **둘 다 준다** — 목록에서 단축어만 보이면
            # 비슷한 이름끼리 구별이 안 되고, 요약만 보이면 평소 부르는 이름이 안 보인다.
            name = (f.get(nf) if nf else None) or summ or key
            if q and q not in key.lower() and q not in summ.lower() and q not in (name or "").lower():
                continue
            out.append({"key": key, "name": name, "summary": summ})
            if len(out) >= 20:
                break
        return out

    def label_suggestions(self, q=""):
        """라벨 자동완성. Jira DC 는 JQL 자동완성 엔드포인트로 준다.
        없으면(구버전·권한) 빈 목록 — 그때는 사용자가 새로 적어 넣으면 된다."""
        try:
            data = self.provider.get_json(
                "/rest/api/2/jql/autocompletedata/suggestions",
                params={"fieldName": "labels", "fieldValue": q or ""}) or {}
            return [x.get("value") or "" for x in (data.get("results") or []) if x.get("value")]
        except Exception:
            return []

    # ── 상태 전이 ──────────────────────────────────────────────────────
    # Jira 는 "상태를 지정" 하는 게 아니라 **워크플로가 허용한 전이 id** 를 실행한다. 그래서
    # 가능한 목록은 티켓의 현재 상태에 따라 매번 달라진다 → 캐시하지 않는다(낡은 목록은
    # 곧 400 이다). ?expand=transitions.fields 로 **전이 화면에 무엇을 물어야 하는지**까지
    # 받아 온다 — 이게 없으면 클라이언트는 필수 입력을 알 수 없다.
    DONE_PREFERRED = "resolved"        # 완료로 보낼 때 기본 목적지(§ 아래 주석)

    TIMECFG_TTL = 6 * 3600     # 인스턴스 설정이라 자주 안 바뀐다

    def timetracking(self):
        """이 인스턴스에서 **하루가 몇 시간인가**. '1d' 는 여기서 8h 가 될 수도 24h 가 될 수도
        있다(Jira 관리자 설정). 추측하면 사용자가 적은 '1d' 와 Jira 가 기록한 값이 달라진다 —
        읽어 와서 화면에 그대로 알린다. 못 읽으면 Jira DC 기본값(8h/일, 5일/주)."""
        def do():
            cfg = self.provider.get_json("/rest/api/2/configuration") or {}
            tt = cfg.get("timeTrackingConfiguration") or {}
            return {"hoursPerDay": float(tt.get("workingHoursPerDay") or 8),
                    "daysPerWeek": float(tt.get("workingDaysPerWeek") or 5),
                    "enabled": bool(cfg.get("timeTrackingEnabled", True))}
        try:
            return self.cache.get_or_set(f"timecfg:{self.env}", self.TIMECFG_TTL, do)[0]
        except Exception:
            return {"hoursPerDay": 8.0, "daysPerWeek": 5.0, "enabled": True}

    def transitions(self, key):
        """이 티켓에서 지금 가능한 전이 목록(+화면 필드). 실패하면 빈 목록."""
        try:
            data = self.provider.get_json(f"/rest/api/2/issue/{key}/transitions",
                                          params={"expand": "transitions.fields"})
        except SessionExpired:
            raise
        except Exception:
            return []
        out = []
        for t in (data or {}).get("transitions") or []:
            to = t.get("to") or {}
            cat = ((to.get("statusCategory") or {}).get("key") or "").lower()
            cat = {"new": "todo", "indeterminate": "inprogress", "done": "done"}.get(cat, cat)
            out.append({
                "id": str(t.get("id")), "name": t.get("name") or "",
                "to": to.get("name") or "", "toCategory": cat,
                "hasScreen": bool(t.get("hasScreen")),
                "fields": self._screen_fields(t.get("fields") or {}),
            })
        # 완료로 가는 길이 여럿이면(Resolved/Closed) **Resolved 를 먼저** 놓는다.
        # 우리 앱에서 '완료' 는 "작업자가 끝냈다" 이지 "검증까지 끝나 닫혔다" 가 아니다 —
        # Closed 는 확인 절차를 거친 쪽이므로 앱이 임의로 보낼 자리가 아니다.
        out.sort(key=lambda t: (t["toCategory"] != "done",
                                (t["to"] or "").lower() != self.DONE_PREFERRED))
        return out

    # 우리가 폼으로 그릴 수 있는 필드 타입만 통과시킨다. 모르는 필수 필드가 하나라도 있으면
    # 그 전이는 앱에서 처리하지 않고 Jira 로 넘긴다 — 사용자가 폼을 다 채운 뒤 400 을 맞는
    # 것보다, 처음부터 안 열어 주고 이유를 말하는 편이 낫다.
    RENDERABLE = {"worklog", "user", "resolution", "comment", "string", "number", "date",
                  "datetime", "option", "priority"}

    def _screen_fields(self, raw):
        out, unsupported = [], []
        for fid, f in (raw or {}).items():
            sch = f.get("schema") or {}
            # ★ 타입은 schema.type 으로 읽는다. system 을 먼저 보면 'assignee' 같은 **필드 이름**이
            #   타입 자리에 와서 렌더 가능한데도 미지원으로 떨어진다(실제로 그랬다).
            #   system 은 "이 필드가 무엇인가"(assignee/resolution/…)이고 type 은 "어떻게 입력하나"다.
            typ = (sch.get("type") or "").lower()
            if typ == "array":
                typ = (sch.get("items") or "").lower()
            system = (sch.get("system") or "").lower()
            item = {"id": fid, "name": f.get("name") or fid, "type": typ, "system": system,
                    "required": bool(f.get("required")),
                    "allowedValues": [{"id": str(v.get("id")), "name": v.get("name") or v.get("value") or ""}
                                      for v in (f.get("allowedValues") or [])]}
            if typ in self.RENDERABLE:
                out.append(item)
            elif item["required"]:
                unsupported.append(item["name"])
        if unsupported:
            return {"fields": out, "unsupported": unsupported}
        return {"fields": out, "unsupported": []}

    def do_transition(self, key, transition_id, *, time_spent=None, assignee=None,
                      resolution=None, comment=None):
        """전이 실행. 화면 필드는 Jira 규약대로 fields/update 에 나눠 싣는다
        (worklog·comment 는 '추가' 라 update, resolution·assignee 는 '설정' 이라 fields).

        ★ **그 전이의 화면에 있는 필드만 보낸다.** Jira 는 화면에 없는 필드를 넣으면 400 을
          낸다("Field 'resolution' cannot be set, it is not on the appropriate screen").
          Open→In Progress 처럼 화면이 없는 전이에는 아무것도 안 보내야 한다. 무엇이 화면에
          있는지는 transitions() 응답이 이미 알고 있으니 그걸로 거른다.
        """
        tid = str(transition_id)
        allowed = set()
        for t in (self.transitions(key) or []):
            if str(t.get("id")) == tid:
                allowed = {f.get("id") for f in (t.get("fields") or {}).get("fields", [])}
                break
        body = {"transition": {"id": tid}}
        fields, update = {}, {}
        if resolution and "resolution" in allowed:
            fields["resolution"] = {"name": resolution}
        if assignee and "assignee" in allowed:
            fields["assignee"] = {"name": assignee}
        if time_spent and "worklog" in allowed:
            update["worklog"] = [{"add": {"timeSpent": time_spent}}]
        # 코멘트는 화면에 없어도 대개 받아 준다(transition comment 는 특별 취급) — 다만 화면이
        # comment 를 선언했거나, 아무 제약 정보가 없을 때만 넣어 불필요한 400 을 피한다.
        if comment and ("comment" in allowed or not allowed):
            update["comment"] = [{"add": {"body": comment}}]
        if fields:
            body["fields"] = fields
        if update:
            body["update"] = update
        res = self.provider.post_json(f"/rest/api/2/issue/{key}/transitions", body)
        # 상태·담당·해결이 한꺼번에 바뀐다 → 본문/댓글을 즉시 다시 채운다(쓰기 우선순위).
        self._invalidate_ticket(key, comments=bool(comment))
        return res

    def add_comment(self, key, body):
        """코멘트 작성 (body = Jira wiki markup). 반환: 생성된 코멘트 객체."""
        res = self.provider.post_json(f"/rest/api/2/issue/{key}/comment", {"body": body})
        self._invalidate_ticket(key, comments=True)
        return res

    def update_comment(self, key, comment_id, body):
        """코멘트 수정 (본인 것). body = Jira wiki markup."""
        res = self.provider.put_json(
            f"/rest/api/2/issue/{key}/comment/{comment_id}", {"body": body})
        self._invalidate_ticket(key, comments=True)
        return res

    def delete_comment(self, key, comment_id):
        """코멘트 삭제 (본인 것)."""
        self.provider.delete(f"/rest/api/2/issue/{key}/comment/{comment_id}")
        self._invalidate_ticket(key, comments=True)
        return {"ok": True}

    def upload_attachment(self, key, filename, data, content_type=None):
        """첨부 단일 파일 업로드. 반환: 첨부 객체 리스트(확정 파일명·id — !파일명! 참조에 사용).
        이미지 붙여넣기 제출 시 파일당 한 번씩 호출한다."""
        res = self.provider.post_multipart(
            f"/rest/api/2/issue/{key}/attachments", filename, data, content_type)
        self._invalidate_ticket(key, attachments=True)
        return res

    def delete_attachment(self, attachment_id, key=None):
        """첨부 삭제 (제출 취소·부분실패 롤백용). key 주면 그 티켓 첨부 캐시도 무효화."""
        self.provider.delete(f"/rest/api/2/attachment/{attachment_id}")
        if key:
            self._invalidate_ticket(key, attachments=True)
        return {"ok": True}

    LINK_TYPES_TTL = 24 * 3600            # 링크 타입은 인스턴스 설정 — 거의 안 바뀐다

    def link_types(self):
        """이슈 링크 타입 — [{name, inward, outward}]. 링크 걸 때 관계 선택지로 쓴다.
        하드코딩 금지(인스턴스마다 다르다). 조회 실패 시 Jira 기본 4종으로 폴백."""
        def do():
            data = self.provider.get_json("/rest/api/2/issueLinkType")
            out = []
            for t in ((data or {}).get("issueLinkTypes") or []):
                if t.get("name"):
                    out.append({"name": t["name"], "inward": t.get("inward") or t["name"],
                                "outward": t.get("outward") or t["name"]})
            return out
        try:
            got = self.cache.get_or_set(f"linktypes:{self.env}", self.LINK_TYPES_TTL, do)[0]
        except Exception:
            got = []
        return got or [
            {"name": "Relates", "inward": "relates to", "outward": "relates to"},
            {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
            {"name": "Duplicate", "inward": "is duplicated by", "outward": "duplicates"},
            {"name": "Cloners", "inward": "is cloned by", "outward": "clones"},
        ]

    def add_issue_link(self, key, other_key, type_name, direction="outward"):
        """이슈 링크 생성. direction=outward 면 `key --(type.outward)--> other_key`.
        (예: Blocks/outward = 이 티켓이 상대를 막는다. inward 면 반대.)"""
        if not other_key or other_key.upper() == (key or "").upper():
            raise ValueError("자기 자신에는 링크할 수 없습니다.")
        inward, outward = (key, other_key) if direction == "outward" else (other_key, key)
        self.provider.post_json("/rest/api/2/issueLink", {
            "type": {"name": type_name or "Relates"},
            "inwardIssue": {"key": inward}, "outwardIssue": {"key": outward}})
        # 양쪽 다 무효화 — 상대 티켓에서도 곧바로 보여야 한다
        self._invalidate_ticket(key, links=True)
        self._invalidate_ticket(other_key, links=True)
        return {"ok": True}

    def delete_issue_link(self, link_id, key=None, other_key=None):
        self.provider.delete(f"/rest/api/2/issueLink/{link_id}")
        for k in (key, other_key):
            if k:
                self._invalidate_ticket(k, links=True)
        return {"ok": True}

    def add_remote_link(self, key, url, title="", relationship=""):
        """원격 링크(Confluence 문서·웹 링크) 추가 — '관련문서'에 붙는다.
        globalId 를 URL 로 두면 같은 문서를 다시 걸어도 한 줄로 갱신된다(실 Jira 동작)."""
        url = (url or "").strip()
        if not url:
            raise ValueError("URL 이 필요합니다.")
        body = {"globalId": url,
                "object": {"url": url, "title": (title or "").strip() or url}}
        if relationship:
            body["relationship"] = relationship
        res = self.provider.post_json(f"/rest/api/2/issue/{key}/remotelink", body)
        self._invalidate_ticket(key, documents=True)
        return res or {"ok": True}

    def delete_remote_link(self, key, link_id):
        self.provider.delete(f"/rest/api/2/issue/{key}/remotelink/{link_id}")
        self._invalidate_ticket(key, documents=True)
        return {"ok": True}

    def comment_source(self, key, comment_id):
        """코멘트 원본(wiki) → 에디터용 HTML. 수정 로드 시 사용. 없으면 None.
        (단일 코멘트 GET 은 일부 서버에서 미지원 → 리스트에서 id 로 찾아 견고하게.)"""
        from .wikihtml import wiki_to_html
        data = self.provider.get_json(
            f"/rest/api/2/issue/{key}/comment",
            params={"maxResults": 1000, "orderBy": "-created"})
        for c in data.get("comments", []):
            if str(c.get("id")) == str(comment_id):
                return {"id": str(comment_id),
                        "html": wiki_to_html(c.get("body") or "", self._mention_name)}
        return None

    def _display_name(self, uid):
        """사용자 표시이름 — 사번 uid → displayName('{본명} {회사}', best-effort, 캐시). 실패 시 uid.
        동명이인 구분이 필요한 곳(@멘션 팝업)은 회사까지 붙은 이 값을 쓴다."""
        def do():
            try:
                u = self.provider.get_json("/rest/api/2/user", params={"username": uid})
                return u.get("displayName") or uid
            except Exception:
                return uid
        try:
            return self.cache.get_or_set(f"udisp:{self.env}:{uid}", self.s.cache_ttl_seconds, do)[0]
        except Exception:
            return uid

    def _mention_name(self, uid):
        """멘션 라벨 — 본명만(본문에 박히는 이름이라 짧아야 한다)."""
        return real_name(self._display_name(uid)) or uid

    # ── 상류(Jira) 상태 ─────────────────────────────────────────────────
    # 세션이 죽었는데도 호출마다 붙어 보면, prod 는 실패 판정에만 최대 JOB_TIMEOUT(180초)를
    # 쓴다. 화면이 그만큼 멎는다. 한 번 실패하면 잠깐 '상류 죽음' 으로 표시해 두고 그동안은
    # 캐시로 답한다(회로차단기). 성공하면 즉시 해제한다.
    UPSTREAM_DOWN_FOR = 20        # 초 — 이 동안은 상류에 붙지 않는다

    def upstream_down(self):
        return time.time() < getattr(self, "_upstream_down_until", 0)

    def mark_upstream_down(self, reason=""):
        self._upstream_down_until = time.time() + self.UPSTREAM_DOWN_FOR
        self._upstream_reason = reason or "상류 응답 없음"

    def mark_upstream_ok(self):
        self._upstream_down_until = 0
        self._upstream_reason = ""

    def upstream_state(self):
        """화면 상단 알림이 쓸 상태. authed 는 세션 사용자를 읽을 수 있는지로 본다."""
        return {
            "down": self.upstream_down(),
            "reason": getattr(self, "_upstream_reason", ""),
            "lastSyncAt": self.cache.last_upstream_ok or None,
            "servedStaleAt": self.cache.served_stale_at or None,
            "hasCache": self.cache.has_any(),
        }

    def current_user(self):
        """세션 사용자 — 본인 댓글(수정/삭제 노출)·매니저 판정용. {id, name}. **성공만** 캐시.

        ★ 실패를 캐시하지 마라. 예전엔 예외를 삼켜 빈 dict 를 돌려줬고, 그 빈 값이 TTL(기본
          15분) 동안 캐시에 눌러앉았다. 그래서 prod 첫 실행에서 세션이 아직 없을 때 한 번
          실패하면, 그 뒤 SSO 로그인에 성공해 로그에 '인증됨' 이 찍혀도 앱은 15분 내내
          "세션 사용자 없음" 으로 봤다 — 매니저 판정이 계속 False 라 매니저 전용 화면이
          403 이었고, 새로고침해도 풀리지 않는 '인증 오류' 의 정체가 이것이었다.
          producer 가 예외를 내면 get_or_set 은 저장하지 않으므로, 밖에서 삼킨다.
        """
        def do():
            u = self.provider.get_json("/rest/api/2/myself")
            return {"id": u.get("name") or u.get("key") or "",
                    "name": real_name(u.get("displayName") or u.get("name")) or "",
                    # display = '{본명} {회사}' 원본 — 동명이인 구분이 필요한 화면(담당자 선택 등)용.
                    # name 은 짧은 본명이라 소속을 알 수 없다.
                    "display": u.get("displayName") or u.get("name") or ""}
        try:
            return self.cache.get_or_set(f"myself:{self.env}", self.s.cache_ttl_seconds, do)[0]
        except Exception:
            return {}

    def session_alive(self):
        """세션이 **정말** 살아 있는가 — /myself 를 캐시 없이 직접 물어본다.

        개별 요청의 401 은 세션이 죽어서가 아닐 때가 많다(XSRF 거절, 특정 엔드포인트 권한,
        상류의 순간 오류). 그걸 세션 만료로 단정하면 로그인 흐름이 돌고 인증 창이 뜬다.
        여기서 한 번 확인해, /myself 가 되면 세션은 산 것으로 본다.
        """
        try:
            u = self.provider.get_json("/rest/api/2/myself")
            if not isinstance(u, dict):
                return False
            # 익명 응답이거나 이름이 없으면 세션이 아니다. (Jira 는 미인증에 200+익명 객체를
            # 주기도 한다 — status 코드만 보면 살아 있는 것처럼 보인다.)
            name = u.get("name") or u.get("key") or ""
            if not name or "anonymous" in str(name).lower():
                return False
            return True
        except Exception:
            return False

    def ticket_badge(self, key):
        """티켓 인라인 뱃지용 경량 요약(요약/타입/상태/담당자). 없으면 None. (renderedFields 미포함=가벼움)"""
        try:
            raw = self.get_issue_light(key)      # 뱃지는 경량 필드만 → 전체 캐시 있으면 재사용, 없으면 경량
        except Exception:
            return None
        if not isinstance(raw, dict) or "fields" not in raw:
            return None
        f = raw.get("fields") or {}
        st = f.get("status") or {}
        a = f.get("assignee") or {}
        return {
            "key": raw.get("key", key),
            "summary": f.get("summary", ""),
            "type": (f.get("issuetype") or {}).get("name", ""),
            "status": st.get("name", ""),
            "statusCategory": _norm_cat((st.get("statusCategory") or {}).get("key")),
            "assignee": real_name(a.get("displayName") or a.get("name")) or None,
            # 마감·완료일 — 하위 Task 목록이 '언제까지'를 같이 보여 준다. 이미 받아 온 필드라
            # 왕복이 늘지 않는다(뱃지 하나 때문에 다시 묻지 않는다).
            "due": f.get("duedate") or None,
            "resolved": f.get("resolutiondate") or None,
            # 우선순위 — 하위 목록의 첫 칸. 이름과 등급을 함께 준다(그림은 등급, 툴팁은 이름).
            "priority": (f.get("priority") or {}).get("name") or None,
            "priRank": _pri_rank((f.get("priority") or {}).get("name")),
            # 아래 둘은 **권한 판정 재료**다(내 티켓인가?). 표시용 이름과 달리 id 여야 한다 —
            # 표시이름은 동명이인이 있어 사람을 특정하지 못한다.
            "assigneeId": a.get("name") or a.get("key") or None,
            "reporterId": ((f.get("reporter") or {}).get("name")
                           or (f.get("reporter") or {}).get("key") or None),
        }

    # ── 담당자 ────────────────────────────────────────────────────────
    def set_assignee(self, key, user_id):
        """담당자 지정/해제. user_id 가 비면 해제(Jira 는 name=null 로 받는다)."""
        self.provider.put_json(f"/rest/api/2/issue/{key}/assignee",
                               {"name": user_id or None})
        self._invalidate_ticket(key)
        return {"ok": True}

    def delete_issue(self, key):
        """티켓 삭제. 되돌릴 수 없다 — 호출부(라우트/화면)가 확인을 받는다."""
        self.provider.delete(f"/rest/api/2/issue/{key}")
        # 이 티켓의 캐시를 전부 버린다. 살아 있으면 지워진 티켓이 목록에 계속 남는다.
        for pre in ("issue", "issueview", "comments", "attachments", "documents",
                    "remotelinks", "timeline", "changelog", "children", "related",
                    "siblings", "ancestors"):
            self.cache.invalidate(f"{pre}:{self.env}:{key}")
        return {"ok": True}

    def ticket_view(self, key, fresh=False):
        """티켓 상세 다이얼로그용 리치 뷰 — 없으면 None. description 은 항상 정화된 안전 HTML.

        fresh=True 면 캐시를 건너뛰고 상류를 직접 본다(본문 편집 시작·충돌 감시).
        """
        raw = self._get_issue_view(key, fresh=fresh)
        if not raw:
            return None
        view = _build_ticket_view(raw, self.s.sp_field_id, self.s.jira_base,
                                  self.s.epic_link_field_id)
        view["descriptionHtml"] = self._proxy_media(view["descriptionHtml"])
        # 섹션은 프록시 이전 HTML 에서 잘렸다 — 이미지가 든 섹션도 프록시를 타야 한다
        for sec in view.get("descriptionSections") or []:
            sec["html"] = self._proxy_media(sec["html"])
        _log_sections(key, view)
        return view

    # ── 계보(좌측 스파인 패널) — 조상 체인 + 형제 ──
    # 모든 조회는 티켓단위 캐시(get_issue / epic_children write-through)를 재사용하고,
    # 조립 결과도 ancestors|siblings:{env}:{key} 로 캐시한다.
    def _lineage_node(self, key, pct=None):
        b = self.ticket_badge(key)
        if not b:
            return None
        return {"key": b["key"], "summary": b["summary"], "type": b["type"],
                "status": b["status"], "statusCategory": b["statusCategory"],
                "assignee": b["assignee"], "pct": pct}

    def _parent_pct(self, key):
        """부모(Task/Story)의 진척 — 자식 Sub-Task 완료 개수 비율(%). 자식 없으면 None."""
        try:
            subs = (self.get_issue(key).get("fields") or {}).get("subtasks") or []
        except Exception:
            return None
        if not subs:
            return None
        done = sum(1 for s in subs
                   if _norm_cat(((((s.get("fields") or {}).get("status") or {})
                                  .get("statusCategory")) or {}).get("key")) == "done")
        return round(done * 100 / len(subs))

    def ticket_ancestors(self, key):
        """조상 체인(위→아래): Sub-Task → [epic, parent] / Story·Task·Bug → [epic] / Epic → [].
        각 노드에 진척률(pct)까지 얹는다 — Epic 은 SP 롤업, 부모는 Sub-Task 완료 비율."""
        def build():
            try:
                f = self.get_issue(key).get("fields") or {}
            except Exception:
                return []
            parent_key = (f.get("parent") or {}).get("key")
            epic_key = f.get(self.s.epic_link_field_id)
            chain = []
            if parent_key:                      # Sub-Task: 부모의 Epic Link 를 조부모로 함께
                try:
                    epic_key = (self.get_issue(parent_key).get("fields")
                                or {}).get(self.s.epic_link_field_id)
                except Exception:
                    epic_key = None
                if epic_key:
                    chain.append((epic_key, self._epic_pct(epic_key)))
                chain.append((parent_key, self._parent_pct(parent_key)))
            elif epic_key:
                chain.append((epic_key, self._epic_pct(epic_key)))
            nodes = [n for n in (self._lineage_node(k, p) for k, p in chain) if n]
            if not nodes and _comp_of(f) == VOC_COMPONENT:
                # VoC 는 Epic/부모에 안 붙는 경우가 많아 계보가 통째로 비어 버린다.
                # 실제 티켓은 아니지만 '어디 소속인지' 는 보여주는 게 낫다 → 가상 상위 노드.
                nodes = [{"key": None, "summary": VOC_COMPONENT, "type": "VoC",
                          "status": None, "statusCategory": None, "assignee": None,
                          "pct": None, "virtual": True}]
            return nodes
        return self._swr(f"ancestors:{self.env}:{key}", build)

    def _epic_pct(self, epic_key):
        try:
            return round(self.epic_progress_one(epic_key).get("progressPct") or 0)
        except Exception:
            return None

    def ticket_siblings(self, key):
        """형제 = 같은 부모(Sub-Task) 또는 같은 Epic(Story/Task/Bug)을 공유하는 티켓들.
        현재 티켓도 포함하고 current=True 로 표시(‘5개 중 2번째’ 위치 파악용). Epic 은 []."""
        def build():
            try:
                f = self.get_issue_light(key).get("fields") or {}     # parent·epic_link 만 보면 됨(경량)
            except Exception:
                return []
            parent_key = (f.get("parent") or {}).get("key")
            if parent_key:                      # Sub-Task → 부모의 subtasks (부모 1회 조회로 끝)
                subs = (self.get_issue_light(parent_key).get("fields") or {}).get("subtasks") or []
                rows = [{"key": s.get("key"),
                         "summary": (s.get("fields") or {}).get("summary") or s.get("key"),
                         "type": (((s.get("fields") or {}).get("issuetype")) or {}).get("name", "Sub-Task"),
                         "statusCategory": _norm_cat(((((s.get("fields") or {}).get("status") or {})
                                                       .get("statusCategory")) or {}).get("key")),
                         "component": None} for s in subs if s.get("key")]
            else:
                epic_key = f.get(self.s.epic_link_field_id)
                if not epic_key:
                    return []
                raws = self._search(f'"Epic Link" = {epic_key}',
                                    cache_key=f"epic_children:{self.env}:{epic_key}")
                rows = []
                for it in raws:
                    ff = it.get("fields") or {}
                    rows.append({"key": it.get("key"),
                                 "summary": ff.get("summary") or it.get("key"),
                                 "type": (ff.get("issuetype") or {}).get("name", ""),
                                 "statusCategory": _norm_cat(((ff.get("status") or {})
                                                              .get("statusCategory") or {}).get("key")),
                                 "component": _comp_of(ff)})
            for r in rows:
                r["current"] = (r["key"] == key)
            return rows
        return self._swr(f"siblings:{self.env}:{key}", build)

    # ── 티켓 타임라인(변경 이력 + 코멘트) ──
    # '중요 알림'만 남긴다: 생성 / 상태 / 담당자 / 해결 / 우선순위 / 마감 / 소속 / 스프린트 + 댓글.
    # description·summary·labels·attachment·Rank 같은 잡음은 제외(설명 수정으로 타임라인이 묻히지 않게).
    TIMELINE_FIELDS = {"status", "assignee", "resolution", "priority",
                       "duedate", "epic link", "parent", "sprint"}

    CHILD_SCAN_LIMIT = 20        # 자손 상태변경 **스캔** 상한(타임라인 — 자식 1명당 changelog 1회, cold 비용 방어)
    CHILD_DISPLAY_LIMIT = 300    # 하위 목록 **표시** 상한. 검색이 경량(1회)+뱃지 배치라 넉넉히 보여도 싸다.

    def prefetch_changelogs(self, keys):
        """여러 티켓의 이력을 **검색 한 번**으로 받아 changelog 캐시에 채운다.

        타임라인은 '자손의 상태 변경'까지 모으느라 자식 수만큼 낱개 조회를 했다.
        prod(SSO)는 직렬이라 자식 20개면 그대로 20 × 왕복이다.
        검색도 expand=changelog 를 지원하므로 한 번에 받아 채워둔다.
        """
        env = self.env
        # 검색이 expand=changelog 를 무시하는 인스턴스가 있다(jira820 이 그렇다).
        # 그런 곳에서 계속 시도하면 매번 **헛도는 왕복**만 늘어난다 → 한 번 확인하고 기억한다.
        cap = f"cap:search_changelog:{env}"
        if self.cache.get(cap) is False:
            return
        miss = [k for k in dict.fromkeys(keys)
                if k and self.cache.get(f"changelog:{env}:{k}") is None]
        if len(miss) < 2:
            return
        for i in range(0, len(miss), self.ISSUE_BATCH):
            chunk = miss[i:i + self.ISSUE_BATCH]
            try:
                data = self.provider.get_json("/rest/api/2/search", params={
                    "jql": "key in (%s)" % ",".join(chunk),
                    "fields": "created,reporter", "expand": "changelog",
                    "maxResults": len(chunk)})
            except SessionExpired:
                raise
            except Exception:
                return
            got = data.get("issues") or []
            if got and all(r.get("changelog") is None for r in got):
                self.cache.set(cap, False, self.STATUS_CATS_TTL)   # 미지원 — 다시 시도하지 않는다
                return
            for raw in got:
                k = raw.get("key")
                # ★ changelog 키가 아예 없으면 검색이 expand 를 무시한 것이다.
                #   그대로 캐시하면 '이력 없음'을 굳혀 자손 상태변경이 통째로 사라진다
                #   → 채우지 않고 개별 조회(_changelog)에 맡긴다.
                if not k or raw.get("changelog") is None:
                    continue
                f = raw.get("fields") or {}
                self.cache.set(f"changelog:{env}:{k}", {
                    "created": f.get("created"), "reporter": f.get("reporter") or {},
                    "histories": (raw.get("changelog") or {}).get("histories") or [],
                }, self.s.cache_ttl_seconds)

    def _changelog(self, key):
        """티켓 원본 이력 {created, reporter, histories} — 티켓단위 캐시.
        본인 타임라인과 '부모의 자손 스캔'이 같은 캐시를 공유한다."""
        def do():
            try:
                raw = self.provider.get_json(
                    f"/rest/api/2/issue/{key}",
                    params={"expand": "changelog", "fields": "created,reporter"})
            except Exception:
                return {}
            f = raw.get("fields") or {}
            return {"created": f.get("created"), "reporter": f.get("reporter") or {},
                    "histories": (raw.get("changelog") or {}).get("histories") or []}
        return self.cache.get_or_set(f"changelog:{self.env}:{key}", self.s.cache_ttl_seconds, do)[0]

    def _child_keys(self, key, limit=None):
        """직계 자식 키 — Epic 은 Epic Link 자식, 그 외는 subtasks. (둘 다 기존 캐시 재사용)"""
        limit = self.CHILD_SCAN_LIMIT if limit is None else limit
        try:
            f = self.get_issue_light(key).get("fields") or {}   # issuetype·subtasks 만 보면 됨(경량)
        except Exception:
            return []
        if (f.get("issuetype") or {}).get("name") == "Epic":
            kids = self._search(f'"Epic Link" = {key}', cache_key=f"epic_children:{self.env}:{key}")
            return [k.get("key") for k in kids if k.get("key")][:limit]
        return [s.get("key") for s in (f.get("subtasks") or []) if s.get("key")][:limit]

    STATUS_CATS_TTL = 24 * 3600           # 상태 정의는 거의 안 바뀐다

    def _status_cats(self):
        """상태명(소문자) → todo|inprogress|done.

        타임라인 상태 뱃지에 색을 주려면 카테고리가 필요한데, **상태명 하드코딩은 금지**다
        (사내 커스텀 워크플로가 많아 이름을 신뢰할 수 없다 — CLAUDE.md §10).
        그래서 인스턴스의 /rest/api/2/status 를 받아 매핑한다. 실패하면 빈 맵 →
        프론트는 색 없는 중립 뱃지로 그린다(기능은 그대로).
        """
        def do():
            try:
                data = self.provider.get_json("/rest/api/2/status")
            except SessionExpired:
                raise
            except Exception:
                return {}
            out = {}
            for st in (data or []):
                nm = (st.get("name") or "").strip()
                if nm:
                    out[nm.lower()] = _norm_cat((st.get("statusCategory") or {}).get("key"))
            return out
        return self.cache.get_or_set(f"statuscats:{self.env}", self.STATUS_CATS_TTL, do)[0]

    def ticket_timeline(self, key, limit=40):
        """티켓 이력 — [{kind,date,author,authorId,field,from,to,srcKey}] 최신순.
        본인 이벤트(생성/중요 필드 변경/댓글) + **직계 자손의 상태 변경**(srcKey 로 출처 표시)."""
        cats = self._status_cats()

        def ev_of(h, allow, src=None):
            who = h.get("author") or {}
            out = []
            for item in (h.get("items") or []):
                fld = (item.get("field") or "").strip()
                if fld.lower() not in allow:
                    continue                             # 잡음(description 등) 제외
                frm, to = item.get("fromString"), item.get("toString")
                low = fld.lower()
                if low == "assignee":
                    # 사람 값은 화면 전체와 같은 규칙으로(표시명 첫 어절). prod 의 '홍길동 SKCC' → '홍길동'
                    frm, to = (real_name(frm) if frm else frm), (real_name(to) if to else to)
                elif low == "priority":
                    frm, to = _prio(frm), _prio(to)      # 'Unclassified' → None(화면은 '미지정')
                ev = {"kind": ("child-" if src else "") + low.replace(" ", "-"),
                      "date": h.get("created"),
                      "author": real_name(who.get("displayName") or who.get("name")),
                      "authorId": who.get("name"), "field": fld,
                      "from": frm, "to": to,
                      "srcKey": src}
                if low == "status":                      # 뱃지 색용 카테고리(없으면 중립)
                    ev["fromCat"] = cats.get((frm or "").lower())
                    ev["toCat"] = cats.get((to or "").lower())
                out.append(ev)
            return out

        def build():
            ev = []
            own = self._changelog(key)
            rep = own.get("reporter") or {}
            if own.get("created"):
                ev.append({"kind": "created", "date": own["created"],
                           "author": real_name(rep.get("displayName") or rep.get("name")),
                           "authorId": rep.get("name"), "field": None,
                           "from": None, "to": None, "srcKey": None})
            for h in own.get("histories") or []:
                ev.extend(ev_of(h, self.TIMELINE_FIELDS))
            for c in self._issue_comments(key, limit=10):   # 코멘트는 기존 캐시 재사용
                ev.append({"kind": "comment", "date": c.get("date"), "author": c.get("author"),
                           "authorId": c.get("authorId"), "field": None,
                           "from": None, "to": None, "srcKey": None})
            # 자손은 '상태 변경'만 (요청 범위). 자식별 changelog 는 캐시되어 재방문 시 무료.
            child_keys = self._child_keys(key)
            self.prefetch_changelogs(child_keys)      # 낱개 N 회 → 검색 1 회
            for ck in child_keys:
                for h in (self._changelog(ck).get("histories") or []):
                    ev.extend(ev_of(h, {"status"}, src=ck))
            ev.sort(key=lambda e: e.get("date") or "", reverse=True)   # 최신순
            return ev[:limit]
        return self._swr(f"timeline:{self.env}:{key}", build)

    def ticket_children(self, key):
        """직계 하위 티켓(Epic→Epic Link 자식 / 그 외→Sub-Task) — ticket_badge 형태.
        _child_keys·ticket_badge 가 모두 캐시라 재방문은 무료."""
        def build():
            # 표시용은 넉넉한 상한(CHILD_DISPLAY_LIMIT) — 스캔용 20 을 쓰면 하위가 많은 Epic 이 20 개로 잘렸다.
            kids = self._child_keys(key, limit=self.CHILD_DISPLAY_LIMIT)
            self.prefetch_issues(kids, light=True)   # 뱃지만 필요 → 경량 배치(이후 badge 는 캐시 히트)
            return [b for b in (self.ticket_badge(k) for k in kids) if b]
        return self._swr(f"children:{self.env}:{key}", build)

    def ticket_related(self, key, limit=20):
        """관련 티켓 — 두 소스를 합친다.
          (1) Jira 이슈 링크(relates to / blocks / duplicates …)  ← issuelinks 필드
          (2) 설명·코멘트에서 **언급**된 티켓                      ← /browse/KEY 링크 파싱
        계보(조상·자식)와 자기 자신은 제외해 좌측 패널의 다른 섹션과 중복되지 않게 한다."""
        def build():
            skip = {key}
            skip |= {a["key"] for a in (self.ticket_ancestors(key) or [])}
            skip |= {c["key"] for c in (self.ticket_children(key) or [])}
            found = []          # [(key, rel, via)] — 순서 유지(링크 먼저, 언급 나중)
            seen = set(skip)

            try:
                f = self.get_issue(key).get("fields") or {}
            except Exception:
                f = {}
            for ln in (f.get("issuelinks") or []):
                t = ln.get("type") or {}
                out_i, in_i = ln.get("outwardIssue"), ln.get("inwardIssue")
                other = out_i or in_i or {}
                k = other.get("key")
                if not k or k in seen:
                    continue
                seen.add(k)
                rel = _rel_label(t, bool(out_i))
                found.append((k, rel, "link"))

            htmls = []
            try:
                v = self.ticket_view(key)
                if v:
                    htmls.append(v.get("descriptionHtml") or "")
            except Exception:
                pass
            try:
                for c in self._issue_comments(key, limit=10):
                    htmls.append(c.get("html") or "")
            except Exception:
                pass
            for h in htmls:
                for k in _BROWSE_KEY_RE.findall(h or ""):
                    if k in seen:
                        continue
                    seen.add(k)
                    found.append((k, "본문·코멘트에서 언급", "mention"))

            picked = found[:limit]
            self.prefetch_issues([k for k, _, _ in picked])   # 관련 티켓 원본을 한 번에
            out = []
            for k, rel, via in picked:
                b = self.ticket_badge(k)
                if b:
                    out.append(dict(b, rel=rel, via=via))
            return out
        return self._swr(f"related:{self.env}:{key}", build)

    def ticket_attachments(self, key):
        """첨부파일 — [{id,filename,size,mime,created,author,url,thumb,isImage}].
        url/thumb 는 prod 에서 크로스오리진·인증이 걸리므로 이미지 프록시(/api/img)를 태운다."""
        def build():
            try:
                f = self.get_issue(key).get("fields") or {}
            except Exception:
                return []
            out = []
            for a in (f.get("attachment") or []):
                mime = a.get("mimeType") or ""
                is_img = mime.startswith("image/")
                out.append({
                    "id": str(a.get("id") or ""),
                    "filename": a.get("filename") or "(이름 없음)",
                    "size": int(a.get("size") or 0),
                    "mime": mime,
                    "created": a.get("created"),
                    "author": real_name((a.get("author") or {}).get("displayName")
                                        or (a.get("author") or {}).get("name")),
                    # 목록의 링크는 **내려받기**다(썸네일만 이미지 프록시).
                    "url": self._media_url(a.get("content") or "", download=True),
                    "thumb": self._media_url(a.get("thumbnail") or "") if is_img else None,
                    "isImage": is_img,
                })
            return out
        return self._swr(f"attachments:{self.env}:{key}", build)

    def _remote_links(self, key):
        """이슈의 원격 링크 — [{title, url}]. 실 Jira: /issue/{key}/remotelink.
        Confluence 문서 + 외부 Web link 가 섞여 온다. `remotelinks:{env}:{key}` 로 캐시."""
        def do():
            try:
                data = self.provider.get_json(f"/rest/api/2/issue/{key}/remotelink")
            except SessionExpired:
                raise
            except Exception:
                return []
            out = []
            for r in (data or []):
                obj = r.get("object") or {}
                url = (obj.get("url") or "").strip()
                if url:
                    # id 를 함께 싣는다 — 이게 있어야 화면에서 링크를 뗄 수 있다(문서 ✕).
                    # relationship/application 은 **Confluence 가 이 티켓을 언급했을 때** 자동으로
                    # 붙는 값이다("mentioned in" 등). 사람이 붙인 참고 문서와 성격이 달라 구분한다.
                    app = (r.get("application") or {})
                    out.append({"id": str(r.get("id") or ""),
                                "title": (obj.get("title") or "").strip(), "url": url,
                                "rel": (r.get("relationship") or "").strip(),
                                "app": (app.get("name") or app.get("type") or "").strip()})
            return out
        return self.cache.get_or_set(f"remotelinks:{self.env}:{key}", self.s.cache_ttl_seconds, do)[0]

    def ticket_documents(self, key, limit=20):
        """관련 문서 — 두 소스를 합친다(URL 기준 중복 제거):
          (1) 설명·코멘트에서 **언급된 Confluence 문서**  ← 본문 앵커 파싱
          (2) Jira **remote link**(Confluence 문서 · Web link)  ← /remotelink

        Web link 는 Confluence 가 아니어도 포함한다(사용자가 이슈에 건 '문서/링크'다).
        본문 언급과 remote link 가 같은 문서를 가리키면 한 번만 나온다."""
        def build():
            htmls = []
            try:
                v = self.ticket_view(key)
                if v:
                    htmls.append(v.get("descriptionHtml") or "")
            except Exception:
                pass
            try:
                for c in self._issue_comments(key, limit=10):
                    htmls.append(c.get("html") or "")
            except Exception:
                pass
            out, seen = [], set()

            def add(u, title, is_conf, link_id=None, mention=False, rel=""):
                u = _abs_url(u, self.s.confluence_base)     # 상대경로면 절대화(안 그러면 404)
                # Confluence 는 문서 정규화 키로, Web link 는 URL 로 중복 판정
                ck = _conf_key(u) if is_conf else ("web:" + u.rstrip("/").lower())
                if ck in seen:
                    return
                seen.add(ck)
                # linkId 가 있는 것만 지울 수 있다 — 본문에 **언급**된 문서는 링크가 아니라 글이라,
                # 지우려면 본문을 고쳐야 한다(화면도 그때만 ✕ 를 보인다).
                # mention=True 는 **저쪽에서 이 티켓을 언급해** 자동으로 생긴 링크다.
                # 참고하라고 사람이 붙인 문서와 성격이 다르므로 화면에서 자리를 나눈다.
                out.append({"title": title, "url": u, "linkId": link_id,
                            "mention": bool(mention), "rel": rel})

            # (1) 본문 언급 Confluence 문서
            for h in htmls:
                for href, text in _ANCHOR_RE.findall(h or ""):
                    u = unescape(href)
                    if _CONF_RE.search(u):
                        add(u, _conf_title(u, text), True)
            # (2) Jira remote link — Confluence 든 Web 이든 포함(같은 문서면 위와 중복 제거됨)
            for r in self._remote_links(key):
                u = r["url"]
                is_conf = bool(_CONF_RE.search(u))
                title = r["title"] or (_conf_title(u) if is_conf else u)
                # ★ 제목이 'page' 처럼 쓸모없으면 **문서에서 진짜 제목을 받아 온다**.
                #   옛 Confluence 링크(/pages/viewpage.action?pageId=…)에는 제목이 URL 어디에도
                #   없어서, 슬러그만 보면 영영 'page' 로 남는다. 조회는 캐시된다.
                if is_conf and _weak_title(title, u):
                    got = self.conf_title_by_id(u)          # ① 페이지 id 로 정확히
                    if not got:
                        try:
                            got = self.link_title(u)        # ② 그것도 안 되면 og:title
                        except Exception:
                            got = None
                    if got:
                        title = got
                add(u, title, is_conf, r.get("id"),
                    mention=_is_mention_link(r), rel=r.get("rel") or "")
            return out[:limit]
        return self._swr(f"documents:{self.env}:{key}", build)

    # ── 사용자 프로필 이미지 ──
    # 아바타는 사실상 불변이라 URL 해석을 아주 길게 캐시(AVATAR_TTL). 바이트는 캐시에 넣지 않고
    # (SQLite 블롭 비대) HTTP Cache-Control(장기·immutable)로 브라우저가 들고 있게 한다.
    AVATAR_TTL = 30 * 24 * 3600      # 30일

    def _avatar_url(self, user):
        """사용자 아바타 URL(큰 것 우선). 없거나 조회 실패면 None. 성공/실패 모두 길게 캐시하지 않도록
        실패는 캐시하지 않는다(세션 만료 등 일시 실패가 30일 굳는 것 방지)."""
        ck = f"avatar_url:{self.env}:{user}"
        hit = self.cache.get(ck)
        if hit is not None:
            return hit or None
        try:
            u = self.provider.get_json("/rest/api/2/user", params={"username": user})
        except Exception:
            return None                                   # 실패는 캐시 안 함 → 다음에 재시도
        urls = ((u or {}).get("avatarUrls") or {})
        url = next((urls[s] for s in ("48x48", "32x32", "24x24", "16x16") if urls.get(s)), None)
        self.cache.set(ck, url or "", self.AVATAR_TTL)    # 없음("")도 캐시 — 매번 재조회 방지
        return url

    def user_avatar(self, user):
        """(bytes, content_type). 아바타가 없거나 못 가져오면 (None, None) → 프론트가 기본 아이콘으로."""
        url = self._avatar_url(user)
        if not url:
            return (None, None)
        try:
            if url.startswith("http://") or url.startswith("https://"):
                return self.fetch_media(url)              # 절대 URL 은 허용 호스트만
            data, ctype = self.provider.get_bytes(url)    # 상대 경로 = Jira 자기 호스트
            return (data, ctype)
        except Exception:
            return (None, None)

    # ── 이미지/첨부 프록시 (prod: 인증 세션으로 받아 same-origin 반환) ──
    def _media_allowed_host(self, host):
        """이미지 프록시 허용 호스트 판별 — jira base 호스트·동일 상위도메인·config image_hosts."""
        host = (host or "").split("@")[-1].split(":")[0].lower()
        if not host:
            return False
        jh = urlparse(self.s.jira_base).netloc.split(":")[0].lower()
        if host == jh:
            return True
        if host in [h.lower() for h in getattr(self.s, "image_hosts", [])]:
            return True
        parent = ".".join(jh.split(".")[-2:]) if jh.count(".") >= 1 else jh
        return host == parent or host.endswith("." + parent)

    LINK_TITLE_TTL = 7 * 24 * 3600         # 페이지 제목도 자주 안 바뀐다

    CONF_TITLE_TTL = 24 * 3600        # 문서 제목은 거의 안 바뀐다

    def conf_title_by_id(self, url):
        """Confluence 페이지 **id 로 진짜 제목**을 받아 온다(없으면 None).

        옛 링크(`/pages/viewpage.action?pageId=123`)에는 제목이 URL 어디에도 없어서, 슬러그만
        보면 영영 'page' 로 남는다. 페이지를 통째로 받아 og:title 을 훑는 것보다 이 조회가
        싸고 정확하다 — 실패하면 호출부가 og:title 로 물러선다.
        """
        base = (self.s.confluence_base or "").rstrip("/")
        if not base:
            return None
        m = _CONF_PAGEID_RE.search(url or "")
        pid = (m.group(1) or m.group(2)) if m else None
        if not pid:
            return None

        def do():
            d = self.provider.get_json(f"{base}/rest/api/content/{pid}")
            return ((d or {}).get("title") or "").strip()

        try:
            return self.cache.get_or_set(f"conftitle:{self.env}:{pid}",
                                         self.CONF_TITLE_TTL, do)[0] or None
        except Exception:
            return None

    def link_title(self, u):
        """웹 URL 의 표시 제목 — og:title 우선, 없으면 <title>. 실패 시 None. 캐시.
        붙여넣기로 만든 링크 뱃지의 라벨(URL 대신 사람이 읽는 제목)로 쓴다."""
        from html import unescape
        from urllib.parse import urlsplit
        try:
            p = urlsplit(u or "")
        except Exception:
            return None
        if p.scheme not in ("http", "https") or not p.netloc:
            return None
        bases = [b for b in (self.s.jira_base, self.s.confluence_base,
                             getattr(self.s, "bitbucket_base", "")) if b]
        origin = f"{p.scheme}://{p.netloc}"
        internal = any(origin.rstrip("/") == b.rstrip("/") for b in bases)

        def do():
            html = ""
            try:
                if internal:                       # 사내 서비스는 인증 세션으로
                    data, _ct = self.provider.get_bytes(u)
                    html = (data or b"")[:200000].decode("utf-8", "replace")
                else:
                    import requests
                    r = requests.get(u, timeout=6, headers={"User-Agent": "LakeTaskManager"})
                    if r.status_code != 200:
                        return {}
                    html = r.text[:200000]
            except Exception:
                return {}
            m = (re.search(r'<meta[^>]+property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
                 or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*property=["\']og:title["\']', html, re.I)
                 or re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S))
            if not m:
                return {}
            t = re.sub(r"\s+", " ", unescape(m.group(1))).strip()[:120]
            return {"t": t} if t else {}

        got = self.cache.get_or_set(f"linktitle:{u}", self.LINK_TITLE_TTL, do)[0] or {}
        return got.get("t") or None

    FAVICON_TTL = 7 * 24 * 3600            # favicon 은 거의 안 바뀐다 — 길게 캐시

    def favicon(self, u):
        """웹 URL origin 의 favicon — (bytes, content_type). 못 가져오면 (None, None).
        사내 서비스(jira/confluence/bitbucket)는 인증 provider 로, 그 외 공개 사이트는 직접 GET.
        링크 뱃지 아이콘용. 결과(base64)를 캐시해 매번 외부 요청하지 않는다."""
        import base64
        from urllib.parse import urlsplit
        try:
            p = urlsplit(u or "")
        except Exception:
            return (None, None)
        if p.scheme not in ("http", "https") or not p.netloc:
            return (None, None)
        origin = f"{p.scheme}://{p.netloc}"
        bases = [b for b in (self.s.jira_base, self.s.confluence_base,
                             getattr(self.s, "bitbucket_base", "")) if b]
        internal = any(origin.rstrip("/") == b.rstrip("/") for b in bases)

        def do():
            for path in ("/favicon.ico", "/favicon.png"):
                try:
                    if internal:
                        data, ct = self.provider.get_bytes(origin + path)
                    else:
                        import requests
                        r = requests.get(origin + path, timeout=5,
                                         headers={"User-Agent": "LakeTaskManager"})
                        if r.status_code != 200:
                            continue
                        data, ct = r.content, r.headers.get("Content-Type")
                    if data:
                        return {"d": base64.b64encode(data).decode(),
                                "ct": (ct or "image/x-icon").split(";")[0].strip()}
                except Exception:
                    continue
            return {}

        got = self.cache.get_or_set(f"favicon:{origin}", self.FAVICON_TTL, do)[0] or {}
        if not got.get("d"):
            return (None, None)
        try:
            return (base64.b64decode(got["d"]), got.get("ct") or "image/x-icon")
        except Exception:
            return (None, None)

    def _media_url(self, u, download=False):
        """첨부 URL — **항상 프록시**를 거친다.

        전엔 dev 에서 Jira 주소를 그대로 줬는데, 그러면 앱 창(SSO 세션을 가진 Chromium)에서만
        열리고 사용자가 평소 쓰는 브라우저에서는 인증이 없어 실패한다. 세 환경이 같은 경로를
        타야 "여기선 되는데 저기선 안 된다" 가 없다.

        download=True 는 파일용(/api/file) — 원래 이름으로 저장되도록 헤더가 다르다.
        """
        if not u:
            return None
        base = (self.s.jira_base or "").rstrip("/")
        target = u if not u.startswith("/") else (base + u if self.env == "prod" else u)
        return ("/api/file?u=" if download else "/api/img?u=") + quote(target, safe="")

    def _proxy_media(self, html):
        """<img src> 를 /api/img 프록시로 재작성 — **세 환경 공통**.
        첨부 이미지는 Jira 상대경로(/secure/attachment/…)라 그대로 두면 앱 오리진에서 404 다
        (mock 은 jira820 이 in-process, local 은 :8080 — 어느 쪽도 앱이 그 경로를 서빙하지 않는다).
        프록시는 provider 로 받아 same-origin 으로 돌려주므로 mock/local/prod 가 같은 경로를 탄다.
        prod 만 절대 URL 로 바꿔 호스트 검증(SSRF 방지)을 태우고, dev 는 상대경로 그대로
        넘겨 provider 가 base 를 붙이게 한다."""
        if not html:
            return html
        if self.env != "prod":
            out = proxy_attachment_images(html)
        else:
            out = proxy_images(html, self.s.jira_base, self._media_allowed_host)
        # 첨부 **링크**도 같은 이유로 재작성한다 — 이미지만 프록시하고 링크를 빼 두면
        # 본문의 파일을 눌렀을 때 앱 오리진에서 404 가 난다.
        return proxy_attachment_links(out, self.s.jira_base)

    def fetch_media(self, u):
        """이미지 URL(u) 을 인증 provider 로 받아 (bytes, content_type) 반환. 허용 안 되면 (None, None)."""
        if not u:
            return None, None
        if u.startswith("/"):
            target = u                         # jira 상대경로 → provider 가 base+path
        elif u.startswith(("http://", "https://")):
            if not self._media_allowed_host(urlparse(u).netloc):
                return None, None              # SSRF 방지 — 허용 호스트만
            target = u
        else:
            return None, None
        try:
            return self.provider.get_bytes(target)
        except Exception:
            return None, None

    def _get_issue_view(self, key, fresh=False):
        """상세 뷰용 단일 티켓 원본 — renderedFields(HTML) 포함 요청. `issueview:{env}:{key}` 캐시.
        prod 은 renderedFields.description(HTML) 반환, mock/local(jira820)도 렌더된 HTML 제공."""
        ck = f"issueview:{self.env}:{key}"

        def fetch():
            d = self.provider.get_json(
                f"/rest/api/2/issue/{key}",
                params={"fields": self._issue_fields(), "expand": "renderedFields"})
            # 이 응답은 **전체 필드**(issuelinks·attachment 포함)라, 같은 티켓의 전체 이슈 캐시도
            # 함께 데운다 → 다이얼로그의 관련(issuelinks)·첨부(attachment)·자식 조회가 재요청 없이
            # 이걸 재사용한다(경량 검색으로 issue:{key} 가 안 데워지는 걸 상쇄 + 기존 중복 제거).
            if isinstance(d, dict) and "fields" in d:
                self.cache.set(f"issue:{self.env}:{key}", d, self.s.cache_ttl_seconds)
            return d
        if fresh:
            # **낡은 값을 받으면 안 되는 자리**(본문 편집 시작·편집 중 충돌 감시)에서만 쓴다.
            # 평소 경로는 캐시를 먼저 주고 뒤에서 갱신한다 — 화면은 빨리 뜨지만 첫 응답이
            # 한 박자 늦다. 그 한 박자가 본문 편집에서는 '남의 수정을 덮어쓰기' 가 된다.
            try:
                data = fetch()
            except SessionExpired:
                raise
            except Exception:
                got = self.cache.get_alive(ck)                # 못 받으면 있던 값으로 — 편집 자체를 막진 않는다
                data = got[0] if got else None
            else:
                self.cache.set(ck, data, self.s.cache_ttl_seconds)
            return data if isinstance(data, dict) and "fields" in data else None
        # SessionExpired(401/403/5xx) 는 전파돼 라우트가 needLogin(401) 처리. 404 는 에러 바디(dict) → None.
        data, _ = self.cache.get_or_set(ck, self.s.cache_ttl_seconds, fetch)
        if not isinstance(data, dict) or "fields" not in data:
            return None       # 존재하지 않는 티켓(404 에러 바디)
        return data

    def _display_name(self, pid):
        """Jira 사용자 displayName('{본명} {회사}').

        **성공만 캐시**(user:{env}:{pid}). 실패하면 로그 남기고 id 폴백하되 **캐시하지 않는다**
        → 일시적 실패(예: 세션 만료)로 username 이 15분간 굳는 문제 방지(다음 호출에 재시도).
        """
        ck = f"user:{self.env}:{pid}"
        hit = self.cache.get(ck)
        if hit is not None:
            return hit
        dn = None
        try:
            u = self.provider.get_json("/rest/api/2/user", params={"username": pid})
            dn = u.get("displayName")
        except Exception:
            # 이름 조회 실패는 흔하고(권한·비활성 사용자) 화면엔 id 로 폴백된다 — 로그로 안 남긴다.
            pass
        if dn:
            self.cache.set(ck, dn, self.s.cache_ttl_seconds)
            return dn
        return pid

    def epic_progress_one(self, key):
        """단일 Epic 진척률 {doneSp,totalSp,mockSp,progressPct,name}."""
        pr = progress.epic_progress(self.epic_issues(key))
        pr["name"] = self.epic_name(key)
        return pr

    # ── 기능3: 인력 워크로드 / 활동 ──
    def workload(self, plan, people):
        return self._fetch_workload(plan, people)

    @staticmethod
    def _wl_zero():
        return {"count": {"task": 0, "subtask": 0, "voc": 0}, "hr": {"task": 0, "subtask": 0, "voc": 0}}

    def _wl_counts(self, jql):
        # count(티켓수) · hr(소요시간, 표준 timespent 초→시). 카테고리 3분할: task/subtask/voc.
        # 주의: 여기서 예외를 삼키면 '조회 실패'가 0 으로 둔갑하고 그게 캐시된다(막대만 0, 상세는 정상).
        #       → 실패는 그대로 올려보내고 workload_person 이 캐시 없이 error 로 처리한다.
        by = {"count": {"task": 0, "subtask": 0, "voc": 0}, "hr": {"task": 0, "subtask": 0, "voc": 0}}
        for it in self._search(jql, max_results=300):   # write-through: 각 티켓 캐시
            f = it.get("fields", {}) or {}
            comps = [c.get("name") for c in (f.get("components") or [])]
            comp = VOC_COMPONENT if VOC_COMPONENT in comps else (comps[0] if comps else "")
            itt = f.get("issuetype") or {}
            c = _wl_category(comp, itt.get("name", ""), itt.get("subtask"))
            if not c:
                continue
            by["count"][c] += 1
            by["hr"][c] += round((f.get("timespent") or 0) / 3600.0, 1)
        return by

    def workload_person(self, pid):
        """**인력 1명**의 워크로드 번들 — `workload:{env}:{pid}` 캐시. (사람 by 사람 비동기 로딩용.)
        displayName 은 카운트 번들과 분리해 매번 재해석(카운트가 캐시돼도 username 이 안 굳게)."""
        key = f'workload:{self.env}:{pid}'
        bundle = self.cache.get(key)
        if bundle is None:
            try:
                bundle = {
                    "id": pid,
                    # 미완료 할당 = 미착수(To Do) + 진행 중(In Progress). 완료는 최근 7일만.
                    # 미착수는 최근 14일내 update 된 것만(할당 후 잊혀진 오래된 티켓=데이터오염 제외).
                    "open": self._wl_counts(f'assignee = "{pid}" AND statusCategory = "To Do" AND updated >= -14d'),
                    "inProgress": self._wl_counts(f'assignee = "{pid}" AND statusCategory = "In Progress"'),
                    "done7d": self._wl_counts(f'assignee = "{pid}" AND statusCategory = Done AND resolved >= -7d'),
                }
                self.cache.set(key, bundle, self.s.cache_ttl_seconds)   # 성공한 결과만 캐시
            except SessionExpired:
                raise            # 세션 만료/로그인 필요 → 라우트가 401 needLogin 으로 (0 으로 위장 금지)
            except Exception:
                # 조회 실패 — 0 을 캐시하지 않는다(다음 로드에서 재시도). UI 는 '조회 실패'로 표시.
                bundle = {"id": pid, "error": True,
                          "open": self._wl_zero(), "inProgress": self._wl_zero(), "done7d": self._wl_zero()}
        return dict(bundle, displayName=self._display_name(pid))

    def display_name_cached(self, uid):
        """캐시에 있으면 displayName, 없으면 None — **상류에 안 붙는다**(shell 로스터를 빠르게 그리려고)."""
        return self.cache.get(f"udisp:{self.env}:{uid}")

    def _fetch_workload(self, plan, people):
        """인력별 Task성/VoC성 × 진행중/최근7일완료 티켓 수. (전체 한 방 — /api/workload 용)"""
        pids = [pid for module in plan["modules"] for pid in people.get(module, [])]
        by_pid = {b["id"]: b for b in self._pmap(pids, self.workload_person)}   # 인력 단위 병렬
        return {module: [by_pid[pid] for pid in people.get(module, []) if pid in by_pid]
                for module in plan["modules"]}

    def _wl_ticket(self, it):
        """워크로드 상세용 티켓 투영: 번호·제목·타입·상태·마감·완료일시."""
        f = it.get("fields", {}) or {}
        st = f.get("status") or {}
        itt = f.get("issuetype") or {}
        # sub-task 는 로케일별 이름이 달라도 뱃지/색이 맞게 "Sub-Task" 로 정규화(issuetype.subtask 기준).
        tname = "Sub-Task" if itt.get("subtask") else itt.get("name", "")
        comps = [c.get("name") for c in (f.get("components") or []) if c.get("name")]
        return {
            "key": it.get("key", ""),
            "summary": f.get("summary", ""),
            "type": tname,
            "status": st.get("name", ""),
            "statusCategory": _norm_cat((st.get("statusCategory") or {}).get("key")),
            "due": f.get("duedate") or None,
            "resolved": f.get("resolutiondate") or None,
            # 우선순위 — 하위 목록의 첫 칸. 이름과 등급을 함께 준다(그림은 등급, 툴팁은 이름).
            "priority": (f.get("priority") or {}).get("name") or None,
            "priRank": _pri_rank((f.get("priority") or {}).get("name")),
            # Epic 분포용. epic 이 있으면 그 Epic 소속이고(= VoC 라도 Epic 이 있으면 그쪽으로 센다),
            # 없고 VoC 컴포넌트면 '사용자 VoC' 를 전용 Epic 처럼 따로 센다.
            "epic": f.get(self.s.epic_link_field_id) or None,
            "voc": self.s.voc_component in comps,
            "components": comps,
        }

    # 버킷별 JQL — 세 리스트를 각각 따로 부를 수 있게 분리(프론트에서 병렬 로딩·개별 렌더).
    WL_BUCKETS = {
        "open":       'assignee = "{u}" AND statusCategory = "To Do" AND updated >= -14d',
        "inProgress": 'assignee = "{u}" AND statusCategory = "In Progress"',
        "done7d":     'assignee = "{u}" AND statusCategory = Done AND resolved >= -7d',
    }

    def workload_bucket(self, user, bucket):
        """인력 상세의 **한 버킷만** (open|inProgress|done7d). 버킷 단위 캐시."""
        jql = self.WL_BUCKETS.get(bucket)
        if not jql:
            return None
        def do():
            raws = self._search(jql.format(u=user), max_results=200)
            out = [self._wl_ticket(it) for it in raws if self._wl_keep(it)]
            self._attach_epic_names(out)
            return out
        return self.cache.get_or_set(f"workload_bucket:{self.env}:{user}:{bucket}",
                                     self.s.cache_ttl_seconds, do)[0]

    def _attach_epic_names(self, tickets):
        """Epic 키 → 제목. 분포 막대의 범례에 키가 아니라 이름이 떠야 읽힌다.
        키를 한 번에 모아 배치 조회하므로 왕복은 1회다(prod SSO 는 직렬이라 중요)."""
        keys = sorted({t["epic"] for t in tickets if t.get("epic")})
        if not keys:
            return
        try:
            self.prefetch_issues(keys, light=True)     # Epic 제목(뱃지)만 필요 → 경량
        except Exception:
            pass
        names = {}
        for k in keys:
            try:
                b = self.ticket_badge(k)
            except Exception:
                b = None
            names[k] = (b or {}).get("summary") or k
        for t in tickets:
            if t.get("epic"):
                t["epicName"] = names.get(t["epic"], t["epic"])

    def _wl_keep(self, it):
        """카운트와 동일 필터: Task성/VoC성만 (Epic·Story·Bug 제외)."""
        f = it.get("fields", {}) or {}
        comps = [c.get("name") for c in (f.get("components") or [])]
        comp = VOC_COMPONENT if VOC_COMPONENT in comps else (comps[0] if comps else "")
        itt = f.get("issuetype") or {}
        return _wl_category(comp, itt.get("name", ""), itt.get("subtask")) is not None

    def workload_tickets(self, user):
        """인력 상세: 진행중 / 최근7일 완료 **티켓 리스트** (카운트 화면의 [+] 확장용).
        버킷 캐시를 재사용하므로 개별 호출과 결과가 동일하다."""
        return {"user": user,
                "open": self.workload_bucket(user, "open"),
                "inProgress": self.workload_bucket(user, "inProgress"),
                "done7d": self.workload_bucket(user, "done7d")}

    def _fetch_workload_tickets(self, user):
        def keep(it):   # 카운트와 동일 필터: Task성/VoC성만 (Epic·Story·Bug 제외)
            f = it.get("fields", {}) or {}
            comps = [c.get("name") for c in (f.get("components") or [])]
            comp = VOC_COMPONENT if VOC_COMPONENT in comps else (comps[0] if comps else "")
            itt = f.get("issuetype") or {}
            return _wl_category(comp, itt.get("name", ""), itt.get("subtask")) is not None
        op = self._search(f'assignee = "{user}" AND statusCategory = "To Do" AND updated >= -14d', max_results=200)
        ip = self._search(f'assignee = "{user}" AND statusCategory = "In Progress"', max_results=200)
        dn = self._search(f'assignee = "{user}" AND statusCategory = Done AND resolved >= -7d', max_results=200)
        return {"user": user,
                "open": [self._wl_ticket(it) for it in op if keep(it)],
                "inProgress": [self._wl_ticket(it) for it in ip if keep(it)],
                "done7d": [self._wl_ticket(it) for it in dn if keep(it)]}

    def activity(self, user):
        key = f"activity:{self.env}:{user}"
        data, _ = self.cache.get_or_set(key, self.s.cache_ttl_seconds, lambda: self._fetch_activity(user))
        return data

    def _fetch_activity(self, user):
        """최근 Jira(/activity ATOM 파싱) + Confluence(CQL) 활동."""
        return {"user": user, "jira": self._parse_activity(user),
                "confluence": self._fetch_confluence(user)}

    def _parse_activity(self, user, limit=20):
        """실 Jira Activity Streams ATOM 파싱: category term=kind, alternate link=/browse/KEY,
        activity:object/summary=요약, updated=일시. (title 은 HTML 이라 요약원이 아님 — object/summary 사용)"""
        import re as _re
        import xml.etree.ElementTree as ET
        A = "{http://www.w3.org/2005/Atom}"          # Atom
        V = "{http://activitystrea.ms/spec/1.0/}"    # activity
        out = []
        try:
            xml = self.provider.get_text("/activity",
                                         params={"maxResults": limit, "streams": f"user IS {user}"})
            root = ET.fromstring(xml)
            for e in root.findall(f"{A}entry"):
                cat = e.find(f"{A}category")
                kind = cat.get("term") if cat is not None else ""
                key = ""
                for ln in e.findall(f"{A}link"):
                    if ln.get("rel") == "alternate":
                        m = _re.search(r"/browse/([A-Z][A-Z0-9]+-\d+)", ln.get("href") or "")
                        if m:
                            key = m.group(1)
                obj = e.find(f"{V}object")
                summary = ""
                if obj is not None:
                    summary = (obj.findtext(f"{A}summary") or "").strip()
                    if not key:                       # 폴백: activity:object/title = KEY
                        key = (obj.findtext(f"{A}title") or "").strip()
                if not summary:                       # 폴백: HTML title 태그 제거
                    t = _re.sub(r"<[^>]+>", "", (e.findtext(f"{A}title") or ""))
                    summary = t.split(" - ", 1)[1].strip() if " - " in t else t.strip()
                out.append({
                    "date": (e.findtext(f"{A}updated") or e.findtext(f"{A}published") or ""),
                    "kind": kind, "key": key, "summary": summary,
                })
        except Exception:
            pass
        return out

    def _fetch_confluence(self, user):
        if not self.s.confluence_base:
            return []
        try:
            # expand 필수: 실 Confluence 는 expand 없으면 version/space 를 안 준다(당시 date/space 누락).
            data = self.provider.get_json("/rest/api/content/search", params={
                "cql": f'contributor = "{user}" and lastmodified >= now("-14d")',
                "expand": "version,space", "limit": 25})
            return [{"date": ((r.get("version") or {}).get("when") or ""),
                     "title": r.get("title", ""),
                     "space": ((r.get("space") or {}).get("key") or "")}
                    for r in data.get("results", [])]
        except Exception:
            return []

    def close(self):
        if self.provider:
            self.provider.close()

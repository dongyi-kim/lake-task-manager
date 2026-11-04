"""Bounded, same-Jira transport for Jira 8.20's native hierarchy wizards.

These are WebWork forms, not REST endpoints. Hidden XSRF/GUID fields belong to
the wizard caller; transports preserve them and never follow or replay a POST.
"""

import re
from html.parser import HTMLParser
from urllib.parse import unquote, urlencode, urlsplit, urlunsplit

from .base import PermissionDenied, SessionExpired, UpstreamUnavailable

MAX_HTML_BYTES = 1024 * 1024
_ACTIONS = {
    prefix + suffix + ".jspa"
    for prefix in ("ConvertIssue", "ConvertSubTask")
    for suffix in ("", "SetIssueType", "SetStatus", "UpdateFields", "Convert")
} | {"MoveSubTaskChooseOperation.jspa", "MoveSubTaskChooseOperation!default.jspa",
     "MoveSubTaskParent.jspa", "MoveSubTaskParent!default.jspa"}
_COMMITS = {"ConvertIssueConvert.jspa", "ConvertSubTaskConvert.jspa", "MoveSubTaskParent.jspa"}


def _origin(parts):
    return parts.scheme.lower(), (parts.hostname or "").lower(), parts.port or (
        443 if parts.scheme.lower() == "https" else 80)


def form_request(base, path, fields, may_commit):
    """Validate before touching a provider queue; return URL, headers and encoded body."""
    if not isinstance(path, str) or any(ord(char) < 32 for char in path) or "\\" in path:
        raise ValueError("Jira native form URL이 올바르지 않습니다.")
    jira, target = urlsplit(base.rstrip("/")), urlsplit(path)
    if jira.scheme not in ("http", "https") or not jira.hostname or jira.username or jira.password:
        raise ValueError("Jira base URL이 올바르지 않습니다.")
    if target.username or target.password or target.fragment or path.startswith("//"):
        raise ValueError("Jira native form URL이 올바르지 않습니다.")
    context = jira.path.rstrip("/")
    if target.scheme or target.netloc:
        if _origin(target) != _origin(jira):
            raise ValueError("Jira 외부 주소로 폼을 전송할 수 없습니다.")
        target_path = unquote(target.path)
    else:
        raw = unquote(target.path)
        if raw.startswith(context + "/") and context:
            target_path = raw
        elif raw.startswith(("/secure/", "/browse/")):
            target_path = context + raw
        elif "/" not in raw:
            target_path = context + "/secure/" + raw
        else:
            raise ValueError("지원하지 않는 Jira native form 경로입니다.")
    relative = target_path[len(context):] if target_path.startswith(context + "/") else ""
    action = relative.removeprefix("/secure/")
    browse = bool(re.fullmatch(r"/browse/[A-Za-z][A-Za-z0-9_]*-\d+", relative))
    if not (relative.startswith("/secure/") and action in _ACTIONS) and not browse:
        raise ValueError("지원하지 않는 Jira native form 경로입니다.")
    commit = fields is not None and action in _COMMITS
    if bool(may_commit) != commit or fields is None and action in _COMMITS or browse and fields is not None:
        raise ValueError("Jira native form의 읽기/쓰기 구분이 올바르지 않습니다.")
    encoded = None
    if fields is not None:
        if not isinstance(fields, (list, tuple)) or any(
            not isinstance(pair, (list, tuple)) or len(pair) != 2
            or not all(isinstance(value, str) for value in pair) for pair in fields
        ):
            raise ValueError("폼 필드는 문자열 (이름, 값) 목록이어야 합니다.")
        encoded = urlencode(fields, encoding="utf-8").encode("ascii")
    origin = urlunsplit((jira.scheme, jira.netloc, "", "", ""))
    url = urlunsplit((jira.scheme, jira.netloc, target_path, target.query, ""))
    headers = {"Accept": "text/html", "Referer": base.rstrip("/") + "/", "Origin": origin}
    if encoded is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
    return url, headers, encoded


def check_html_size(size):
    if size > MAX_HTML_BYTES:
        raise UpstreamUnavailable("Jira 폼 응답이 허용 크기(1MiB)를 초과했습니다.")


class _LoginForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.found = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "form" and (values.get("id") == "login-form" or re.search(
            r"(?:login|signin|j_security_check)", values.get("action") or "", re.I
        )):
            self.found = True
        if tag == "input" and values.get("name") in {"os_username", "os_password", "j_username", "j_password"}:
            self.found = True


def form_response(base, status, headers, body, identity_probe):
    """Return HTML intact, or raise an error without exposing HTML/tokens to logs."""
    headers = {str(key).lower(): str(value) for key, value in headers.items()}
    if status == 401:
        raise SessionExpired("Jira 인증 세션을 다시 확인해야 합니다.")
    check_html_size(len(body))
    charset = re.search(r"charset=([\w-]+)", headers.get("content-type", ""), re.I)
    try:
        html = body.decode(charset.group(1) if charset else "utf-8")
    except (LookupError, UnicodeError):
        raise UpstreamUnavailable("Jira 폼 응답의 문자 인코딩을 확인할 수 없습니다.") from None
    location = headers.get("location", "")
    try:
        redirect = urlsplit(location)
        foreign = bool(redirect.netloc and _origin(redirect) != _origin(urlsplit(base)))
    except ValueError:
        raise UpstreamUnavailable("Jira 폼의 이동 주소를 확인할 수 없습니다.") from None
    login_redirect = bool(re.search(r"/(?:login|signin|sso|sessiontimeout)(?:[./?]|$)", redirect.path, re.I))
    parser = _LoginForm()
    parser.feed(html)
    denied = headers.get("x-authentication-denied-reason") or headers.get("www-authenticate")
    seraph = headers.get("x-seraph-loginreason", "").upper()
    if denied or seraph not in {"", "OK", "NONE"} or parser.found or (
        300 <= status < 400 and (foreign or login_redirect)
    ):
        raise SessionExpired("Jira 인증 세션을 다시 확인해야 합니다.")
    if status >= 500:
        raise UpstreamUnavailable(f"Jira 폼 서버 응답 오류(HTTP {status}).")
    if status == 403:
        alive = identity_probe()
        if alive is True:
            raise PermissionDenied("Jira native form", "Jira 권한 또는 폼 보안 토큰으로 요청이 거부되었습니다.")
        if alive is False:
            raise SessionExpired("Jira 인증 세션이 만료되었습니다.")
        raise UpstreamUnavailable("Jira 폼 접근 권한을 확인하지 못했습니다.")
    return {"status": int(status), "html": html, "location": location}


def identity_response(status, data):
    if status in (401, 403):
        return False
    if status == 200 and isinstance(data, dict):
        identity = data.get("name") or data.get("key") or data.get("accountId")
        if identity:
            return "anonymous" not in str(identity).lower()
    return None

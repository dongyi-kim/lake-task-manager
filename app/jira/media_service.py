"""Avatar, link metadata, and authenticated media proxy behavior for JiraClient."""

import base64
import re
from html import unescape
from urllib.parse import quote, urlparse, urlsplit

from app.content.htmlsafe import proxy_attachment_images, proxy_attachment_links, proxy_images


_CONF_PAGEID_RE = re.compile(r"[?&]pageId=(\d+)|/pages/(\d+)(?:[/?#]|$)")


def _is_default_avatar_url(url):
    """Whether Jira's URL points to its generated silhouette instead of a user photo."""
    low = (url or "").lower()
    if not low:
        return True
    return "useravatar" in low and "ownerid=" not in low


class JiraMediaMixin:
    """Media-facing JiraClient methods; the owner supplies settings, cache, and provider."""

    AVATAR_TTL = 30 * 24 * 3600
    AVATAR_BYTES_MAX = 128 * 1024
    LINK_TITLE_TTL = 7 * 24 * 3600
    CONF_TITLE_TTL = 24 * 3600
    FAVICON_TTL = 7 * 24 * 3600

    def _avatar_url(self, user):
        cache_key = f"avatar_url:{self.env}:{user}"
        hit = self.cache.get(cache_key)
        if hit is not None:
            return hit or None
        try:
            raw = self.provider.get_json("/rest/api/2/user", params={"username": user})
        except Exception:
            return None
        urls = ((raw or {}).get("avatarUrls") or {})
        url = next((urls[size] for size in ("48x48", "32x32", "24x24", "16x16") if urls.get(size)), None)
        if _is_default_avatar_url(url):
            url = None
        self.cache.set(cache_key, url or "", self.AVATAR_TTL)
        return url

    def user_avatar(self, user):
        url = self._avatar_url(user)
        if not url:
            return None, None
        cache_key = f"avatar_bytes:{self.env}:{user}"
        cached = self.cache.get(cache_key)
        if isinstance(cached, dict) and cached.get("b64"):
            try:
                return base64.b64decode(cached["b64"]), cached.get("ct") or None
            except Exception:
                pass
        try:
            if url.startswith(("http://", "https://")):
                data, content_type = self.fetch_media(url)
            else:
                data, content_type = self.provider.get_bytes(url)
        except Exception:
            return None, None
        if data and len(data) <= self.AVATAR_BYTES_MAX:
            self.cache.set(cache_key, {
                "b64": base64.b64encode(data).decode("ascii"),
                "ct": content_type or "",
            }, self.AVATAR_TTL)
        return data, content_type

    def _media_allowed_host(self, host):
        host = (host or "").split("@")[-1].split(":")[0].lower()
        if not host:
            return False
        jira_host = urlparse(self.s.jira_base).netloc.split(":")[0].lower()
        if host == jira_host:
            return True
        if host in [value.lower() for value in getattr(self.s, "image_hosts", [])]:
            return True
        parent = ".".join(jira_host.split(".")[-2:]) if "." in jira_host else jira_host
        return host == parent or host.endswith("." + parent)

    def conf_title_by_id(self, url):
        base = (self.s.confluence_base or "").rstrip("/")
        if not base:
            return None
        match = _CONF_PAGEID_RE.search(url or "")
        page_id = (match.group(1) or match.group(2)) if match else None
        if not page_id:
            return None

        def produce():
            raw = self._conf_get_json(f"{base}/rest/api/content/{page_id}")
            return ((raw or {}).get("title") or "").strip()

        try:
            return self.cache.get_or_set(
                f"conftitle:{self.env}:{page_id}", self.CONF_TITLE_TTL, produce,
            )[0] or None
        except Exception:
            return None

    def link_title(self, url):
        try:
            parsed = urlsplit(url or "")
        except Exception:
            return None
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return None
        bases = [value for value in (
            self.s.jira_base, self.s.confluence_base, getattr(self.s, "bitbucket_base", ""),
        ) if value]
        origin = f"{parsed.scheme}://{parsed.netloc}"
        internal = any(origin.rstrip("/") == base.rstrip("/") for base in bases)

        def produce():
            try:
                if internal:
                    data, _content_type = self.provider.get_bytes(url)
                    html = (data or b"")[:200000].decode("utf-8", "replace")
                else:
                    import requests
                    response = requests.get(url, timeout=6, headers={"User-Agent": "LakeTaskManager"})
                    if response.status_code != 200:
                        return {}
                    html = response.text[:200000]
            except Exception:
                return {}
            match = (
                re.search(r'<meta[^>]+property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
                or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*property=["\']og:title["\']', html, re.I)
                or re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
            )
            if not match:
                return {}
            title = re.sub(r"\s+", " ", unescape(match.group(1))).strip()[:120]
            return {"t": title} if title else {}

        result = self.cache.get_or_set(f"linktitle:{url}", self.LINK_TITLE_TTL, produce)[0] or {}
        return result.get("t") or None

    def favicon(self, url):
        try:
            parsed = urlsplit(url or "")
        except Exception:
            return None, None
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return None, None
        origin = f"{parsed.scheme}://{parsed.netloc}"
        bases = [value for value in (
            self.s.jira_base, self.s.confluence_base, getattr(self.s, "bitbucket_base", ""),
        ) if value]
        internal = any(origin.rstrip("/") == base.rstrip("/") for base in bases)

        def produce():
            for path in ("/favicon.ico", "/favicon.png"):
                try:
                    if internal:
                        data, content_type = self.provider.get_bytes(origin + path)
                    else:
                        import requests
                        response = requests.get(
                            origin + path, timeout=5, headers={"User-Agent": "LakeTaskManager"},
                        )
                        if response.status_code != 200:
                            continue
                        data, content_type = response.content, response.headers.get("Content-Type")
                    if data:
                        return {
                            "d": base64.b64encode(data).decode(),
                            "ct": (content_type or "image/x-icon").split(";")[0].strip(),
                        }
                except Exception:
                    continue
            return {}

        result = self.cache.get_or_set(f"favicon:{origin}", self.FAVICON_TTL, produce)[0] or {}
        if not result.get("d"):
            return None, None
        try:
            return base64.b64decode(result["d"]), result.get("ct") or "image/x-icon"
        except Exception:
            return None, None

    def _media_url(self, url, download=False):
        if not url:
            return None
        base = (self.s.jira_base or "").rstrip("/")
        target = url if not url.startswith("/") else (base + url if self.env == "prod" else url)
        return ("/api/file?u=" if download else "/api/img?u=") + quote(target, safe="")

    def _proxy_media(self, html):
        if not html:
            return html
        if self.env != "prod":
            result = proxy_attachment_images(html)
        else:
            result = proxy_images(html, self.s.jira_base, self._media_allowed_host)
        return proxy_attachment_links(result, self.s.jira_base)

    def fetch_media(self, url):
        if not url:
            return None, None
        if url.startswith("/"):
            target = url
        elif url.startswith(("http://", "https://")):
            if not self._media_allowed_host(urlparse(url).netloc):
                return None, None
            target = url
        else:
            return None, None
        try:
            return self.provider.get_bytes(target)
        except Exception:
            return None, None

"""
Jira Activity Streams ATOM 피드 — 실 Jira(Atlassian Streams 플러그인) 형태를 충실히 모사.

참고(공식 예제): https://developer.atlassian.com/server/framework/atlassian-sdk/consuming-an-activity-streams-feed/
실 엔트리 구조:
  <entry>
    <title type="html"><a>{행위자}</a> {verb} <a>{KEY}</a></title>   ← HTML(요약 아님)
    <author><name/><usr:username/><activity:object-type>.../person</></author>
    <published/> <updated/>
    <category term="{kind}"/>
    <link rel="alternate" href=".../browse/{KEY}"/>
    <atlassian:application>com.atlassian.jira</atlassian:application>
    <activity:verb>http://activitystrea.ms/schema/1.0/{verb}</activity:verb>
    <activity:object>
      <title type="text">{KEY}</title>
      <summary type="text">{이슈 요약}</summary>    ← 진짜 요약은 여기
      <link rel="alternate" href=".../browse/{KEY}"/>
      <activity:object-type>http://streams.atlassian.com/syndication/types/issue</activity:object-type>
    </activity:object>
  </entry>
클라이언트(jira_client._parse_activity)는 category term=kind, alternate link=/browse/KEY,
activity:object/summary=요약, updated=일시 를 읽는다.
"""

from xml.sax.saxutils import escape

_ATOM = "http://www.w3.org/2005/Atom"
_ACTIVITY = "http://activitystrea.ms/spec/1.0/"
_ATLASSIAN = "http://streams.atlassian.com/syndication/general/1.0"
_USR = "http://streams.atlassian.com/syndication/username/1.0"
_MEDIA = "http://purl.org/syndication/atommedia"

# kind → activity:verb schema 단어 (실 Jira 매핑 근사)
_VERB = {"created": "post", "commented": "post", "logged work": "update",
         "resolved": "update", "transitioned": "update", "updated": "update"}


def _dt(d, hm=None):
    # ATOM 표준(UTC). 이슈/코멘트 직렬화(world._dt)와 같은 오프셋이라 mock==local 파리티 유지.
    return d.isoformat() + "T" + (hm or "09:00") + ":00.000+0000"


def feed(base_url, user, events, limit=20, display_name=None):
    dn = display_name or user
    profile = f"{base_url}/secure/ViewProfile.jspa?name={escape(user)}"
    parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<feed xmlns="{_ATOM}" xmlns:activity="{_ACTIVITY}" '
        f'xmlns:atlassian="{_ATLASSIAN}" xmlns:usr="{_USR}" xmlns:media="{_MEDIA}">',
        '<title>Activity Stream</title>',
        f'<id>urn:jira:activity:{escape(user)}</id>',
        f'<link rel="self" href="{escape(base_url)}/activity"/>',
    ]
    if events:
        parts.append(f'<updated>{_dt(events[0]["date"], events[0].get("time"))}</updated>')
    for i, e in enumerate(events[:limit]):
        key, kind, summ = e["key"], e["kind"], e.get("summary", "")
        when = _dt(e["date"], e.get("time"))
        browse = f"{base_url}/browse/{escape(key)}"
        verb = "http://activitystrea.ms/schema/1.0/" + _VERB.get(kind, "post")
        # title 은 HTML(엔티티 인코딩): "<a>행위자</a> {kind} <a>KEY</a>". 요약은 activity:object 에.
        title_html = (f'<a href="{profile}">{escape(dn)}</a> {escape(kind)} '
                      f'<a href="{browse}">{escape(key)}</a>')
        parts += [
            '<entry>',
            f'<id>urn:jira:activity:{escape(user)}:{i}</id>',
            f'<title type="html">{escape(title_html)}</title>',
            f'<published>{when}</published>',
            f'<updated>{when}</updated>',
            '<author>',
            f'<name>{escape(user)}</name>',
            f'<usr:username>{escape(user)}</usr:username>',
            '<activity:object-type>http://activitystrea.ms/schema/1.0/person</activity:object-type>',
            '</author>',
            f'<category term="{escape(kind)}"/>',
            f'<link rel="alternate" href="{browse}"/>',
            '<atlassian:application>com.atlassian.jira</atlassian:application>',
            f'<activity:verb>{verb}</activity:verb>',
            '<activity:object>',
            f'<id>urn:jira:activity:object:{escape(key)}:{i}</id>',
            f'<title type="text">{escape(key)}</title>',
            f'<summary type="text">{escape(summ)}</summary>',
            f'<link rel="alternate" href="{browse}"/>',
            '<activity:object-type>http://streams.atlassian.com/syndication/types/issue</activity:object-type>',
            '</activity:object>',
            '</entry>',
        ]
    parts.append('</feed>')
    return "\n".join(parts)

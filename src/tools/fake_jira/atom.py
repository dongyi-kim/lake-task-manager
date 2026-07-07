"""
Jira 활동 스트림 ATOM 피드 생성 — 실 Jira /activity 형태를 흉내.
클라이언트(jira_client._fetch_activity)가 stdlib xml.etree 로 파싱한다:
  entry 별 <updated> · <author><name> · <category term> · <link rel=alternate href> · <title>
"""

from xml.sax.saxutils import escape


def _dt(d):
    return d.isoformat() + "T09:00:00.000+0000"


def feed(base_url, user, events, limit=20):
    parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:activity="http://activitystrea.ms/spec/1.0/">',
        '<title>Activity Stream</title>',
        f'<id>urn:jira:activity:{escape(user)}</id>',
    ]
    if events:
        parts.append(f'<updated>{_dt(events[0]["date"])}</updated>')
    for i, e in enumerate(events[:limit]):
        key, kind, summ = e["key"], e["kind"], e.get("summary", "")
        title = f'{user} {kind} {key} - {summ}'
        href = f'{base_url}/browse/{key}'
        parts += [
            '<entry>',
            f'<id>urn:jira:activity:{escape(user)}:{i}</id>',
            f'<title type="html">{escape(title)}</title>',
            f'<published>{_dt(e["date"])}</published>',
            f'<updated>{_dt(e["date"])}</updated>',
            f'<author><name>{escape(user)}</name></author>',
            f'<category term="{escape(kind)}"/>',
            f'<link rel="alternate" href="{escape(href)}"/>',
            '<activity:verb>http://activitystrea.ms/schema/1.0/post</activity:verb>',
            '</entry>',
        ]
    parts.append('</feed>')
    return "\n".join(parts)

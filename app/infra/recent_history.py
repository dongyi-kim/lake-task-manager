"""Stable recent-document identities, independent of titles and navigation URLs."""
import json
import re
from urllib.parse import parse_qs, urlsplit


def canonical_url(url, kind, data=None):
    if kind != 'confluence':
        return url
    data = data if isinstance(data, dict) else {}
    try:
        parsed = urlsplit(url)
        match = re.search(r'/pages/(\d+)(?:/|$)', parsed.path)
        cid = str(data.get('pageId') or data.get('id') or
                  parse_qs(parsed.query).get('pageId', [''])[0] or (match[1] if match else ''))
        if not cid.isascii() or not cid.isdigit():
            return url
        base = str(data.get('confluenceBase') or '').rstrip('/')
        if not base:
            prefix = re.split(r'/(?:pages|display|spaces|x)(?:/|$)', parsed.path, maxsplit=1)[0]
            base = f'{parsed.scheme}://{parsed.netloc}{prefix}' if parsed.netloc else prefix
        return base + '/pages/viewpage.action?pageId=' + cid
    except ValueError:
        return url


def migrate_documents(conn):
    """Deduplicate old URL aliases without pretending migration is a new visit."""
    rows = conn.execute("SELECT url,kind,title,meta,type,data,opened_at FROM recent WHERE kind='confluence' ORDER BY opened_at DESC,rowid DESC").fetchall()
    unique = {}
    for row in rows:
        try:
            data = json.loads(row[5] or '{}')
        except (ValueError, TypeError):
            data = {}
        url = canonical_url(row[0], row[1], data)
        unique.setdefault(url, (url, *row[1:]))
    if rows:
        conn.execute("DELETE FROM recent WHERE kind='confluence'")
        conn.executemany("INSERT OR REPLACE INTO recent(url,kind,title,meta,type,data,opened_at) VALUES (?,?,?,?,?,?,?)", list(unique.values())[:100])

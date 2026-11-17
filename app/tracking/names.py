"""Explicit Tracking identity observations and cache-only label hydration."""
from datetime import datetime

from app.jira.person_names import useful_name


def _observed_at(value):
    try:
        if isinstance(value, (int, float)):
            return float(value)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def tracking_name_observations(snapshot, checkpoint=None, people=None):
    checkpoint = checkpoint or {}
    raw_issues, sources = checkpoint.get("issues") or {}, checkpoint.get("sources") or {}
    issues = snapshot.get("issues") or []
    observations, times = {}, {}
    source_times = [_observed_at(value) for value in (snapshot.get("coverage") or {}).get("sourceFetchedAt") or []]
    source_floor = min((value for value in source_times if value is not None), default=None)
    selected = None if people is None else {str(uid).casefold() for uid in people}

    def observe(uid, name, observed):
        uid = str(uid).casefold()
        if uid and observed is not None and useful_name(uid, name) and (selected is None or uid in selected):
            if uid not in observations or observations[uid][1] < observed:
                observations[uid] = (str(name), observed)

    def person(raw, observed):
        if not isinstance(raw, dict):
            return
        if raw.get("displayName"):
            uid = raw.get("name") or raw.get("key") or raw.get("accountId") or raw.get("id")
        else:
            uid = raw.get("id") or raw.get("accountId") or raw.get("key") or raw.get("name")
        if uid:
            observe(uid, raw.get("displayName") or raw.get("display") or raw.get("name"), observed)

    for issue in issues:
        key = issue.get("key")
        raw = raw_issues.get(key) or {}
        observed = _observed_at(issue.get("fetchedAt") or raw.get("_trackingFetchedAt"))
        component_observed = min(observed, source_floor) if observed is not None and source_floor is not None else None
        times[key] = (observed, component_observed)
        for field in ("assignee", "creator", "reporter"):
            person(issue.get(field), observed)
            observe(issue.get(field + "Id") or "", issue.get(field + "Name"), observed)
            person((raw.get("fields") or {}).get(field), observed)
        source = sources.get(key) or {}
        history = source.get("history") or raw.get("changelog") or {}
        for row in history.get("histories") or []:
            person(row.get("author"), component_observed)
        for row in source.get("comments") or []:
            person(row.get("author"), component_observed)
            person(row.get("updateAuthor"), component_observed)
    events = [*(snapshot.get("events") or []), *(e for issue in issues for e in issue.get("events") or [])]
    for event in events:
        issue_observed, component_observed = times.get(event.get("issueKey"), (None, None))
        # History/comment caches can predate the current raw issue fetch. An old
        # run without component provenance must not renew an actor's name age.
        observed = issue_observed if event.get("kind") == "created" else component_observed
        observe(event.get("actorId") or "", event.get("actorName"), observed)
        person(event.get("author"), observed)
    return observations


def remember_tracking_names(client, observations):
    rows = [(uid, name, observed) for uid, (name, observed) in observations.items()]
    batch = getattr(client, "remember_display_names", None)
    if callable(batch):
        batch(rows)
    elif callable(getattr(client, "remember_display_name", None)):
        for row in rows:
            client.remember_display_name(*row)


def observe_tracking_names(snapshot, client, checkpoint=None):
    if callable(getattr(client, "remember_display_name", None)):
        remember_tracking_names(client, tracking_name_observations(snapshot, checkpoint))


def cached_tracking_names(snapshot, client):
    """Enrich this response copy and return one memoized lookup for rich comments."""
    cached = getattr(client, "display_name_cached", None)
    memo = {}

    def lookup(uid):
        key = str(uid or "").casefold()
        if key not in memo:
            memo[key] = cached(uid) if callable(cached) and key else None
        return memo[key]

    def label(row, id_field, name_field, uid=None):
        uid = row.get(id_field) or uid
        if uid and not useful_name(uid, row.get(name_field)):
            name = lookup(uid)
            if useful_name(uid, name):
                row[name_field] = name

    for issue in snapshot.get("issues") or []:
        for field in ("assignee", "creator", "reporter"):
            raw = issue.get(field)
            uid = issue.get(field + "Id") or (raw.get("id") if isinstance(raw, dict) else None)
            label(issue, field + "Id", field + "Name", uid)
            if isinstance(raw, dict):
                label(raw, "id", "name", uid)
    events = [*(snapshot.get("events") or []), *(e for issue in snapshot.get("issues") or [] for e in issue.get("events") or [])]
    for event in events:
        label(event, "actorId", "actorName")
        if isinstance(event.get("author"), dict):
            label(event["author"], "id", "name", event.get("actorId"))
    return lookup

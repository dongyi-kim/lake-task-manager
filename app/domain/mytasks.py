"""'내 Task' 모델 — 세션 사용자가 담당한 일감을 **부모 Task 단위 그룹**으로 묶어 돌려준다.

왜 그룹인가: 화면이 세 가지 뷰(시간 우선 / 부모 클러스터 / 계층 우선)를 제공하는데, 셋 다
같은 사실에서 파생된다 — "내 실행 원자(atom)가 무엇이고, 그게 어느 부모·어느 Epic 아래 있으며,
그 부모는 얼마나 진행됐는가". 그래서 서버는 뷰가 아니라 **그 사실 하나**를 준다.

용어:
- atom  : 내가 실제로 실행하는 단위. 내가 담당한 Sub-Task, 또는 하위가 없는 내 Task.
          내가 Task 담당이면서 하위가 있으면 그 Task 자체는 원자가 아니다(하위가 실행 단위).
- group : 원자를 담는 부모 Task. 내가 하위만 담당하는 남의 Task 도 그룹이 된다(맥락으로 필요).
- others: 같은 그룹 안의 **동료 하위** — 처음부터 펼치지 않고 집계만 준다(공간 절약).

정렬 키는 **여기서 계산해 내려보낸다**(프론트에서 매번 재계산하지 않게):
- atom.dueDays / atom.priRank
- group.urgency(= 내 원자 중 가장 급한 마감) / group.priRank(= 가장 높은 우선순위)
- group.pct(= SP 가중 롤업, 부분점수 포함)
"""

import re
import secrets
import threading
from datetime import date, datetime

from app.domain.names import real_name
from app.domain.progress import norm_cat

# 우선순위 정규화 — 인스턴스마다 이름이 다를 수 있어 이름을 소문자로 맞춰 본다.
# 못 알아보면 중간(2)으로 둔다: 모르는 값이 맨 위나 맨 아래로 튀는 게 제일 나쁘다.
# 우선순위 이름 → 등급(0=가장 급함). Jira 는 인스턴스마다 우선순위 **이름 체계가 다르다** —
# 기본 스킴(Highest/High/…)과 Blocker/Critical/Major/Minor/Trivial 스킴이 둘 다 흔하다.
# 모르는 이름은 2(보통)로 떨어지므로, 안 적어 두면 Blocker 가 '보통' 으로 표시된다(실제로 그랬다).
_PRI_RANK = {
    "highest": 0, "high": 1, "medium": 2, "normal": 2, "low": 3, "lowest": 4,
    "blocker": 0, "critical": 1, "major": 2, "minor": 3, "trivial": 4,
    "urgent": 0, "p1": 0, "p2": 1, "p3": 2, "p4": 3, "p5": 4,
}
_PRI_BAND = {0: "high", 1: "high", 2: "mid", 3: "low", 4: "low"}
# 'P0-Blocker' 처럼 **접두사 숫자로 등급을 말하는** 체계(사내 표준). 이름보다 이게 우선한다.
_P_PREFIX = re.compile(r"^\s*P\s*(\d+)", re.I)

# 상태 부분점수 — 완료만 100% 로 치면 진행 중인 일이 통째로 0 이라 롤업이 실제보다 어둡다.
# (WBS/Epic 진척률은 '완료/전체' 이진이 원칙이지만, 이 화면은 개인 트래킹용 체감 지표라 다르다.
#  두 숫자를 섞어 쓰지 말 것 — 목적이 다르다.)
_PARTIAL = {"done": 1.0, "inprogress": 0.4, "todo": 0.0}

_NO_DUE = 10 ** 6           # 마감 없음 = 맨 뒤. None 정렬 분기를 안 만들려고 큰 수를 쓴다
# '최근 완료' 로 볼 기간 선택지. 1주는 워크로드의 done7d 와 같은 창이다.
DONE_WINDOWS = {"1w": 7, "1m": 30}
ASYNC_SYNC_TTL = 30 * 60
TASK_STREAM_CONTRACT = "task-snapshot.v1"
_TASK_SYNC_LOCK = threading.RLock()


def _days_until(due, today):
    if not due:
        return None
    try:
        d = datetime.strptime(str(due)[:10], "%Y-%m-%d").date()
    except Exception:
        return None
    return (d - today).days


def pri_rank(name):
    """우선순위 이름 → 등급(0=가장 급함). **이 프로젝트의 유일한 판정처**다.

    ★ 사내 체계는 'P0-Blocker … P4-Trivial' 이라 **접두사 숫자가 곧 등급**이다. 이름을 통째로
      표에서 찾는 방식은 사내 이름이 바뀌는 순간(P1-Critical → P1-치명) 전부 '보통' 으로
      떨어진다. 그래서 숫자를 먼저 읽고, 그게 없을 때만 이름 표로 간다
      (Highest/High/… · Blocker/Critical/… 같은 표준 스킴).
    """
    n = (name or "").strip()
    m = _P_PREFIX.match(n)
    if m:
        return min(int(m.group(1)), 4)
    return _PRI_RANK.get(n.lower(), 2)


def _pri(f):
    name = ((f.get("priority") or {}).get("name") or "").strip()
    return name, pri_rank(name)


def _cat(f):
    st = f.get("status") or {}
    return norm_cat((st.get("statusCategory") or {}).get("key"))


def _node(raw, today, epic_field):
    """이슈 원본 → 화면이 쓰는 얇은 노드."""
    f = raw.get("fields") or {}
    a = f.get("assignee") or {}
    rp = f.get("reporter") or {}
    pri_name, pri_rank = _pri(f)
    dd = _days_until(f.get("duedate"), today)
    itype = (f.get("issuetype") or {}).get("name") or ""
    return {
        "key": raw.get("key") or "",
        "title": f.get("summary") or "",
        "type": itype,
        "isSub": bool((f.get("issuetype") or {}).get("subtask")),
        "status": (f.get("status") or {}).get("name") or "",
        "statusCategory": _cat(f),
        "assignee": real_name(a.get("displayName") or a.get("name")) or None,
        "assigneeId": a.get("name"),
        "reporter": real_name(rp.get("displayName") or rp.get("name")) or None,
        "reporterId": rp.get("name"),
        "pri": pri_name, "priRank": pri_rank, "priBand": _PRI_BAND.get(pri_rank, "mid"),
        "due": f.get("duedate") or None,
        "dueDays": dd,
        "resolved": f.get("resolutiondate") or None,   # 완료 카드는 마감 대신 완료일을 보인다
        "sp": f.get(epic_field["sp"]),
        "epic": f.get(epic_field["epic"]) or None,
        # 사용자 VoC 는 Epic 이 없어도 **전용 Epic 처럼** 취급한다(색·묶음 모두).
        # 단 Epic 이 배정돼 있으면 그 Epic 이 우선이다 — 워크로드 Epic 분포와 같은 규칙.
        "voc": epic_field["voc"] in [c.get("name") for c in (f.get("components") or [])],
        "components": [c.get("name") for c in (f.get("components") or []) if c.get("name")],
        "parentKey": ((f.get("parent") or {}).get("key")) or None,
    }


def _sp(n):
    """SP 누락 시 가중치 — 롤업이 0 으로 무너지지 않게 최소 1 (Bug 는 0 이 정상값이라 그대로)."""
    v = n.get("sp")
    if v is None:
        return 0 if n.get("type") == "Bug" else 1
    try:
        return float(v)
    except Exception:
        return 1


def _rollup(nodes):
    tot = sum(_sp(n) for n in nodes)
    if not tot:
        return 0
    got = sum(_sp(n) * _PARTIAL.get(n["statusCategory"], 0) for n in nodes)
    return int(round(got / tot * 100))


def _role_of(node, me):
    a, r = node["assigneeId"] == me, node["reporterId"] == me
    return "both" if (a and r) else ("assignee" if a else ("reporter" if r else None))


def _matches_scope(node, context):
    """Return whether one child is an execution atom for a saved Task-page scope."""
    kind = context.get("scopeKind")
    if kind == "module":
        ids = set(context.get("moduleIds") or ())
        components = set(context.get("moduleComponents") or ())
        return (node["assigneeId"] in ids or node["reporterId"] in ids
                or any(value in components for value in (node.get("components") or ())))
    if kind == "uassignee":
        return node["assigneeId"] == context.get("specificUser")
    if kind == "ureporter":
        return node["reporterId"] == context.get("specificUser")
    if kind == "epic":
        return node.get("epic") == context.get("epic")
    if kind == "jql":
        return node["key"] in set(context.get("matchedKeys") or ())
    me = context.get("me")
    if kind == "reporter":
        return node["reporterId"] == me
    if kind == "both":
        return node["assigneeId"] == me or node["reporterId"] == me
    return node["assigneeId"] == me


def _hydrated_group(parent, children, context, expected_keys):
    """Build the same group payload as ``build_my_tasks`` for one independently loaded parent."""
    me = context.get("me")
    parent = dict(parent)
    parent["role"] = _role_of(parent, me)
    children = [dict(child) for child in children]
    for child in children:
        child["role"] = _role_of(child, me)

    loaded = {child["key"] for child in children}
    expected = list(dict.fromkeys(expected_keys or loaded))
    missing = [key for key in expected if key not in loaded]
    initial = (context.get("groupMeta") or {}).get(parent["key"], {})
    matched = set(context.get("matchedKeys") or ())
    parent_matched = parent["key"] in matched
    mine = [child for child in children
            if child["key"] in matched or (parent_matched and _matches_scope(child, context))]
    mine_keys = {child["key"] for child in mine}
    others = [child for child in children if child["key"] not in mine_keys]
    if not expected and not children:
        mine = [parent]
        others = []

    mine.sort(key=lambda node: (
        node["dueDays"] if node["dueDays"] is not None else _NO_DUE,
        node["priRank"], node["key"]))
    complete = not missing
    return {
        "key": parent["key"], "title": parent["title"], "type": parent["type"],
        "mine": initial.get("mine", _matches_scope(parent, context)),
        "role": initial.get("role", parent.get("role")),
        "assignee": parent["assignee"], "assigneeId": parent["assigneeId"],
        "status": parent["status"], "statusCategory": parent["statusCategory"],
        "reporter": parent.get("reporter"), "reporterId": parent.get("reporterId"),
        "voc": parent.get("voc"), "epic": parent["epic"],
        "pri": parent["pri"], "priRank": min((node["priRank"] for node in mine), default=2),
        "priBand": parent["priBand"], "due": parent["due"], "dueDays": parent["dueDays"],
        "atoms": mine, "others": others, "hasSubs": bool(expected or children),
        "standalone": not bool(expected or children),
        "pct": _rollup(children) if complete and children else None,
        "kidsDone": sum(1 for child in children if child["statusCategory"] == "done"),
        "kidsTotal": len(expected) if expected else len(children),
        "othersDone": sum(1 for child in others if child["statusCategory"] == "done"),
        "urgency": min((node["dueDays"] for node in mine if node["dueDays"] is not None), default=None),
        "childrenPending": bool(missing), "childrenLoaded": complete,
        "childrenLoadedCount": len(children),
    }


def _sync_key(client, sync_id):
    return "mytasks:sync:%s:%s" % (getattr(client, "env", "?"), sync_id)


def hydrate_my_task_group(client, sync_id, group_key):
    """Hydrate exactly one Task-with-SubTask group from an opaque base-response snapshot."""
    state = client.cache.get(_sync_key(client, sync_id))
    if not isinstance(state, dict):
        raise LookupError("Task 동기화 정보가 만료되었습니다.")
    session_user = (client.current_user() or {}).get("id")
    if session_user != state.get("sessionUser"):
        raise PermissionError("다른 사용자 Task 동기화 정보입니다.")
    expected = (state.get("children") or {}).get(group_key)
    if expected is None:
        raise KeyError("동기화 대상 Task가 아닙니다.")

    today = client.s_today() if hasattr(client, "s_today") else date.today()
    fields = {"sp": client.s.sp_field_id, "epic": client.s.epic_link_field_id,
              "voc": client.s.voc_component}
    parents = client.issues_by_keys([group_key], light=True)
    if not parents:
        raise LookupError("Parent Task를 불러오지 못했습니다.")
    parent = _node(parents[0], today, fields)
    children = [_node(raw, today, fields)
                for raw in client.issues_by_keys(expected, light=True)]
    return _normalize_task_groups([
        _hydrated_group(parent, children, state.get("context") or {}, expected)
    ])[0]


def hydrate_my_task_snapshot(client, sync_id, group_key):
    """Hydrate one Parent and publish the next authoritative cumulative Task snapshot.

    A full child response can claim rows that the deferred base temporarily rendered as standalone
    groups. Resolving that ownership in the browser recreates the same append/dedup/sort logic that
    the Task stream deliberately moved to the server. Keep the mutable sync model server-side and
    return a monotonically versioned replacement instead.
    """
    group = hydrate_my_task_group(client, sync_id, group_key)
    sync_key = _sync_key(client, sync_id)
    session_user = (client.current_user() or {}).get("id")
    with _TASK_SYNC_LOCK:
        state = client.cache.get(sync_key)
        if not isinstance(state, dict):
            raise LookupError("Task 동기화 정보가 만료되었습니다.")
        if session_user != state.get("sessionUser"):
            raise PermissionError("다른 사용자 Task 동기화 정보입니다.")
        model = state.get("model")
        if not isinstance(model, dict):
            raise LookupError("Task 동기화 모델이 만료되었습니다.")

        hydrated = set(state.get("hydrated") or ())
        if group_key not in hydrated:
            groups = _normalize_task_groups([
                group if previous.get("key") == group_key else previous
                for previous in model.get("groups") or ()])
            atoms = [atom for item in groups for atom in item.get("atoms") or ()]
            model["groups"] = groups
            model["counts"] = _counts(atoms)
            hydrated.add(group_key)

        sequence = int(state.get("snapshotSequence") or 0) + 1
        model["syncPending"] = max(0, len(state.get("children") or {}) - len(hydrated))
        model["streamComplete"] = True
        model["snapshotSequence"] = sequence
        state["model"] = model
        state["hydrated"] = sorted(hydrated)
        state["snapshotSequence"] = sequence
        client.cache.set(sync_key, state, ASYNC_SYNC_TTL)
        return {"contract": TASK_STREAM_CONTRACT, "type": "snapshot", "syncId": sync_id,
                "sequence": sequence, "model": model}


def hydrate_my_task_epics(client, sync_id):
    """Load Epic labels independently so cards are not held behind serial badge lookups."""
    state = client.cache.get(_sync_key(client, sync_id))
    if not isinstance(state, dict):
        raise LookupError("Task 동기화 정보가 만료되었습니다.")
    session_user = (client.current_user() or {}).get("id")
    if session_user != state.get("sessionUser"):
        raise PermissionError("다른 사용자 Task 동기화 정보입니다.")
    return client.epic_metadata_many(state.get("epics") or ())


class _TaskRowsClient:
    """Delegate every Jira operation except the Task-page search itself.

    Progressive Task rendering receives one cached JQL leaf at a time.  Reusing the normal model
    builder with this tiny adapter keeps grouping, scope matching and statistics identical to the
    non-streaming API instead of reimplementing those rules in the HTTP layer.
    """

    def __init__(self, client, rows, recorder=None, cache_only=False):
        self._client = client
        self._rows = list(rows or ())
        self._recorder = recorder
        self._cache_only = bool(cache_only)

    def __getattr__(self, name):
        return getattr(self._client, name)

    def search_issues(self, jql, max_results=200, cache_key=None):
        if self._recorder is not None:
            self._recorder.append((cache_key, jql, max_results))
            return []
        if max_results is None:
            return list(self._rows)
        return self._rows[:max(0, int(max_results))]

    def issues_by_keys(self, keys, light=False):
        if not self._cache_only:
            return self._client.issues_by_keys(keys, light=light)
        requested = list(dict.fromkeys(key for key in (keys or ()) if key))
        if not requested:
            return []
        by_key = {row.get("key"): row for row in self._rows if (row or {}).get("key")}
        missing = [key for key in requested if key not in by_key]
        if missing:
            full = self._client.cache.get_many(
                f"issue:{self._client.env}:{key}" for key in missing)
            light_rows = self._client.cache.get_many(
                f"issueL:{self._client.env}:{key}" for key in missing) if light else {}
            for key in missing:
                row = full.get(f"issue:{self._client.env}:{key}")
                if row is None and light:
                    row = light_rows.get(f"issueL:{self._client.env}:{key}")
                if isinstance(row, dict) and row.get("key") == key:
                    by_key[key] = row
        return [by_key[key] for key in requested if key in by_key]


def _leaf_status_axis(leaf):
    """Best-effort status band for progress display; membership still comes from issue rows."""
    value = re.sub(r'\s+', ' ', str(leaf or '').lower())
    if re.search(r'statuscategory\s*=\s*(?:"done"|done)(?:\s|$|\))', value):
        return "done"
    if re.search(r'statuscategory\s*=\s*(?:"in progress"|in progress)(?:\s|$|\))', value):
        return "inprogress"
    if re.search(r'statuscategory\s*=\s*(?:"to do"|to do)(?:\s|$|\))', value):
        return "todo"
    return None


def iter_my_task_models(client, user=None, include_done=False, limit=None, scope="assignee",
                        open_filter="all", prog_filter="all", done_filter="1w",
                        request_token=None):
    """Yield versioned, authoritative cumulative Task snapshots.

    Jira leaves still complete independently and are cached before this iterator sees them.  The
    server owns append/dedup/order/statistics: after each leaf it rebuilds one cumulative model from
    all successful rows so the browser only replaces state when ``requestToken`` and monotonic
    ``sequence`` match its active request.  A disconnected browser therefore cannot corrupt another
    filter, while every leaf that completed before disconnect remains reusable in the Jira cache.
    """
    token = str(request_token or secrets.token_urlsafe(18))[:128]
    sequence = 0
    axes = ["todo", "inprogress", "done"]

    def event(model, *, done=False, replace=True, completed_leaf=None,
              completed_leaves=None, errors=(), leaf_done=0, leaf_total=0):
        nonlocal sequence
        payload = {
            "contract": TASK_STREAM_CONTRACT,
            "type": "snapshot",
            "requestToken": token,
            "sequence": sequence,
            "replace": bool(replace),
            "done": bool(done),
            "axes": axes,
            "model": model,
            "statistics": (model or {}).get("counts") if isinstance(model, dict) else None,
            "progress": {
                "completed": leaf_done,
                "total": leaf_total,
                "succeeded": leaf_done - len(errors),
                "failed": len(errors),
            },
            # Compatibility aliases make the transition observable without coupling old clients
            # to the new nested progress shape.
            "leafDone": leaf_done,
            "leafTotal": leaf_total,
            "completedLeaf": completed_leaf,
            "completedLeaves": list(completed_leaves or (
                [completed_leaf] if completed_leaf else [])),
            "partial": bool(errors),
            "partialErrors": list(errors),
        }
        sequence += 1
        return payload

    calls = []
    planner = _TaskRowsClient(client, (), recorder=calls)
    empty = build_my_tasks(
        planner, user=user, include_done=include_done, limit=limit, scope=scope,
        open_filter=open_filter, prog_filter=prog_filter, done_filter=done_filter,
        defer_children=True, create_sync=False)
    if not calls:
        yield event(empty, done=True, leaf_done=0, leaf_total=0)
        return

    query_results = [[] for _call in calls]
    query_failed = [False for _call in calls]
    errors = []
    query_total = len(calls)
    leaf_done = 0
    # The precise total is available without running an upstream request.  Unsupported syntax is
    # one legacy chunk and remains correct, merely less granular.
    leaf_total = 0
    for _cache_key, jql, _max_results in calls:
        try:
            leaf_total += len(client._compile_jql(jql).leaves)
        except Exception:
            leaf_total += 1

    # Do not replace a warm same-filter browser model with an empty planning shell.  A new filter
    # already starts from its own empty cache entry, while both cases receive axes/progress now.
    yield event(empty, replace=False, leaf_done=0, leaf_total=leaf_total)

    def cumulative_model(*, create_sync=False):
        rows_by_key = {}
        for rows, (_cache_key, _jql, max_results) in zip(query_results, calls):
            selected = rows if max_results is None else rows[:max(0, int(max_results))]
            for row in selected:
                key = (row or {}).get("key")
                if key:
                    rows_by_key[key] = row
        return build_my_tasks(
            _TaskRowsClient(client, rows_by_key.values(), cache_only=not create_sync), user=user,
            include_done=include_done, limit=limit, scope=scope,
            open_filter=open_filter, prog_filter=prog_filter, done_filter=done_filter,
            defer_children=True, create_sync=create_sync)

    for query_index, (cache_key, jql, max_results) in enumerate(calls):
        cached_batch = []
        for chunk in client.iter_search_issue_chunks(jql, light=True):
            leaf_done += 1
            leaf = chunk.get("leaf")
            axis = _leaf_status_axis(leaf)
            if chunk.get("error"):
                query_failed[query_index] = True
                error = dict(chunk["error"])
                errors.append(error)
                completed = {
                    "leaf": leaf, "axis": axis, "status": "error", "error": error,
                    "leafIndex": chunk.get("leafIndex"),
                    "queryIndex": query_index, "queryTotal": query_total,
                }
                if chunk.get("coalesceCached"):
                    cached_batch.append(completed)
                else:
                    yield event(
                        cumulative_model(), errors=errors, leaf_done=leaf_done,
                        leaf_total=leaf_total, completed_leaf=completed)
                continue
            combined = list(chunk.get("combined") or ())
            query_results[query_index] = (
                combined if max_results is None else combined[:max(0, int(max_results))]
            )
            completed = {
                "leaf": leaf, "axis": axis, "status": "success",
                "leafIndex": chunk.get("leafIndex"),
                "queryIndex": query_index, "queryTotal": query_total,
            }
            if chunk.get("coalesceCached"):
                cached_batch.append(completed)
            else:
                yield event(
                    cumulative_model(), errors=errors, leaf_done=leaf_done,
                    leaf_total=leaf_total, completed_leaf=completed)
        if cached_batch:
            # A fully warm subquery completes without upstream waiting.  Emit one cumulative
            # snapshot instead of N near-identical payloads; cold leaves remain one-by-one.
            yield event(
                cumulative_model(), errors=errors, leaf_done=leaf_done,
                leaf_total=leaf_total, completed_leaf=cached_batch[-1],
                completed_leaves=cached_batch)
        # Preserve the historical mt:* aggregate cache only after this subquery is complete.  A
        # disconnected stream never publishes a partial list as a complete API-cache hit.
        if cache_key is not None and not query_failed[query_index]:
            client.cache.set(cache_key + ":light", query_results[query_index],
                             client.s.cache_ttl_seconds)

    complete = cumulative_model(create_sync=True)
    yield event(complete, done=True, errors=errors,
                leaf_done=leaf_done, leaf_total=leaf_total)


def build_my_tasks(client, user=None, include_done=False, limit=None, scope="assignee",
                   open_filter="all", prog_filter="all", done_filter="1w",
                   defer_children=False, create_sync=True):
    """세션 사용자(또는 user 지정)의 '내 Task' 모델.

    scope: assignee(담당) | reporter(내가 등록) | both. '내 일'의 정의는 사람마다 다르다 —
    남에게 넘긴 뒤 결과를 봐야 하는 사람에게는 reporter 도 자기 일이다. JQL 조건이라 서버 몫.
    open_filter: all | 2w      — '할당됨' 축에 무엇을 담을지
    done_filter: 1w | 1m       — '최근 완료' 축의 기간

    담당 이슈를 한 번에 긁고(=JQL 1회), 부모/형제는 **필요한 것만** 배치로 채운다.
    prod SSO 는 직렬이라 왕복 횟수가 곧 체감 속도다.
    """
    me = user or (client.current_user() or {}).get("id")
    if not me:
        return {"user": None, "groups": [], "epics": [], "error": "세션 사용자를 확인할 수 없습니다."}

    today = client.s_today() if hasattr(client, "s_today") else date.today()
    ef = {"sp": client.s.sp_field_id, "epic": client.s.epic_link_field_id,
          "voc": client.s.voc_component}

    # 1) 내가 담당(또는 등록)한 이슈.
    #    화면이 '할당됨 / 진행 중 / 최근 완료' 3상태를 축으로 쓰므로 상태별 조건을 **한 질의**에 담는다
    #    (버킷마다 따로 부르면 prod SSO 는 직렬이라 왕복이 그대로 지연이 된다).
    #      · 할당됨: 오래 방치된 것까지 다 볼지(all), 최근 2주 안에 손댄 것만 볼지(2w)
    #      · 진행 중: 항상 전부 — 지금 하는 일을 숨기면 안 된다
    #      · 완료   : 최근 N일 안에 끝낸 것만(완료 전체를 넣으면 화면이 과거로 가득 찬다)
    # 스코프 해석 — assignee(기본) | reporter | both | module:<모듈명>.
    #   module 스코프: 그 모듈의 일감 = 모듈 인력(people.yaml) 중 하나가 담당/보고 **또는**
    #   티켓 component 가 그 모듈. (매니저 구분 없이 누구나 다른 모듈도 볼 수 있다.)
    #   assignee(기본) | reporter | both | mymodules | module:<모듈> |
    #   assignee:<사번> | reporter:<사번> | epic:<키>  — 뒤 셋은 '특정 사람/Epic' 필터(퀵필터 picker).
    mod_ids, mod_comps = set(), set()
    spec_user, epic_scope, raw_jql = None, None, None

    def _lit(s):
        """따옴표로 감싸는 JQL 리터럴 안전화 — 따옴표·역슬래시 제거(스코프 문자열은 사용자 입력)."""
        return "".join(c for c in (s or "") if c not in '"\\').strip()

    if scope == "mymodules" or scope.startswith("module:"):
        from app.infra.settings import load_people, modules_of
        people = load_people() or {}
        if scope == "mymodules":
            targets = modules_of(me)                      # 내가 속한 모듈 전체(union)
        else:
            m = scope.split(":", 1)[1].strip()
            targets = [m] if m in people else []
        for mod in targets:
            for pid in (people.get(mod) or []):
                if pid:
                    mod_ids.add(pid)
            mod_comps.add(mod)
        scope_kind = "module" if targets else "assignee"  # 대상 없으면 안전 폴백
    elif scope.startswith("assignee:"):
        spec_user = _lit(scope.split(":", 1)[1])
        scope_kind = "uassignee" if spec_user else "assignee"
    elif scope.startswith("reporter:"):
        spec_user = _lit(scope.split(":", 1)[1])
        scope_kind = "ureporter" if spec_user else "assignee"
    elif scope.startswith("epic:"):
        epic_scope = "".join(c for c in scope.split(":", 1)[1] if c.isalnum() or c == "-")
        scope_kind = "epic" if epic_scope else "assignee"
    elif scope.startswith("jql:"):
        # 고급 검색 — 사용자가 UI 빌더/직접 입력으로 만든 **완전한 JQL**. 이건 의도된 자유 질의라
        # 리터럴 안전화를 하지 않는다(그 자체가 질의문이다). 로컬 1인 앱이라 자기 Jira 를 자기가 조회.
        raw_jql = scope.split(":", 1)[1].strip()
        scope_kind = "jql" if raw_jql else "assignee"
    else:
        scope_kind = scope if scope in ("reporter", "both") else "assignee"

    def _in(field, ids):
        return "%s in (%s)" % (field, ", ".join('"%s"' % i for i in sorted(ids)))

    # 상태 축 조건(할당됨/진행중/최근완료 세 버킷을 한 질의에). 캐시 키에도 반영해 필터가 다르면
    # 다른 캐시가 되게 한다(2w/1w 결과가 섞이지 않게).
    open_cond = 'statusCategory = "To Do"'
    if open_filter == "2w":
        open_cond += " AND updated >= -14d"
    # 진행 중: 기본은 전부(지금 하는 일을 숨기지 않는다). 1m 이면 최근 1달 안에 손댄 것만
    # (오래 멈춰 사실상 방치된 '진행 중' 을 감춘다 — 할당됨의 2w 와 같은 취지).
    prog_cond = 'statusCategory = "In Progress"'
    if prog_filter == "1m":
        prog_cond += " AND updated >= -30d"
    done_days = DONE_WINDOWS.get(done_filter, DONE_WINDOWS["1w"])
    parts = ["(%s)" % prog_cond, "(%s)" % open_cond,
             "(statusCategory = Done AND resolved >= -%dd)" % done_days]
    cond = "(%s)" % " OR ".join(parts)
    filt = "all" if include_done else ("%s.%s.%s" % (open_filter, prog_filter, done_filter))
    env = getattr(client, "env", "?")

    def _where(base):
        j = base if include_done else "%s AND %s" % (base, cond)   # include_done 이면 상태 무관 전체
        return j + " ORDER BY duedate ASC"

    # (cache_key, jql) 서브쿼리 — `IN`/`OR` 을 **사람 하나치**로 쪼갠다. 사람별 목록은 모듈이
    # 겹치면 그대로 재사용되므로 각자 캐시 대상이다. 'me' 도 사람이라 같은 규칙을 쓴다:
    # currentUser 로 온 사번이든 picker(사번)든 **같은 사번 → 같은 `asg:{id}`/`rep:{id}` 키**로
    # 모여 currentUser()/username 이 따로 캐시되거나 중복 조회되지 않는다.
    subqs = []

    def _add(kkey, base):
        subqs.append(("mt:%s:%s:%s" % (env, kkey, filt), _where(base)))

    if scope_kind == "module":
        for p in sorted(mod_ids):
            _add("asgrep:%s" % p, '(assignee = "%s" OR reporter = "%s")' % (p, p))
        if mod_comps:
            _add("cmp:%s" % ",".join(sorted(mod_comps)), _in("component", mod_comps))
        if not subqs:
            _add("asg:%s" % me, 'assignee = "%s"' % me)
    elif scope_kind == "uassignee":
        _add("asg:%s" % spec_user, 'assignee = "%s"' % spec_user)
    elif scope_kind == "ureporter":
        _add("rep:%s" % spec_user, 'reporter = "%s"' % spec_user)
    elif scope_kind == "epic":
        _add("epic:%s" % epic_scope, '"Epic Link" = %s' % epic_scope)
    elif scope_kind == "jql":
        # 완전한 자유 질의 — 상태 조건으로 감싸지 않고(그 자체가 전체 질의), 캐시도 안 한다(ad-hoc).
        jq = raw_jql if ("order by" in raw_jql.lower()) else (raw_jql + " ORDER BY duedate ASC")
        subqs.append((None, jq))
    elif scope_kind == "reporter":
        _add("rep:%s" % me, 'reporter = "%s"' % me)
    elif scope_kind == "both":                            # 담당·보고를 따로 쪼개 각자 캐시(합집합은 병합)
        _add("asg:%s" % me, 'assignee = "%s"' % me)
        _add("rep:%s" % me, 'reporter = "%s"' % me)
    else:
        _add("asg:%s" % me, 'assignee = "%s"' % me)

    # 실행 + **취합/중복제거** — 한 티켓이 여러 사람 질의에 걸릴 수 있으니(예: A가 담당, B가 보고)
    # 이슈 키로 dedup 한다. Epic 은 실행 단위가 아니라 묶음이라 목록에서 뺀다.
    seen, mine_raw = set(), []
    for ck, jq in subqs:
        for r in client.search_issues(jq, max_results=limit, cache_key=ck):
            k = r.get("key")
            if not k or k in seen:
                continue
            if (((r.get("fields") or {}).get("issuetype") or {}).get("name")) == "Epic":
                continue
            seen.add(k)
            mine_raw.append(r)
    # Epic 은 담당자가 있어도 '실행 단위'가 아니라 묶음이다 — 목록에 섞으면 노이즈가 된다
    # (Epic 자체는 아래에서 그룹의 소속 표시로만 쓴다).
    def role_of(n):
        """이 일감에서 내 역할 — 화면이 '담당/등록'을 구분해 보여줄 수 있게."""
        return _role_of(n, me)

    def is_mine(n):
        """이 스코프의 '대상 일'인가 — 하위 중 무엇을 원자로 뽑을지의 기준이다.
        module 스코프에선 '그 모듈 소속'(인력 담당/보고 or 모듈 component)을 대상으로 본다."""
        if scope_kind == "module":
            if n["assigneeId"] in mod_ids or n["reporterId"] in mod_ids:
                return True
            return any(c in mod_comps for c in (n.get("components") or []))
        if scope_kind == "uassignee":
            return n["assigneeId"] == spec_user
        if scope_kind == "ureporter":
            return n["reporterId"] == spec_user
        if scope_kind == "epic":
            return n.get("epic") == epic_scope          # 그 Epic 직속(Sub-Task 는 맥락으로만)
        if scope_kind == "jql":
            return n["key"] in seen                      # JQL 에 실제로 매치된 것만 원자(맥락 제외)
        if scope_kind == "reporter":
            return n["reporterId"] == me
        if scope_kind == "both":
            return n["assigneeId"] == me or n["reporterId"] == me
        return n["assigneeId"] == me

    mine = [_node(r, today, ef) for r in mine_raw]
    for n in mine:
        n["role"] = role_of(n)
    if not mine:
        return {"user": {"id": me}, "groups": [], "epics": [], "counts": _counts([])}

    # 2) 맥락 채우기 — 부모(내가 Sub 담당인 경우)와 하위(내 Task 의 동료 Sub 포함).
    #    하위는 JQL('parent in ...')이 아니라 공통 direct-child membership 캐시로 모은다.
    #    이 화면의 JQL row에 subtasks가 이미 있으므로 상류 호출 없이 캐시를 prime하고,
    #    티켓 다이어로그·PMO_VIT·WBS가 같은 parent를 열 때 그대로 재사용한다.
    def sub_keys(raw):
        try:
            return client.direct_child_keys(raw.get("key"), parent_issue=raw)
        except Exception:
            client.miss_add()
            return []

    need_parents = sorted({n["parentKey"] for n in mine if n["isSub"] and n["parentKey"]})
    # Task 화면은 description/attachment/link를 쓰지 않는다. JQL이 이미 채운 issueL 캐시를
    # 그대로 재사용해야 하므로 Parent도 light projection으로만 보강한다.
    parent_raw = {r["key"]: r for r in client.issues_by_keys(need_parents, light=True)} \
        if need_parents else {}
    parents = {k: _node(r, today, ef) for k, r in parent_raw.items()}

    # 하위를 알아야 하는 이슈 = 내 Task 들 + 위에서 가져온 부모들
    expected_kids = {}
    for r in list(mine_raw) + list(parent_raw.values()):
        key = r.get("key")
        keys = sub_keys(r)
        if key and keys:
            expected_kids[key] = list(dict.fromkeys(keys))
    kid_keys = [key for keys in expected_kids.values() for key in keys]
    kid_of = {}                                   # 부모키 -> [자식 노드]
    if defer_children:
        # 1차 응답은 JQL 결과에 이미 실려 온 SubTask만 사용한다. 동료 하위 전체를 받는 배치는
        # Parent별 후속 API로 미뤄, 느린 그룹 하나 때문에 모든 카드가 기다리지 않게 한다.
        # 실제 parent 관계가 이미 있는 JQL row는 타입 이름/로케일과 무관하게 자식으로 재사용한다.
        # (SubTask 판정 자체는 여전히 issuetype.subtask가 원칙이지만, Jira가 parent를 준 row를
        # base에서 버리면 full 보강 뒤 카드가 다른 그룹으로 순간 이동한다.)
        child_rows = sorted(
            (r for r in mine_raw if ((r.get("fields") or {}).get("parent") or {}).get("key")),
            key=lambda row: row.get("key") or "")
    else:
        child_rows = client.issues_by_keys(sorted(set(kid_keys)), light=True) if kid_keys else []
    for r in child_rows:
        c = _node(r, today, ef)
        c["role"] = role_of(c)
        if c["parentKey"]:
            kid_of.setdefault(c["parentKey"], []).append(c)

    # 3) 그룹 구성 — 부모 Task 단위. 하위 없는 내 Task 는 자기 자신이 그룹이자 원자.
    groups = {}

    def group_of(node, has_kids):
        g = groups.get(node["key"])
        if not g:
            g = groups[node["key"]] = {
                "key": node["key"], "title": node["title"], "type": node["type"],
                "mine": is_mine(node), "role": node.get("role"),
                "assignee": node["assignee"], "assigneeId": node["assigneeId"],
                "status": node["status"], "statusCategory": node["statusCategory"],
                "reporter": node.get("reporter"), "reporterId": node.get("reporterId"),
                "voc": node.get("voc"),          # Epic 이 없어도 VoC 는 전용 Epic 처럼 묶고 색을 준다
                "epic": node["epic"], "pri": node["pri"], "priRank": node["priRank"],
                "priBand": node["priBand"], "due": node["due"], "dueDays": node["dueDays"],
                "atoms": [], "others": [], "hasSubs": has_kids,
                # 단독 = 하위가 없어 부모 헤더를 따로 그릴 필요가 없는 내 Task
                "standalone": not has_kids,
            }
        return g

    seen_atoms = set()

    def add_atom(g, node):
        if node["key"] in seen_atoms:
            return
        seen_atoms.add(node["key"])
        g["atoms"].append(node)

    for n in mine:
        if n["isSub"] and n["parentKey"]:
            p = parents.get(n["parentKey"])
            g = group_of(p, True) if p else group_of(n, False)
            add_atom(g, n)
        else:
            kids = kid_of.get(n["key"]) or []
            g = group_of(n, bool(expected_kids.get(n["key"]) or kids))
            my_kids = [c for c in kids if is_mine(c)]
            if my_kids:
                for c in my_kids:
                    add_atom(g, c)      # 하위가 있으면 하위가 실행 단위 — Task 자체는 원자가 아니다
            elif not expected_kids.get(n["key"]) and not kids:
                add_atom(g, n)          # 하위가 아예 없는 단독 Task → Task 자체가 원자(soloPanel 로 감)
            # else: 하위는 있으나 전부 남의 것/스코프 밖(예: epic 스코프의 Sub-Task 는 Epic Link 가 없어
            #   is_mine 이 안 잡힌다). 이때 **Task 자신을 하위 원자로 넣지 않는다** — 넣으면 부모 헤더와
            #   같은 티켓이 자기 그룹 본문에 하위처럼 다시 뜬다('자기 자신이 SubTask 로 보이던' 버그).
            #   실제 하위는 아래 4)에서 others 로 붙고, 프론트가 '내 하위가 없으면 others 를 대신 보여' 준다.

    # 4) 동료 하위 + 롤업 + 정렬 키
    for g in groups.values():
        kids = kid_of.get(g["key"]) or []
        expected = expected_kids.get(g["key"]) or [child["key"] for child in kids]
        loaded_keys = {child["key"] for child in kids}
        pending = bool(defer_children and any(key not in loaded_keys for key in expected))
        mine_keys = {a["key"] for a in g["atoms"]}
        g["others"] = [c for c in kids if c["key"] not in mine_keys]
        g["hasSubs"] = bool(expected or kids)
        g["standalone"] = not g["hasSubs"]
        g["childrenPending"] = pending
        g["childrenLoaded"] = not pending
        g["childrenLoadedCount"] = len(kids)
        # 롤업은 하위 전체 기준(동료 몫 포함) — 부모의 실제 진척이다.
        # 하위가 없으면 그 Task 하나의 상태가 곧 진척이라 별도 바를 그리지 않는다(pct=None).
        g["pct"] = _rollup(kids) if kids and not pending else None
        # 화면에는 퍼센트 대신 **몇 개 중 몇 개**를 적는다. 퍼센트는 SP 가중이 섞인 값이라
        # "3/7" 처럼 손으로 세어 확인할 수가 없다 — 눈으로 대조되는 숫자가 신뢰를 만든다.
        g["kidsDone"] = sum(1 for c in kids if c["statusCategory"] == "done")
        g["kidsTotal"] = len(expected) if expected else len(kids)
        g["othersDone"] = sum(1 for c in g["others"] if c["statusCategory"] == "done")
        g["atoms"].sort(key=lambda a: (a["dueDays"] if a["dueDays"] is not None else _NO_DUE,
                                       a["priRank"], a["key"]))
        g["urgency"] = min((a["dueDays"] for a in g["atoms"] if a["dueDays"] is not None),
                           default=None)
        g["priRank"] = min((a["priRank"] for a in g["atoms"]), default=2)

    out = sorted(groups.values(),
                 key=lambda g: (g["urgency"] if g["urgency"] is not None else _NO_DUE,
                                g["priRank"], g["key"]))
    out = _normalize_task_groups(out)

    # 5) Epic 메타 — 그룹이 참조하는 것만(이름을 보여주려면 제목이 필요하다)
    epic_keys = sorted({g["epic"] for g in out if g["epic"]})
    epics = []
    if defer_children:
        # 제목/상태가 없어도 카드는 Epic key로 즉시 그릴 수 있다. 직렬 badge 조회는 별도 동기화한다.
        epics = [{"key": key, "title": key, "statusCategory": "todo", "pending": True}
                 for key in epic_keys]
    else:
        epics = client.epic_metadata_many(epic_keys)

    atoms = [a for g in out for a in g["atoms"]]
    result = {"user": {"id": me, "name": (client.current_user() or {}).get("name") or me},
              "scope": scope, "openFilter": open_filter, "doneFilter": done_filter,
              "doneWindowDays": DONE_WINDOWS.get(done_filter, 7),
              "today": today.isoformat(), "groups": out, "epics": epics,
              "counts": _counts(atoms)}
    pending_groups = {g["key"]: expected_kids.get(g["key"], [])
                      for g in out if g.get("childrenPending")}
    if defer_children and create_sync and (pending_groups or epic_keys):
        sync_id = secrets.token_urlsafe(18)
        session_user = (client.current_user() or {}).get("id")
        context = {
            "me": me, "scopeKind": scope_kind, "specificUser": spec_user,
            "epic": epic_scope, "moduleIds": sorted(mod_ids),
            "moduleComponents": sorted(mod_comps), "matchedKeys": sorted(seen),
            "groupMeta": {g["key"]: {"mine": g["mine"], "role": g.get("role")}
                          for g in out},
        }
        result["syncId"] = sync_id
        result["syncPending"] = len(pending_groups)
        result["epicsPending"] = bool(epic_keys)
        result["snapshotSequence"] = 0
        client.cache.set(_sync_key(client, sync_id), {
            "sessionUser": session_user, "context": context,
            "children": pending_groups, "epics": epic_keys,
            "model": result, "hydrated": [], "snapshotSequence": 0,
        }, ASYNC_SYNC_TTL)
    return result


def _counts(atoms):
    """상단 요약 — '오늘 뭘 봐야 하나'의 첫 신호. 지남/오늘/이번주/전체·완료."""
    over = sum(1 for a in atoms if (a["dueDays"] is not None and a["dueDays"] < 0)
               and a["statusCategory"] != "done")
    today_n = sum(1 for a in atoms if a["dueDays"] == 0 and a["statusCategory"] != "done")
    week = sum(1 for a in atoms if a["dueDays"] is not None and 0 < a["dueDays"] <= 7
               and a["statusCategory"] != "done")
    return {"total": len(atoms), "overdue": over, "today": today_n, "week": week,
            "done": sum(1 for a in atoms if a["statusCategory"] == "done"),
            "noDue": sum(1 for a in atoms if a["dueDays"] is None)}


def _normalize_task_groups(groups):
    """Give every issue key one authoritative owner and refresh group-derived ordering fields."""
    by_group = {group.get("key"): group for group in groups or () if group.get("key")}
    mine_by_key = {}
    for group in groups or ():
        for atom in group.get("atoms") or ():
            if atom.get("key"):
                mine_by_key[atom["key"]] = atom

    rows = []
    for source in groups or ():
        group = dict(source)
        group["atoms"] = [atom for atom in source.get("atoms") or ()
                          if (not atom.get("parentKey")
                              or atom.get("parentKey") == group.get("key")
                              or atom.get("parentKey") not in by_group)]
        group["others"] = list(source.get("others") or ())
        if group.get("hasSubs"):
            promoted = [row for row in group["others"] if row.get("key") in mine_by_key]
            if promoted:
                promote_keys = {row.get("key") for row in promoted}
                merged = {row.get("key"): row for row in group["atoms"] if row.get("key")}
                for row in promoted:
                    merged[row["key"]] = mine_by_key[row["key"]]
                group["atoms"] = list(merged.values())
                group["others"] = [row for row in group["others"]
                                   if row.get("key") not in promote_keys]
        rows.append(group)

    claimed = set()
    for group in sorted(rows, key=lambda item: not bool(item.get("hasSubs"))):
        atoms, others = [], []
        for row in group.get("atoms") or ():
            key = row.get("key")
            if not key or key in claimed:
                continue
            claimed.add(key)
            atoms.append(row)
        for row in group.get("others") or ():
            key = row.get("key")
            if not key or key in claimed:
                continue
            claimed.add(key)
            others.append(row)
        group["atoms"], group["others"] = atoms, others

    normalized = []
    row_key = lambda row: (
        row.get("dueDays") if row.get("dueDays") is not None else _NO_DUE,
        row.get("priRank", 2), row.get("key") or "")
    for group in rows:
        if not (group.get("hasSubs") or group["atoms"] or group["others"]):
            continue
        group["atoms"] = sorted(group["atoms"], key=row_key)
        atom_keys = {row.get("key") for row in group["atoms"]}
        group["others"] = sorted(
            (row for row in group["others"] if row.get("key") not in atom_keys), key=row_key)
        children = {row.get("key"): row for row in group["atoms"] + group["others"]
                    if row.get("key") and row.get("key") != group.get("key")}
        group["kidsTotal"] = max(int(group.get("kidsTotal") or 0), len(children))
        group["kidsDone"] = sum(row.get("statusCategory") == "done" for row in children.values())
        group["othersDone"] = sum(row.get("statusCategory") == "done"
                                  for row in group["others"])
        due = [row.get("dueDays") for row in group["atoms"]
               if row.get("dueDays") is not None]
        group["urgency"] = min(due) if due else None
        if group["atoms"]:
            group["priRank"] = min(row.get("priRank", 2) for row in group["atoms"])
        normalized.append(group)
    return sorted(normalized, key=lambda item: (
        item.get("urgency") if item.get("urgency") is not None else _NO_DUE,
        item.get("priRank", 2), item.get("key") or ""))

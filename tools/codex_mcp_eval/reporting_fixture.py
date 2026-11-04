"""Independent, explicit source world for period/scope reporting comparisons.

The host oracle is computed from the authored records below, never from reporting
production code. Both candidates use the same world, dates, and transport. The
Jira820 source dates are local calendar dates; timestamp overrides serialize the
same instants in UTC so the month-boundary cases exercise timezone conversion.
"""
from __future__ import annotations

import copy
from datetime import date, datetime, time, timedelta, timezone

from fastapi import Request

from tools.codex_mcp_eval.fixture import EvaluationFixture

PERSON = "test.ui01"
OTHER_PERSON = "test.ui02"
MODULE = "TEST"
EPIC = "DL-92000"
TASK = "DL-92020"
DENIED_CHILD = "DL-92023"
KST = timezone(timedelta(hours=9))


def periods(today: date | str) -> dict:
    today = date.fromisoformat(today) if isinstance(today, str) else today
    month_end = today.replace(day=1)
    week_end = today - timedelta(days=today.weekday())
    return {"day": (today - timedelta(days=1), today),
            "week": (week_end - timedelta(days=7), week_end),
            "month": ((month_end - timedelta(days=1)).replace(day=1), month_end)}


def _utc(day, hour=12, minute=0, second=0):
    return datetime.combine(day, time(hour, minute, second), KST).astimezone(timezone.utc).isoformat()


def build_reporting_data(today: date | str) -> dict:
    today = date.fromisoformat(today) if isinstance(today, str) else today
    spans = periods(today)
    month_start, month_end = spans["month"]
    week_start, _ = spans["week"]
    day_start, _ = spans["day"]
    created = month_start - timedelta(days=35)
    issues = {}

    def add(number, summary, *, resolved=None, person=PERSON, module=MODULE,
            epic=EPIC, parent=None, kind="Task", sp=3, description="", labels=()):
        key = f"DL-{number}"
        done = resolved is not None
        issues[key] = {"key": key, "project": "DL", "type": kind, "summary": summary,
            "description": description, "module": module, "component": module,
            "assignee": person, "reporter": PERSON, "statusCategory": "done" if done else "inprogress",
            "statusName": "Resolved" if done else "In Progress", "priority": "P2-Major",
            "labels": ["reporting_fixture", *labels], "sp": sp,
            "epicKey": epic if kind != "Epic" else None, "epicName": summary if kind == "Epic" else None,
            "parentKey": parent, "created": created, "updated": resolved or day_start,
            "resolved": resolved, "due": None, "tcreated": "09:00", "tupdated": "12:00",
            "tresolved": "12:00" if done else None, "comments": [], "changelog": [],
            "worklog": [], "subtasks": [], "links": [], "remotelinks": [],
            "_timestamps": {"created": _utc(created, 9), "updated": _utc(resolved or day_start),
                            "resolutiondate": _utc(resolved) if done else None}}
        if done:
            change(key, resolved, "status", "In Progress", "Resolved", old_id="3", new_id="5")
        return key

    def change(key, day, field, old, new, *, old_id=None, new_id=None):
        issues[key]["changelog"].append({"author": PERSON, "date": day, "time": "12:00",
            "items": [{"field": field, "fieldtype": "jira", "from": old_id, "fromString": old,
                       "to": new_id, "toString": new}]})

    def comment(key, day, text, *, author=PERSON):
        rows = issues[key]["comments"]
        rows.append({"id": str(50000 + len(rows)), "author": author, "body": text, "text": text,
                     "kind": "note", "created": day, "tcreated": "13:00"})

    add(92000, "보고서 검증 신규 적재 Epic", kind="Epic", sp=None)
    add(92010, "다른 모듈 운영 Epic", kind="Epic", module="Workbench", sp=None)
    add(92001, "신규 적재 고객 테이블", resolved=month_start + timedelta(days=5),
        description="신규 적재 대상 CRM.PUBLIC.CUSTOMERS. 최초 운영 적재와 검증 완료.")
    add(92002, "신규 적재 고객·주문 통합", resolved=week_start + timedelta(days=1),
        description="CRM.PUBLIC.CUSTOMERS, CRM.PUBLIC.ORDERS 신규 적재 업무. 고객 테이블은 DL-92001과 동일 대상.")
    comment("DL-92002", week_start + timedelta(days=1),
            "CRM.PUBLIC.ORDERS 최초 적재 완료. CRM.PUBLIC.CUSTOMERS는 DL-92001 실적과 중복이므로 한 번만 집계.")
    add(92003, "일간 신규 적재", resolved=day_start,
        description="CRM.PUBLIC.DAILY_ONLY 최초 적재 완료.")
    add(92004, "신규 적재 반품 완료", resolved=week_start + timedelta(days=2), person=OTHER_PERSON,
        description="계획: CRM.PUBLIC.RETURNS 신규 적재. 범위 확정은 댓글 최종 합의를 확인.")
    # A decisive agreement beyond page one in both ascending and descending
    # order: routine notes on either side do not change the business outcome.
    for index in range(105):
        comment("DL-92004", month_start + timedelta(days=7), f"사전 검토 {index + 1}: 샘플 형식 확인, 범위 변경 없음.")
    for index in range(105):
        comment("DL-92004", week_start + timedelta(days=4), f"후속 검토 {index + 1}: 샘플 형식 확인, 범위 변경 없음.")
    comment("DL-92004", week_start + timedelta(days=3),
            "최종 범위 변경: CRM.PUBLIC.RETURNS 신규 적재를 취소하고 제외합니다. 데이터는 적재하지 않았습니다. 이 티켓은 검토 종료로 Resolved 처리했습니다.")
    add(92005, "타 모듈 신규 적재", resolved=week_start + timedelta(days=3), module="Workbench", epic="DL-92010",
        description="CRM.PUBLIC.OTHER_MODULE 운영 적재 완료. TEST 모듈 실적 아님.")
    add(92006, "월 시작 경계 신규 적재", resolved=month_start,
        description="SAP.PUBLIC.MATERIALS 신규 적재 완료. 완료 시각은 한국시간 월 첫날 00:00.")
    issues["DL-92006"]["_timestamps"]["resolutiondate"] = _utc(month_start, 0)
    add(92007, "다음 달 시작 경계", resolved=month_end, person=OTHER_PERSON,
        description="CRM.PUBLIC.NEXT_MONTH 신규 적재 완료. 이전 달 실적에 포함하지 않음.")
    issues["DL-92007"]["_timestamps"]["resolutiondate"] = _utc(month_end, 0)
    add(92008, "이전 기간 완료 후 최근 댓글", resolved=month_start - timedelta(days=1),
        description="CRM.PUBLIC.PREVIOUS_MONTH 적재는 지난 집계기간 전에 완료됨. 최근 updated는 댓글 때문.")
    issues["DL-92008"]["_timestamps"].update(
        resolutiondate=_utc(month_start - timedelta(days=1), 23, 59, 59), updated=_utc(today, 9))
    issues["DL-92008"]["updated"] = today
    comment("DL-92008", today, "과거 완료 자료를 재확인했습니다. 신규 완료 실적은 아닙니다.")
    add(92009, "신규 적재 결제 재오픈", description="CRM.PUBLIC.PAYMENTS 최초 적재 검증 실패. 재오픈 후 수정 진행 중.")
    change("DL-92009", month_start + timedelta(days=9), "status", "In Progress", "Resolved", old_id="3", new_id="5")
    change("DL-92009", month_start + timedelta(days=19), "status", "Resolved", "Reopened", old_id="5", new_id="4")
    comment("DL-92009", month_start + timedelta(days=19), "재오픈했습니다. 현재 완료 실적에서 제외하고 진행 중으로 보고해 주세요.")
    add(92011, "[완료] 제목만 완료인 진행 작업", person=OTHER_PERSON, sp=8,
        description="완료라는 제목은 초안입니다. Jira 상태는 In Progress이고 검증 미완료.")
    add(92013, "신규 적재 검토 - 기존 테이블 변경", resolved=month_start + timedelta(days=15),
        description="CRM.PUBLIC.LEGACY 기존 테이블의 컬럼 추가와 재적재만 수행. 최초 적재가 아니므로 신규 테이블 수에서 제외.")
    add(92020, "주요 신규 적재 묶음", sp=8, description="하위 작업은 완료됐지만 부모의 통합 검증은 진행 중. 하위 100%를 부모 완료로 표현하지 마세요.")
    issues[TASK]["updated"] = created
    issues[TASK]["_timestamps"]["updated"] = _utc(created)
    child_specs = [(92014, week_start + timedelta(days=1), "CRM.PUBLIC.ORDER_LINES"),
                   (92021, day_start, "CRM.PUBLIC.DAILY_CHILD"),
                   (92022, month_start + timedelta(days=14), "CRM.PUBLIC.SHIPMENTS"),
                   (92023, week_start + timedelta(days=1), "CRM.PUBLIC.ORDER_LINES"),
                   (92024, month_end, "CRM.PUBLIC.NEXT_CHILD")]
    for number, resolved, table in child_specs:
        key = add(number, "하위 신규 적재 " + table, resolved=resolved, kind="Sub-Task", parent=TASK,
                  epic=None, sp=None, description=f"{table} 최초 운영 적재 완료. 동일 테이블 다른 티켓과 중복 집계하지 않음.")
        issues[TASK]["subtasks"].append(key)
    # First 100 histories are old noise; meaningful child changes are beyond page one.
    old = issues[DENIED_CHILD]["changelog"]
    issues[DENIED_CHILD]["changelog"] = []
    for index in range(105):
        change(DENIED_CHILD, created, "priority", f"이전 검토 {index}", f"검토 {index + 1}")
    issues[DENIED_CHILD]["changelog"].extend(old)
    change(DENIED_CHILD, week_start + timedelta(days=2), "summary", "주문 라인 적재", "하위 신규 적재 CRM.PUBLIC.ORDER_LINES")
    comment(DENIED_CHILD, week_start + timedelta(days=2), "하위 검증 기록: 주문 라인 건수와 합계가 원천과 일치합니다.")
    comment(DENIED_CHILD, month_end + timedelta(days=1), "다음 기간 활동: 운영 모니터링을 확인했습니다. 지난달 활동에는 포함하지 마세요.")

    def in_scope(row, scope):
        if scope == "person":
            return row["assignee"] == PERSON
        if scope == "module":
            return row["component"] == MODULE
        if scope == "task":
            return row["key"] == TASK or row["parentKey"] == TASK
        return row["key"] == EPIC or row["epicKey"] == EPIC or row["parentKey"] == TASK

    completed = {period: {scope: sorted(key for key, row in issues.items()
        if row["type"] != "Epic" and row["statusCategory"] == "done" and row["resolved"] is not None
        and start <= row["resolved"] < end and in_scope(row, scope))
        for scope in ("person", "module", "epic", "task")}
        for period, (start, end) in spans.items()}
    # Explicit business outcomes authored alongside source text, independent of
    # the system being evaluated. Date shifting must not make the oracle stale.
    contributions = {
        "DL-92001": ["CRM.PUBLIC.CUSTOMERS"],
        "DL-92002": ["CRM.PUBLIC.CUSTOMERS", "CRM.PUBLIC.ORDERS"],
        "DL-92003": ["CRM.PUBLIC.DAILY_ONLY"],
        "DL-92004": [],  # Latest comment cancels the planned table.
        "DL-92006": ["SAP.PUBLIC.MATERIALS"],
        "DL-92007": ["CRM.PUBLIC.NEXT_MONTH"],
        "DL-92008": ["CRM.PUBLIC.PREVIOUS_MONTH"],
        "DL-92013": [],  # Existing-table modification, not a new ingestion.
        **{f"DL-{number}": [table] for number, _, table in child_specs},
    }
    semantic_sources = completed["month"]["module"]
    semantic_tables = sorted({table for key in semantic_sources for table in contributions.get(key, [])})
    truth = {"today": today.isoformat(), "timezone": "Asia/Seoul", "personId": PERSON,
        "module": MODULE, "epicKey": EPIC, "taskKey": TASK,
        "periods": {name: {"start": start.isoformat(), "end": end.isoformat()}
                    for name, (start, end) in spans.items()}, "completed": completed,
        "children": sorted(issues[TASK]["subtasks"]), "parentStatus": "In Progress",
        "descendantProgress": {"done": len(child_specs), "total": len(child_specs), "percent": 100},
        "semanticTables": semantic_tables, "semanticSources": semantic_sources,
        "excludedTables": {"CRM.PUBLIC.RETURNS": "취소·제외", "CRM.PUBLIC.LEGACY": "기존 변경·재적재",
                           "CRM.PUBLIC.PAYMENTS": "재오픈·미완료", "CRM.PUBLIC.OTHER_MODULE": "다른 모듈",
                           "CRM.PUBLIC.PREVIOUS_MONTH": "기간 이전", "CRM.PUBLIC.NEXT_MONTH": "기간 이후"},
        "lateExclusion": {"key": "DL-92004", "commentId": "50210", "commentTotal": 211},
        "historyTail": {"key": DENIED_CHILD, "total": 107, "id": "1106", "field": "summary"},
        "descendantActivity": {"key": DENIED_CHILD, "inPeriodCommentId": "50000", "outsidePeriodCommentId": "50001"},
        "reopened": "DL-92009", "deniedChild": DENIED_CHILD}
    return {"issues": issues, "oracle": truth}


class ReportingFixture(EvaluationFixture):
    """Same HTTP MCP/security/runtime as production, with authored reporting records."""
    def __enter__(self):
        super().__enter__()
        try:
            data = build_reporting_data(self.today)
            self._reporting_truth = data["oracle"]
            self.settings.search_jira_projects = ["DL"]
            self.world.issues = copy.deepcopy(data["issues"])
            self.world.attachments, self.world.confluence, self.world.activity = {}, {}, {}
            self.world._index()
            self.store.issues = copy.deepcopy(data["issues"])
            self.store.attachments, self.store.confluence, self.store.activity = {}, {}, {}
            self.store.denied_issue_keys = {DENIED_CHILD} if self.fault == "partial" else set()
            self.store.reindex()
            original = self.store.serializer.issue_fields
            def timestamp_fields(issue, base_url=""):
                fields = original(issue, base_url)
                fields.update(issue.get("_timestamps") or {})
                return fields
            self.store.serializer.issue_fields = timestamp_fields
            # jira820 currently ignores orderBy in this route. Adapt this
            # private source server, not production reads, to Jira's contract.
            def ordered_comments(key: str, request: Request, maxResults: int = 50, startAt: int = 0, orderBy: str = ""):
                rows = self.store.jira_comments(key)
                if orderBy:
                    rows.sort(key=lambda row: row["created"], reverse=orderBy.startswith("-"))
                return {"startAt": startAt, "maxResults": maxResults, "total": len(rows),
                        "comments": rows[startAt:startAt + maxResults]}
            upstream = self.provider._client.app
            upstream.add_api_route("/rest/api/2/issue/{key}/comment", ordered_comments, methods=["GET"])
            upstream.router.routes.insert(0, upstream.router.routes.pop())
            self._before = self.fingerprint()
            return self
        except BaseException:
            self.close()
            raise

    def ground_truth(self):
        return {**copy.deepcopy(self._reporting_truth), "issues": copy.deepcopy(self.store.issues),
                "users": copy.deepcopy(self.store.users)}

    def assert_conformance(self):
        before = len(self.upstream_calls)
        raw = self.provider._client
        start, end = periods(self.today)["month"]
        query = f'project = DL AND component = TEST AND statusCategory = Done AND resolved >= "{start}" AND resolved < "{end}" ORDER BY key ASC'
        expected = self._reporting_truth["completed"]["month"]["module"]
        actual, offset = [], 0
        while True:
            response = raw.get("/rest/api/2/search", params={"jql": query, "startAt": offset, "maxResults": 2})
            assert response.status_code == 200, response.text
            page = response.json()
            actual.extend(row["key"] for row in page["issues"])
            offset += len(page["issues"])
            if offset >= page["total"]:
                break
            assert page["issues"], "Reporting fixture pagination made no progress"
        wanted = [key for key in expected if key not in self.store.denied_issue_keys]
        assert actual == wanted, (actual, wanted)
        edge = raw.get("/rest/api/2/issue/DL-92006").json()["fields"]["resolutiondate"]
        assert datetime.fromisoformat(edge).astimezone(KST).date() == start
        assert "DL-92006" in actual and "DL-92007" not in actual and "DL-92008" not in actual
        end_edge = raw.get("/rest/api/2/issue/DL-92007").json()["fields"]["resolutiondate"]
        before_edge = raw.get("/rest/api/2/issue/DL-92008").json()["fields"]["resolutiondate"]
        assert datetime.fromisoformat(end_edge).astimezone(KST).date() == end
        assert datetime.fromisoformat(before_edge).astimezone(KST).date() == start - timedelta(days=1)
        for order in ("created", "-created"):
            collected, offset = [], 0
            while offset < 211:
                comments = raw.get("/rest/api/2/issue/DL-92004/comment", params={
                    "startAt": offset, "maxResults": 100, "orderBy": order}).json()
                assert comments["total"] == 211 and comments["startAt"] == offset
                collected.extend(comments["comments"])
                offset += len(comments["comments"])
                assert comments["comments"], "Comment pagination made no progress"
            assert len({row["id"] for row in collected}) == 211
            assert collected[105]["id"] == "50210" and "취소" in collected[105]["body"]
            assert not any("취소" in row["body"] for row in collected[:100])
            dates = [row["created"] for row in collected]
            assert dates == sorted(dates, reverse=order.startswith("-"))
        # Jira 8.20 histories are delivered by issue expand, not a Cloud-only
        # changelog endpoint. Check its real metadata and tail beyond page one.
        history_key = DENIED_CHILD
        if history_key not in self.store.denied_issue_keys:
            histories = raw.get(f"/rest/api/2/issue/{history_key}", params={"expand": "changelog"}).json()["changelog"]
            assert histories["startAt"] == 0 and histories["total"] == 107
            assert len(histories["histories"]) == 107
            assert histories["histories"][-1]["id"] == "1106"
            assert histories["histories"][-1]["items"][0]["fromString"] == "주문 라인 적재"
        parent = raw.get(f"/rest/api/2/issue/{TASK}").json()["fields"]
        assert sorted(row["key"] for row in parent["subtasks"]) == self._reporting_truth["children"]
        assert parent["status"]["statusCategory"]["key"] == "indeterminate"
        assert len(self.upstream_calls) == before == 0
        return {"completedMonthModule": wanted, "pagination": "complete", "timezoneBoundary": edge,
                "comments": {"total": 211, "lateExclusionId": "50210", "orders": ["created", "-created"]}, "cacheWarmed": False}

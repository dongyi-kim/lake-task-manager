"""Authored business events and independent oracles for the work-skills candidate."""
from __future__ import annotations

import copy
from datetime import date, timedelta

from tools.codex_mcp_eval.identity_fixture import build_identity_data
from tools.codex_mcp_eval.reporting_fixture import PERSON, OTHER_PERSON, build_reporting_data, periods, _utc

LABEL = "work_research_fixture"
ROOT = "DL-94010"
CHILDREN = ["DL-94011", "DL-94012"]
PLAN = {"project_key": "DL", "modules": ["TEST"], "epics": {}, "wbs": [
    {"id": "TEST-1", "module": "TEST", "name": "조사 검증", "start": "2026-01-01", "end": "2026-12-31",
     "epics": [{"key": "DL-94000", "weight": 1}]}]}


def build_work_data(today: date | str, *, source_revision: int = 1) -> dict:
    if source_revision not in {1, 2}:
        raise ValueError("Invalid controlled source revision")
    today = date.fromisoformat(today) if isinstance(today, str) else today
    start, end = periods(today)["month"]
    yesterday = today - timedelta(days=1)
    week = periods(today)["week"][0] + timedelta(days=1)
    data = build_reporting_data(today)
    users = build_identity_data(today)["users"]
    users[PERSON] = {"name": PERSON, "displayName": "조사담당 가상회사"}
    users[OTHER_PERSON] = {"name": OTHER_PERSON, "displayName": "개발담당 가상회사"}
    issues = data["issues"]

    def add(number, title, body, *, day=None, done=True, person=PERSON, kind="Task", parent=None):
        key, day = f"DL-{number}", day or start + timedelta(days=5)
        row = copy.deepcopy(issues["DL-92001"])
        row.update(key=key, summary=title, description=body, type=kind, assignee=person,
                   reporter=PERSON, labels=[LABEL], epicKey="DL-94000" if kind == "Task" else None,
                   parentKey=parent, statusCategory="done" if done else "inprogress",
                   statusName="Resolved" if done else "In Progress", resolved=day if done else None,
                   created=start - timedelta(days=3), updated=day, comments=[], changelog=[], subtasks=[],
                   epicName=title if kind == "Epic" else None)
        row["_timestamps"] = {"created": _utc(row["created"], 9), "updated": _utc(day),
                              "resolutiondate": _utc(day) if done else None}
        issues[key] = row
        return row

    def comment(number, identifier, day, body, author=PERSON):
        issues[f"DL-{number}"]["comments"].append({"id": str(identifier), "author": author,
            "body": body, "text": body, "kind": "note", "created": day, "tcreated": "13:00"})

    add(94000, "업무 조사 Epic", "TEST-1 조사 검증 WBS에 연결된 합성 Epic", kind="Epic", done=False)
    add(94001, "고객 신규 적재", "prod.dw.customers 신규 적재. 실행 사건 LOAD-1 검증 완료.")
    comment(94001, 61001, start + timedelta(days=5), "LOAD-1: prod.dw.customers 최초 적재 완료. 개발담당이 실행하고 조사담당이 검증.", OTHER_PERSON)
    add(94002, "주문 신규 적재", "prod.dw.orders 신규 적재 LOAD-2 완료.", day=start + timedelta(days=6))["labels"].append("VoC")
    add(94003, "고객 적재 개선 1", "prod.dw.customers 컬럼 추가 CHANGE-1 완료.", day=start + timedelta(days=8))
    add(94004, "고객 적재 개선 2", "prod.dw.customers 인덱스 개선 CHANGE-2 완료. CHANGE-1과 별개 사건.", day=start + timedelta(days=9))
    add(94005, "고객 적재 중단", "prod.dw.customers 운영 적재 STOP-1 중단 완료. 최초 적재 실적은 실제 발생했음.", day=start + timedelta(days=10))
    add(94006, "신규 적재 검토 종료", "prod.dw.cancelled 신규 적재 계획. 검토 종료로 Resolved 처리.")
    # An exclusion beyond the preview and an oversized source record exercise both paging contracts.
    for index in range(25):
        comment(94006, 62000 + index, start + timedelta(days=6), f"형식 검토 {index}; 실행 확정 아님.")
    comment(94006, 62100, start + timedelta(days=7), "취소 결정. prod.dw.cancelled는 실제 적재하지 않았음. " + "검토 기록 " * 1600)
    for index in range(25):
        comment(94006, 62200 + index, start + timedelta(days=8), f"종료 후 보관 점검 {index}; 실행 추가 없음.")
    add(94007, "고객 적재 공동 검증", "DL-94001의 LOAD-1 동일 사건 재보고. prod.dw.customers 신규 적재를 중복 집계하지 않음.", person=OTHER_PERSON)
    comment(94007, 61007, start + timedelta(days=5), "LOAD-1 공동 작업: 조사담당 검증, 개발담당 실행. 동일 산출물·사건.", OTHER_PERSON)
    add(94008, "일간 장애 점검", "장애 점검 진행 중. 아직 해결 완료 아님.", day=yesterday, done=False)
    comment(94008, 61008, yesterday, "장애 원인: 배치 큐 대기. 우회 설정 적용 후 검증 진행 중.")
    comment(94008, 61009, today, "오늘 활동: 후속 검증을 시작함. 완료되지 않음.")
    add(94009, "활동 없는 할당", "현재 진행 상태만 확인됨. 오늘 수행·댓글·완료 활동 없음.", day=today - timedelta(days=20), done=False)
    add(94010, "통합 검증 묶음", "부모 통합 검증은 진행 중. 담당·보고와 개발 기여는 댓글 구분 참조.", done=False)
    comment(94010, 61010, week, "개발담당이 코드 구현을 수행. 조사담당은 보고·검수 담당.", OTHER_PERSON)
    add(94011, "하위 적재 검증", "하위 검증 완료", kind="Sub-Task", parent=ROOT)
    add(94012, "하위 성능 검증", "하위 성능 검증 진행 중", kind="Sub-Task", parent=ROOT, done=False)
    issues[ROOT]["subtasks"] = list(CHILDREN)
    add(94013, "수행 후 담당 이전", "조사담당이 수행했으나 현재 담당자는 개발담당으로 변경됨.", day=week, person=OTHER_PERSON)
    comment(94013, 61013, week, "조사담당(test.ui01)이 실제 매핑 검증을 완료. 현재 담당자 변경 후 인수인계.")
    add(94014, "설계 검토 참여", "개발 기여자는 아직 확인되지 않음.", done=False)
    comment(94014, 61014, week, "조사담당은 설계 리뷰만 수행. 코드 구현 참여를 의미하지 않음.")
    add(94015, "고객 테이블 이름 변경", "prod.dw.customer_v1을 prod.dw.customers로 rename. 같은 테이블이라는 운영 변경 기록 RENAME-1 확인.")
    add(94016, "테스트 환경 고객", "test.dw.customers는 prod.dw.customers와 다른 환경의 별개 테이블.")
    add(94017, "기간 밖 적재", "prod.dw.outside 적재 완료. 집계 종료 시각에 실행했으므로 이전 월 제외.", day=end)
    if source_revision == 2:
        issues["DL-94002"]["description"] = "prod.dw.orders 적재 완료 보고는 오류. 실행 전 취소했으므로 신규 실적 제외."
        comment(94002, 61999, today, "정정: LOAD-2는 실행 전 취소. prod.dw.orders 실제 적재 없음.")
        issues["DL-94002"]["_timestamps"]["updated"] = _utc(today)
        issues["DL-94017"]["labels"] = []
        add(94018, "신규 고객 인수인계", "prod.dw.customers LOAD-1 동일 사건 인수인계. 새로운 적재 사건은 아님.")

    documents = {PERSON: [
        {"title": "운영 결정", "space": "DL", "date": start + timedelta(days=3),
         "body": "prod.dw.customers 운영 유지 계획. 실제 실행·중단은 DL-94001, DL-94005 기록을 대조한다."},
        {"title": "주간보고", "space": "DL", "date": week,
         "body": "<table><tr><th>업무</th><th>상태</th></tr><tr><td>DL-94010</td><td>진행</td></tr></table>"},
        {"title": "과거보고없음", "space": "DL", "date": today,
         "body": "현재 보고만 보관. 지난주 발행본은 없어서 당시 문구·상태 재현 불가."},
        {"title": "일간 양식", "space": "DL", "date": yesterday,
         "body": "<table><tr><th>업무</th><th>실적</th><th>장애</th><th>다음 작업</th></tr></table>"},
    ]}

    def expected(keys, total=None, *, required=(), documents=(), progress=None, operations=False, uncertain=False,
                 statuses=None):
        return {"keys": sorted(keys), "total": total, "requiredEvidence": list(required),
                "documents": list(documents), "progress": progress, "operations": operations,
                "mustDiscloseUncertainty": uncertain, "requiredStatuses": dict(statuses or {})}

    req = lambda key, text: {"key": f"DL-{key}", "text": text}
    cases = {}
    def pair(family, normal, boundary):
        cases[f"WORK-R{family:02d}-normal"] = normal
        cases[f"WORK-R{family:02d}-boundary"] = boundary
    history = expected(["DL-94001", "DL-94003", "DL-94005"], 3,
                       required=[req(94001, "LOAD-1"), req(94003, "CHANGE-1"), req(94005, "STOP-1")])
    pair(1, history, {**copy.deepcopy(history), "documents": ["운영 결정"]})
    new = expected(["DL-94001", "DL-94002", "DL-94007"], 2,
                   required=[req(94001, "prod.dw.customers"), req(94002, "prod.dw.orders"), req(94007, "LOAD-1")])
    pair(2, new, {**copy.deepcopy(new), "keys": sorted([*new["keys"], "DL-94006", "DL-94017"]),
                 "requiredEvidence": [*new["requiredEvidence"], req(94006, "실제 적재하지 않았음"), req(94017, "이전 월 제외")]})
    pair(3, expected(["DL-94008"], 1, required=[req(94008, "오늘 활동")]),
         expected(["DL-94009"], 0, required=[req(94009, "오늘 수행·댓글·완료 활동 없음")]))
    current_statuses = {ROOT: "inprogress", CHILDREN[0]: "done", CHILDREN[1]: "inprogress"}
    pair(4, expected([ROOT, *CHILDREN], 3, statuses=current_statuses,
                     progress={"done": 1, "total": 2, "percent": 50}),
         expected([ROOT, CHILDREN[0]], None, uncertain=True))
    pair(5, expected(["DL-94013"], 1, required=[req(94013, "실제 매핑 검증을 완료")]),
         expected([], None, uncertain=True))
    pair(6, expected(["DL-94001", "DL-94007"], 1, required=[req(94007, "동일 산출물·사건")]),
         expected(["DL-94014"], None, required=[req(94014, "코드 구현 참여를 의미하지 않음")], uncertain=True))
    operation_keys = [f"DL-{n}" for n in (94001, 94002, 94003, 94004, 94005, 94007)]
    ops = expected(operation_keys, 2, required=[req(n, event) for n, event in
        ((94001, "LOAD-1"), (94002, "LOAD-2"), (94003, "CHANGE-1"), (94004, "CHANGE-2"), (94005, "STOP-1"))], operations=True)
    pair(7, ops, {**copy.deepcopy(ops), "keys": sorted([*operation_keys, "DL-94006"]),
                  "requiredEvidence": [*ops["requiredEvidence"], req(94006, "실제 적재하지 않았음")]})
    pair(8, expected([ROOT], 1, required=[req(94010, "개발담당이 코드 구현을 수행")]),
         expected(["DL-94014"], None, required=[req(94014, "리뷰만 수행")], uncertain=True))
    pair(9, expected(["DL-94015"], 1, required=[req(94015, "RENAME-1")]),
         expected(["DL-94015", "DL-94016"], 2, required=[req(94016, "별개 테이블")]))
    pair(10, expected([ROOT, *CHILDREN], 3, statuses=current_statuses, documents=["주간보고"]),
         expected([], None, documents=["과거보고없음"], uncertain=True))
    pair(11, expected(["DL-94008"], 1, required=[req(94008, "우회 설정")], documents=["일간 양식"]),
         expected([ROOT, CHILDREN[0]], None, documents=["일간 양식"], uncertain=True))
    oracle = {"today": today.isoformat(), "period": {"start": start.isoformat(), "end": end.isoformat()},
        "cases": cases, "unionTables": 2, "operations": [
            {"category": "new", "tickets": 3, "voc": 1, "uniqueTables": 2, "workEvents": 2},
            {"category": "improve", "tickets": 2, "voc": 0, "uniqueTables": 1, "workEvents": 2},
            {"category": "stop", "tickets": 1, "voc": 0, "uniqueTables": 1, "workEvents": 1}]}
    oracle["sourceRevision"] = source_revision
    oracle["ambiguousPersonIds"] = sorted(uid for uid, user in users.items()
                                          if user["displayName"].startswith("정민수 "))
    if source_revision == 2:
        changed = oracle["cases"]["WORK-R02-boundary"]
        changed.update(keys=["DL-94001", "DL-94002", "DL-94006", "DL-94007", "DL-94018"], total=1)
        changed["requiredEvidence"] = [req(94001, "prod.dw.customers"), req(94002, "실행 전 취소"),
                                      req(94006, "실제 적재하지 않았음"), req(94018, "동일 사건")]
    return {"issues": issues, "users": users, "documents": documents, "oracle": oracle}

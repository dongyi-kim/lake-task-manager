"""Current, additive ETL sample work for the ordinary mock/local Jira world.

These are canonical Jira records, shared by in-process mock and HTTP local mode.
Dates move with the world's day; existing WBS, roster and battery fixtures stay
intact. DL-10000..10209 is reserved for this deterministic development workload.
"""
from datetime import timedelta


LABEL = "mock-rich-tracking"
_STATUSES = {"1": "Open", "3": "In Progress", "4": "Reopened", "5": "Resolved", "6": "Closed"}
_TOPICS = [
    ("CDC 수집", "주문 변경 이벤트 중복 제거"),
    ("배치 적재", "매출 집계 증분 적재 전환"),
    ("스키마 관리", "고객 테이블 스키마 변경 감지"),
    ("장애 복구", "Kafka 오프셋 복구 절차 자동화"),
    ("성능 개선", "대용량 파일 병렬 적재 튜닝"),
    ("데이터 검증", "정산 원천·목적지 건수 대사"),
    ("보안 처리", "개인정보 마스킹 규칙 적용"),
    ("운영 자동화", "실패 파티션 선택 재처리"),
    ("배포 검증", "커넥터 무중단 배포 검증"),
    ("품질 관리", "수집 지연 임계값 알림 개선"),
]
_VOC = [
    ("수집 지연", "새벽 주문 데이터 반영 지연 확인"),
    ("적재 실패", "판매 이력 파티션 재적재 요청"),
    ("데이터 정합성", "정산 대시보드와 원천 건수 차이 조사"),
    ("사용 문의", "수집 작업 상태별 대응 방법 안내"),
    ("스키마 변경", "신규 고객 속성 컬럼 추가 요청"),
    ("권한 요청", "운영 계정 적재 경로 접근 권한 확인"),
    ("성능 개선", "월말 대량 적재 처리 시간 단축 요청"),
    ("알림 개선", "재처리 완료 알림 수신 대상 변경"),
    ("중복 데이터", "이벤트 재전송 후 중복 레코드 확인"),
    ("운영 문의", "휴일 배치 실행 일정 확인"),
    ("오류 안내", "커넥터 인증 만료 오류 설명 보완"),
    ("재처리 요청", "지연 도착 데이터 선택 재처리 요청"),
]


def add_rich_tracking_workload(world):
    """Add 30 Tasks, 60 Sub-Tasks and 120 VoC without changing configuration.

    Non-ETL worlds are intentionally unaffected. All new activity ends before
    today so an early-morning collection cannot encounter future sample events.
    """
    members = world.people.get("ETL") or []
    epics = list(dict.fromkeys(e["key"] for w in world.plan["wbs"] if w["module"] == "ETL"
                              for e in w["epics"] if e["key"] in world.issues))
    if not members or not epics:
        return
    today = world.today
    yesterday = today - timedelta(days=1)
    next_number = 10000

    def make(itype, summary, index, created, *, epic=None, parent=None, voc=False):
        nonlocal next_number
        key = f"{world.project}-{next_number}"
        next_number += 1
        if key in world.issues:
            raise ValueError(f"Rich Tracking sample key already exists: {key}")
        assignee = members[index % len(members)]
        row = {
            "key": key, "project": world.project, "type": itype, "summary": summary,
            "description": _description(summary, index), "module": "ETL",
            "component": "사용자 VoC" if voc else "ETL", "assignee": assignee,
            "reporter": members[(index + 1) % len(members)], "statusCategory": "todo", "statusName": "Open",
            "labels": [LABEL, "etl-operations" if voc else "etl-delivery"],
            "sp": None if itype == "Sub-Task" else [2, 3, 5, 8][index % 4],
            "epicKey": epic, "epicName": None, "parentKey": parent,
            "created": created, "updated": created, "resolved": None,
            "due": created + timedelta(days=7 if voc else 35),
            "tcreated": "00:30", "tupdated": "00:30", "tresolved": "00:30",
            "comments": [], "worklog": [], "subtasks": [], "changelog": [], "links": [], "remotelinks": [],
        }
        world.issues[key] = row
        return row

    def change(row, day, before, after, hour=1):
        prior_resolution = row["resolved"], row["tresolved"]
        row["changelog"].append({"author": row["assignee"], "date": day, "time": f"{hour:02d}:00",
            "items": [{"field": "status", "fieldtype": "jira", "from": before, "to": after,
                       "fromString": _STATUSES[before], "toString": _STATUSES[after]}]})
        row["statusName"] = _STATUSES[after]
        row["statusCategory"] = "done" if after in {"5", "6"} else "inprogress" if after == "3" else "todo"
        row["resolved"] = day if after in {"5", "6"} else None
        row["tresolved"] = f"{hour:02d}:00"
        if after == "6" and before == "5":
            # Closing a resolved ticket is bookkeeping, not another completion.
            row["resolved"], row["tresolved"] = prior_resolution

    def note(row, day, index, sequence=0):
        body = _comment(row["summary"], index, sequence)
        row["comments"].append({"author": members[(index + sequence) % len(members)],
            "kind": "note", "text": body, "body": body, "created": day,
            "tcreated": f"{5 + sequence % 5:02d}:{sequence % 6 * 10:02d}"})

    def finish(row):
        row["changelog"].sort(key=lambda h: (h["date"], h["time"]))
        row["comments"].sort(key=lambda c: (c["created"], c["tcreated"]))
        stamps = [(row["created"], row["tcreated"])]
        stamps += [(h["date"], h["time"]) for h in row["changelog"]]
        stamps += [(c["created"], c["tcreated"]) for c in row["comments"]]
        row["updated"], row["tupdated"] = max(stamps)
        if row["statusCategory"] != "todo":
            row["worklog"] = [{"author": row["assignee"], "date": row["updated"], "seconds": 3600}]

    def delivery_history(row, index):
        mode = index % 6
        if mode == 0:
            change(row, today - timedelta(days=6), "1", "3")
            change(row, today - timedelta(days=4), "3", "5")
            change(row, today - timedelta(days=3), "5", "6")
        elif mode == 1:
            change(row, row["created"] + timedelta(days=2), "1", "3")
            change(row, today - timedelta(days=5), "3", "5")
            change(row, today - timedelta(days=3), "5", "4")
            change(row, today - timedelta(days=2), "4", "3")
        elif mode == 2:
            # A long-lived, previously untouched request only starts this week.
            change(row, today - timedelta(days=4), "1", "3")
        elif mode == 3:
            change(row, today - timedelta(days=18), "1", "3")
        elif mode == 4:
            day = today - timedelta(days=3)
            for hour, before, after in [(1, "1", "3"), (2, "3", "5"), (3, "5", "4"), (4, "4", "3")]:
                change(row, day, before, after, hour)
            change(row, today - timedelta(days=2), "3", "5")
            change(row, yesterday, "5", "6")
        end = today - timedelta(days=10) if mode == 3 else yesterday
        for sequence, day in enumerate([row["created"], max(row["created"], end - timedelta(days=2)), end]):
            note(row, day, index, sequence)
        if mode != 3:
            row["changelog"].append({"author": row["reporter"], "date": end, "time": "04:30",
                "items": [{"field": "assignee", "fieldtype": "jira", "from": row["reporter"],
                           "fromString": world._dn(row["reporter"]), "to": row["assignee"],
                           "toString": world._dn(row["assignee"])}]})
        finish(row)

    for index in range(30):
        category, topic = _TOPICS[index // 3]
        stage = ["설계", "구현", "검증"][index % 3]
        age = 75 if index % 6 == 2 else 3 if index % 6 == 5 else 28
        task = make("Task", f"[ETL 개발 - {category}] {topic} · {stage}", index,
                    today - timedelta(days=age), epic=epics[index % len(epics)])
        delivery_history(task, index)
        # One realistic long conversation and one history beyond a default Jira
        # page, so actual development collection exercises independent sources.
        if index == 0:
            for sequence in range(72):
                note(task, today - timedelta(days=6 - sequence % 6), index, sequence + 3)
        if index == 1:
            for sequence in range(60):
                task["changelog"].append({"author": members[sequence % len(members)],
                    "date": today - timedelta(days=6 - sequence % 6), "time": f"00:{sequence % 60:02d}",
                    "items": [{"field": "description", "fieldtype": "jira", "from": None, "to": None,
                               "fromString": f"검증 메모 {sequence}", "toString": f"검증 메모 {sequence + 1}"}]})
        finish(task)
        for part in range(2):
            child_index = index * 2 + part
            child_age = 75 if child_index % 6 == 2 else 3 if child_index % 6 == 5 else 28
            # The parent's lifetime always contains both Sub-Task creations.
            task["created"] = min(task["created"], today - timedelta(days=child_age))
            sub = make("Sub-Task", f"[ETL 개발 - {category}] {topic} · {['원천·목적지 대사', '운영 인수 점검'][part]}",
                       child_index, today - timedelta(days=child_age), parent=task["key"])
            task["subtasks"].append(sub["key"])
            delivery_history(sub, child_index)

    for index in range(120):
        category, title = _VOC[index % len(_VOC)]
        age = 1 + index // 5
        created = today - timedelta(days=age)
        row = make("Bug" if index % 4 == 0 else "Task",
                   f"[사용자 VoC - {category}] {title} · 접수 {index + 1:03d}", index, created,
                   voc=True, epic=epics[index % len(epics)] if index % 5 == 0 else None)
        mode = index % 6
        if mode != 5:
            change(row, created, "1", "3")
        if mode in {0, 1, 4}:
            change(row, min(created + timedelta(days=1), yesterday), "3", "5", 2)
        if mode == 0:
            change(row, min(created + timedelta(days=2), yesterday), "5", "6", 3)
        elif mode == 1:
            change(row, min(created + timedelta(days=2), yesterday), "5", "4", 3)
            change(row, min(created + timedelta(days=3), yesterday), "4", "3", 4)
        end = min(created + timedelta(days=3), yesterday)
        note(row, created, index)
        if index % 4:
            note(row, end, index, 1)
        finish(row)


def _description(summary, index):
    if index % 5 == 0:
        return (f"<h2>{summary}</h2><p>운영 요청을 재현하고 원천·목적지 데이터의 처리 결과를 비교한다.</p>"
                "<table><thead><tr><th>점검 항목</th><th>확인 기준</th></tr></thead><tbody>"
                "<tr><td>중복 이벤트</td><td>업무 키와 변경 순서가 일치함</td></tr>"
                "<tr><td>복구 검증</td><td>재처리 후 누락·중복이 없음</td></tr></tbody></table>"
                "<h3>검증 쿼리</h3><pre><code>SELECT business_key, COUNT(*) FROM target "
                "GROUP BY business_key HAVING COUNT(*) &gt; 1;</code></pre>"
                "<p>개발 샘플 데이터다. 증빙 로그와 실행 결과를 댓글에 남긴 뒤 운영 담당자에게 인계한다.</p>")
    return (f"h2. {summary}\n\n운영 요청의 원인을 재현하고 배포 전후 결과를 비교한다.\n\n"
            "h3. 완료 기준\n* 원천·목적지 데이터 대사\n* 재처리 시 누락·중복 점검\n* 운영 인수 및 복구 절차 확인\n\n"
            "||점검 항목||검증 방법||담당 범위||\n|정합성|업무 키별 건수·금액 비교|ETL 개발|\n"
            "|복구|체크포인트부터 재실행|ETL 운영|\n\n{code:language=sql}\n"
            "SELECT partition_date, COUNT(*) FROM target\nGROUP BY partition_date;\n{code}\n\n"
            "개발 샘플 데이터다. 실제 고객 정보와 운영 접속 정보는 포함하지 않는다.")


def _comment(summary, index, sequence):
    messages = [
        "원천 로그와 수집 체크포인트를 비교했습니다. 중복 이벤트는 업무 키와 변경 순서를 함께 확인하겠습니다.",
        "개발 환경에서 재현했습니다. 정상 경로와 재시도 경로를 나눠 검증하고 결과를 표에 정리했습니다.",
        "정합성 대사를 마쳤습니다. 지연 도착 데이터는 다음 실행에서 반영되며, 재처리 전후 건수를 다시 확인하겠습니다.",
        "운영 인수 점검에서 예외 경로를 추가로 발견했습니다. 복구 절차를 보완한 뒤 재검증이 필요합니다.",
        "리뷰 의견을 반영했습니다. 설정 변경과 코드 변경의 배포 순서, 실패 시 되돌리는 절차를 함께 기록했습니다.",
        "다음 담당자에게 실행 로그와 검증 쿼리를 전달했습니다. 같은 증상이 반복되면 체크포인트와 원천 도착 시간을 비교해 주세요.",
    ]
    return (f"h3. 검증 기록 {sequence + 1}\n{summary}\n\n{messages[(index + sequence) % len(messages)]}\n\n"
            "||구간||확인 내용||결과||\n|수집|원천 도착 시각·체크포인트 비교|확인 완료|\n"
            "|적재|업무 키 중복·파티션 누락 점검|재실행 후 재확인|\n\n"
            "{code:language=sql}\nSELECT COUNT(*) FROM target WHERE partition_date = :run_date;\n{code}\n\n"
            "후속 확인: 배치 종료 후 대사 결과를 이 댓글 스레드에 추가하고 운영 인수 항목과 함께 검토합니다.")

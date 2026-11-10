"""Stable year-long business corpus, with a private ledger and public-only world."""
from __future__ import annotations

from copy import deepcopy
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import random
from typing import Any

from .events import Event, stamp
from .oracle import EventOracle
from .projection import comment_source, project

KST = timezone(timedelta(hours=9))
VERSION = "etl-year-world-v5"
DEVELOPMENT_LIFECYCLES = {
    "dormant_recent_start": "DL-21010", "comment_verified_partial": "DL-21012",
    "insufficient_comments": "DL-21013", "long_running_incremental": "DL-21014",
    "dependency_wait": "DL-21015", "reopened_rework": "DL-21017",
    "handoff": "DL-21018", "administrative_update": "DL-21019",
}


@dataclass(frozen=True)
class YearWorldSpec:
    seed: str = "ltm-etl-year-v1"
    start: datetime = datetime(2025, 11, 1, tzinfo=KST)
    end: datetime = datetime(2026, 11, 1, tzinfo=KST)
    observed_until: datetime = datetime(2026, 11, 10, 23, 59, 59, tzinfo=KST)

    def __post_init__(self):
        for value in (self.start, self.end, self.observed_until):
            stamp(value)
        if not self.seed or self.start.day != 1 or self.start.hour or self.start.minute or self.start.second:
            raise ValueError("A nonempty seed and a month-start interval are required")
        if len(self.months) != 12 or self.months[-1][1] != self.end or self.observed_until < self.end:
            raise ValueError("The world requires twelve whole months and an observation tail")

    @property
    def months(self):
        spans, current = [], self.start
        while current < self.end:
            following = current.replace(year=current.year + (current.month == 12), month=current.month % 12 + 1)
            spans.append((current, following))
            current = following
        return tuple(spans)


@dataclass
class EtlWorldBundle:
    spec: YearWorldSpec
    world: Any
    oracle: EventOracle
    manifest: dict
    public_sources: dict
    documents: dict = field(repr=False)

    def public_source(self, source_id):
        return deepcopy(self.public_sources[source_id])

    def install_confluence_revisions(self, state):
        """Seed immutable versions before evaluation; no HTTP writes or live-store changes."""
        state.seed()
        with state.lock:
            for page in self.documents.values():
                cid = str(page["id"])
                original = state.pages[cid]
                for revision in page["revisions"]:
                    row = deepcopy(original)
                    row["body"] = {"storage": {"value": revision["body"], "representation": "storage"}}
                    row["version"] = {"number": revision["version"], "when": revision["published_at"],
                                      "by": {"username": "etl.user00"}}
                    row["history"] = {"createdDate": page["revisions"][0]["published_at"],
                                      "createdBy": {"username": "etl.user00"}}
                    state.versions[cid][revision["version"]] = deepcopy(row)
                    state.pages[cid] = row
        return {name: str(page["id"]) for name, page in self.documents.items()}


class _Author:
    def __init__(self, spec):
        self.spec, self.events, self._identities = spec, [], set()

    def rng(self, identity):
        digest = hashlib.sha256((self.spec.seed + "|" + identity).encode()).digest()
        return random.Random(int.from_bytes(digest, "big"))

    def emit(self, identity, key, stream, kind, occurred, payload, *, recorded=None, source=None):
        if identity in self._identities:
            raise ValueError("Duplicate event identity: " + identity)
        self._identities.add(identity)
        recorded = recorded or occurred
        if kind == "issue":
            source = f"jira:{key}:description"
        elif source is None:
            source = comment_source(key, identity) if payload.get("text") else None
        self.events.append(Event(identity, key, stream, kind, occurred, recorded, deepcopy(payload),
                                 (source,) if source else ()))

    def issue(self, key, stream, start, summary, description, *, kind="Task", sp=0, epic=None,
              parent=None, module="ETL", active_until=None, targets=(), operation=None, links=(),
              status="In Progress", active_intervals=None):
        rng = self.rng(key)
        context = _report_task_context(key, f"etl.user{rng.randrange(8):02d}")
        if context:
            description += (f"\n개발 범위: {' / '.join(context['scopes'])}. 산출물: {', '.join(context['deliverables'])}. "
                f"모듈 {context['module']}, {context['environment']} 환경, 릴리즈 {context['release']}. "
                f"검토 참여: {', '.join(context['participants'])}. 적용 기간 2026-10-01~2026-10-31.")
        rng = self.rng(key)
        self.emit(key + ":created", key, stream, "issue", start, {
            "summary": summary, "description": description, "type": kind, "sp": sp,
            "epic": epic, "parent": parent, "module": module, "assignee": f"etl.user{rng.randrange(8):02d}",
            "active_until": active_until or self.spec.end, "targets": list(targets), "operation": operation,
            "initial_status": status, "active_intervals": active_intervals,
            "report_context": context,
            "labels": ["ETL"] if module == "ETL" else [module], "links": list(links)})

    def field(self, key, stream, when, name, old, new, suffix=""):
        self.emit(f"{key}:field:{name}:{stamp(when)}:{suffix}", key, stream, "field", when,
                  {"field": name, "old": old, "value": new, "text": f"변경 승인: {name} {old} → {new}."})

    def execution(self, key, when, operation, tables, *, ref=None, recorded=None, edges_add=(), edges_remove=(), text=None):
        ref = ref or f"RUN-{key[3:]}"
        self.emit(key + ":run:" + ref, key, "operations", "execution", when, {
            "execution_ref": ref, "operation": operation, "tables": list(tables),
            "edges_add": list(edges_add), "edges_remove": list(edges_remove),
            "text": text or f"실행번호 {ref}: {operation} 작업을 수행하고 결과를 검증했습니다. 대상: {', '.join(tables)}. "
                              f"연결 추가 {list(edges_add)}, 연결 해제 {list(edges_remove)}."}, recorded=recorded)

    def document(self, identity, when, text, *, version=1, kind="publication", extra=None):
        page_id = str(700000000000 + int(hashlib.sha256(identity.encode()).hexdigest()[:9], 16))
        self.emit(f"doc:{identity}:v{version}", "", "development", kind,
                  (extra or {}).get("period_start", when) if kind == "development_report" else when, {
            "document": identity, "page_id": page_id, "title": identity, "version": version, "text": text,
            **(extra or {})}, recorded=when, source=f"confluence:{page_id}:version:{version}")


def _report_task_context(key, participant):
    profiles = {
        "DL-21740": (["복구 위치 보존"], ["재시작 토큰 저장"]),
        "DL-21742": (["이벤트 중복 차단"], ["멱등 처리 키"]),
        "DL-21743": (["원천 계약 지문", "스키마 변화 표식"], ["계약 지문 비교기", "추가 합계 검증"]),
        "DL-21744": (["시간창 역전 처리"], ["역순 입력 정렬", "역순 재시도 시험", "시간창 역전 처리 인수", "극단 지연 시나리오"]),
        "DL-21745": (["배치 역전 보정"], ["지연 구간 보정"]),
        "DL-21747": (["배치 역전 보정"], ["지연 구간 보정"]),
        "DL-21748": (["다중 원천 통합"], ["다중 원천 통합 검증"]),
        "DL-21749": (["분산 체크포인트 교차검증"], ["경계 조건 검증"]),
        "DL-50003": (["원천 계약 지문", "스키마 변화 표식"], ["계약 지문 비교기"]),
    }
    if key not in profiles:
        return None
    scopes, deliverables = profiles[key]
    return {"scopes": scopes, "deliverables": deliverables,
            "module": "Storage" if key == "DL-50003" else "ETL",
            "environment": "test" if key == "DL-50003" else "prod", "release": "R17",
            "participants": ["etl.user07"] if key in {"DL-21745", "DL-21747"} else [participant]}


def _development_reports(a):
    def at(day, hour=12, *, november=False):
        return a.spec.months[11][0] + timedelta(days=day-1 + (31 if november else 0), hours=hour)
    participants = [f"etl.user{i:02d}" for i in range(8)]
    context = {"module": "ETL", "environment": "prod", "release": "R17", "participants": participants}
    def claim(deliverable, stage, day=None, *, artifact=None, outcome="accepted", november=False,
              period_bound=True):
        result = {"deliverable": deliverable, "stage": stage,
                  "occurred_at": at(day, 10, november=november) if day else None,
                  "artifact": artifact, "outcome": outcome}
        if not period_bound:
            result["period_bound"] = False
        return result
    def item(identifier, scopes, text, *claims, **metadata):
        return {"item_id": identifier, "context": {"scopes": scopes}, "text": text,
                "claims": list(claims), **metadata}
    def report(identity, title, publication, left, right, items, *, version=1, page_context=None):
        page_context = page_context or context
        area = "운영" if page_context["environment"] == "prod" else "검증"
        body = (f"<h1>{title}</h1><p>보고 기간 {left.date()}~{(right-timedelta(days=1)).date()}. "
                f"R17 {area} 개발 회의. 참석 {', '.join(participants)}.</p>" +
                "".join(f"<h2>{row['item_id'] if row['item_id'].isdigit() else str(i+1)}. 개발 경과</h2><p>{row['text']}</p>" for i, row in enumerate(items)))
        a.document(identity, publication, body, version=version, kind="development_report", extra={
            "title": title, "period_start": left, "period_end": right, "report_context": page_context,
            "items": items, "ancestors": [page_context["module"] + " 개발", area + " 환경 / R17"]})
    token = claim("재시작 토큰 저장", "test_acceptance", 15, artifact="R17 주문 복구 패치")
    dedup = claim("멱등 처리 키", "test_acceptance", 15, artifact="R17 주문 복구 패치")
    report("etl-dev-daily-2026-10-15", "ETL 개발 일간보고 2026-10-15", at(16), at(15, 0), at(16, 0), [
        item("linked-restart", ["복구 위치 보존"], '<a href="http://127.0.0.1:8080/browse/DL-21740">복구 위치 보존</a>: 10월 15일 R17 주문 복구 패치의 재시작 토큰 저장 시험을 검수자가 수용했다.', token, jira_keys=["DL-21740"]),
        item("source-fingerprint", ["스키마 변화 표식"], "스키마 변화 표식의 계약 지문 비교기 구현을 10월 15일 마쳤다. 검수는 주간 회의로 넘겼다.", claim("계약 지문 비교기", "implementation", 15)),
        item("order-recovery", ["복구 위치 보존", "이벤트 중복 차단"], "복구 위치 보존과 이벤트 중복 차단을 묶은 R17 주문 복구 패치를 10월 15일 검수했다. 재시작 토큰 저장과 멱등 처리 키 시험을 수용했다.", token, dedup),
        item("reverse-implementation", ["시간창 역전 처리"], "시간창 역전 처리: 10월 15일 역순 입력 정렬 구현을 마쳤다. 극단 지연 시나리오는 다음 릴리즈 검토 안건이다.", claim("역순 입력 정렬", "implementation", 15), claim("극단 지연 시나리오", "plan", outcome=None)),
    ])
    report("etl-dev-weekly-2026-10-26", "ETL 개발 주간보고 2026-10-26", at(27), at(19, 0), at(26, 0), [
        item("order-recovery", ["복구 위치 보존", "이벤트 중복 차단"], "전주 10월 15일에 수용한 R17 주문 복구 패치의 재시작 토큰 저장과 멱등 처리 키 시험 결과를 이관했다. 복구 위치 보존·이벤트 중복 차단 담당 공동 참석.", token, dedup),
        item("source-fingerprint", ["원천 계약 지문"], "원천 계약 지문: 10월 23일 계약 지문 비교기 회귀 시험을 검수자가 수용했다.", claim("계약 지문 비교기", "test_acceptance", 23, artifact="R17 계약 비교 시험")),
        item("reverse-test", ["시간창 역전 처리"], "시간창 역전 처리: 10월 22일 역순 재시도 시험을 수용했다. 인수 회의는 다음 주 예정이다.", claim("역순 재시도 시험", "test_acceptance", 22), claim("인수 회의", "plan", outcome=None)),
        item("late-window", ["배치 역전 보정"], "배치 역전 보정의 지연 구간 보정을 이번 주 검수했다. etl.user07이 공동 검토했다.", claim("지연 구간 보정", "test_acceptance", outcome=None), intended_task_key="DL-21745"),
        item("boundary-check", ["분산 체크포인트 교차검증"], "분산 체크포인트 교차검증: 경계 조건 검증을 이번 주 수용했다.", claim("경계 조건 검증", "test_acceptance")),
    ])
    report("etl-dev-monthly-2026-10", "ETL 개발 월간보고 2026-10", at(3, november=True), at(1, 0), at(1, 0, november=True), [
        item("order-recovery", ["복구 위치 보존", "이벤트 중복 차단"], "10월 15일 R17 주문 복구 패치 검수 실적: 복구 위치 보존의 재시작 토큰 저장, 이벤트 중복 차단의 멱등 처리 키 시험 수용.", token, dedup),
        item("reverse-final", ["시간창 역전 처리"], "시간창 역전 처리 인수는 10월 29일 고객이 승인했다. 시간창 역전 처리 담당이 참석했다.", claim("시간창 역전 처리 인수", "final_acceptance", 29)),
        item("boundary-check", ["분산 체크포인트 교차검증"], "분산 체크포인트 교차검증: 경계 조건 검증 수용. 이번 달 검수 목록에 반영했다.", claim("경계 조건 검증", "test_acceptance")),
        item("integration-pending", ["다중 원천 통합"], "다중 원천 통합 검증: Jira 상태는 10월 31일 종료로 기록됐다. 11월 2일 고객 검수에서 원천 간 합계 불일치가 남아 인수 보류를 통보받았다.", claim("다중 원천 통합 검증", "final_acceptance", 2, outcome="pending", november=True)),
    ])
    report("etl-dev-daily-2026-10-24", "ETL 개발 일간보고 2026-10-24", at(25), at(24, 0), at(25, 0), [
        item("fingerprint-final", ["스키마 변화 표식"], "스키마 변화 표식의 계약 지문 비교기는 10월 24일 인수 승인으로 기록했다.", claim("계약 지문 비교기", "final_acceptance", 24))])
    report("etl-dev-daily-2026-10-24", "ETL 개발 일간보고 2026-10-24", at(5, november=True), at(24, 0), at(25, 0), [
        # These new undated statements are not dated to the withdrawn October24
        # acceptance. Unlike explicit "this week/month" reports, their public text
        # supplies no original-period occurrence for the pending decision or plan.
        item("fingerprint-final", ["스키마 변화 표식"], "11월 5일 정정: 10월 24일 계약 지문 비교기 인수 승인 기록은 test 환경 시연과 혼동했다. 운영 인수 보류이며 추가 합계 검증을 계획했다. 스키마 변화 표식 담당 검토.",
             claim("계약 지문 비교기", "final_acceptance", outcome="pending", period_bound=False),
             claim("추가 합계 검증", "plan", outcome=None, period_bound=False))], version=2)
    report("storage-dev-daily-2026-10-23", "Storage 개발 일간보고 2026-10-23", at(24), at(23, 0), at(24, 0), [
        item("storage-fingerprint", ["스키마 변화 표식"], "Storage 스키마 변화 표식: test 환경 R17 계약 지문 비교기 검수를 10월 23일 수용했다.",
             claim("계약 지문 비교기", "test_acceptance", 23, artifact="Storage 검증 패키지"))],
        page_context={**context, "module": "Storage", "environment": "test"})


def _development(a):
    months = a.spec.months
    for i, title in enumerate(("수집 계약과 스키마", "증분 적재 신뢰성", "재처리와 복구", "연합 조회 모델", "품질 검증과 관측")):
        a.issue(f"DL-{20000+i}", "development_epics", a.spec.start, title, title + " 연간 개발 범위. 하위 Task의 SP를 기준으로 진척을 집계한다.", kind="Epic")
    memberships, previous, next_id = {}, [], 21000
    for month, (start, end) in enumerate(months):
        count = 100 if month == 0 else (60 if month == 11 else 64)
        fresh = [f"DL-{i}" for i in range(next_id, next_id + count)]
        next_id += count
        active = fresh if month == 0 else previous[-(100-count):] + fresh
        for key in active:
            memberships.setdefault(key, []).append(month)
        previous = fresh
    assert next_id == 21800
    for index, (key, active) in enumerate(memberships.items()):
        start = months[active[0]][0] + timedelta(hours=9)
        finish = months[active[-1]][1] - timedelta(hours=8)
        is_child = index % 5 == 1
        parent = f"DL-{int(key[3:])-1}" if is_child else None
        # Each block has four parent tasks and one subtask. Rotate blocks across
        # all five epics so the subtask slot cannot leave an entire epic empty.
        epic = None if is_child else f"DL-{20000+(index//5)%5}"
        topic = ("CDC offset 재시작", "스키마 호환성", "배치 SLA", "실패 구간 재처리", "Polaris 권한 전파")[index % 5]
        sp = 0 if is_child else a.rng(key).choice([1, 2, 3, 5, 8, 13])
        if key == "DL-21000":
            sp = 8
        lifecycle = next((name for name, target in DEVELOPMENT_LIFECYCLES.items() if target == key), None)
        intervals = None
        if lifecycle:
            finish = a.spec.end
            intervals = [(start, finish)]
            if lifecycle == "dormant_recent_start":
                intervals = [(months[11][0] + timedelta(days=27, hours=10), finish)]
            elif lifecycle == "reopened_rework":
                intervals = [(start, months[8][0] + timedelta(days=19, hours=16)),
                             (months[11][0] + timedelta(days=9, hours=10), finish)]
        a.issue(key, "development", start, f"{topic} 개선 {index+1}",
                f"{topic} 개발 작업. 재현 조건: 원천 파티션 지연과 스키마 변동을 분리한다. "
                f"수용 기준: 실패 시 checkpoint 보존, 재시도 후 중복 행 0건. 설계 검토와 운영 실행 실적은 별도로 기록한다. 담당 범위 {key}.",
                kind="Sub-Task" if is_child else ("Bug" if index % 9 == 0 else "Task"),
                sp=sp, epic=epic, parent=parent, active_until=finish,
                status="Open" if lifecycle == "dormant_recent_start" else "In Progress", active_intervals=intervals)
        if lifecycle:
            _development_lifecycle(a, key, lifecycle)
            continue
        a.emit(key + ":review", key, "development", "note", start + timedelta(days=3),
               {"text": f"설계 검토: {topic}의 실패 재현과 계약 테스트를 점검했다. 코드 구현은 담당자가 수행하고 검수자는 실행 여부만 확인한다."})
        a.field(key, "development", finish, "status", "In Progress", "Resolved")
        if index % 31 == 0:
            old_index = a.rng(key).randrange(8)
            a.field(key, "development", start + timedelta(days=5), "assignee",
                    f"etl.user{old_index:02d}", f"etl.user{(old_index+1)%8:02d}")
    # Same observed parent state can conceal completed/reopened history.
    a.field("DL-21000", "development", a.spec.end + timedelta(days=2), "status", "Resolved", "Reopened")
    a.field("DL-21000", "development", a.spec.end + timedelta(days=3), "status", "Reopened", "In Progress")
    a.field("DL-21001", "development", months[3][0] + timedelta(days=5), "parent", "DL-21000", "DL-21002")
    # A second completed child remains under a parent whose integration test is open.
    a.field("DL-21006", "development", months[3][0] + timedelta(days=6), "parent", "DL-21005", "DL-21000")
    a.emit("DL-21000:membership-audit", "DL-21000", "development", "note",
           months[2][0] + timedelta(days=13, hours=12),
           {"text": "2026-01-15 00:00 KST 기준 하위 티켓 전체 목록: DL-21001 한 건. "
                    "완료 상태는 각 하위 티켓의 이력에서 별도로 확인한다. 이 목록은 완료 여부를 보증하지 않는다."})
    a.document("etl-wbs-mapping", a.spec.start, "<p>ETL-1: DL-20000 가중치 3, DL-20001 가중치 1. SP 가중 진척률이며 Jira 부모 계층이 아니다.</p>",
               kind="mapping", extra={"wbs": "ETL-1", "epics": {"DL-20000": 3, "DL-20001": 1}})
    a.document("etl-wbs-mapping", months[6][0], "<p>2026-05-01 적용: ETL-1은 DL-20000 가중치 2, DL-20002 가중치 1로 변경. 이전 매핑은 버전 1에 보존.</p>",
               version=2, kind="mapping", extra={"wbs": "ETL-1", "epics": {"DL-20000": 2, "DL-20002": 1}})


def _development_lifecycle(a, key, pattern):
    """Eight independently authored histories, without progress percentages or answer labels."""
    start = a.spec.start
    def when(month, day, hour=10):
        return a.spec.months[month][0] + timedelta(days=day-1, hours=hour)
    def update(suffix, at, phase, text, **facts):
        a.emit(key + ":development:" + suffix, key, "development", "development_progress", at,
               {"phase": phase, "text": text, **facts})
    if pattern == "dormant_recent_start":
        update("backlog", start + timedelta(days=1, hours=10), "not_started",
               "원천 계약 승인이 지연되어 백로그에 보관한다. 아직 구현·테스트에 착수하지 않았다.")
        begun = when(11, 28)
        a.field(key, "development", begun, "status", "Open", "In Progress")
        update("start", begun, "started", "원천 계약이 승인되어 오늘 CDC offset 복구 구현에 처음 착수했다. CDC offset 복구 구현과 재시작 검증 모두 미완료다.",
               pending=["CDC offset 복구 구현", "재시작 검증"])
    elif pattern == "comment_verified_partial":
        update("start", when(0, 5), "started", "체크포인트 보존과 중복 방지 구현에 착수했다. 통합 검증은 별도 승인한다.")
        update("checkpoint", when(11, 8, 14), "milestone", "검수 기록: CDC 체크포인트 저장을 장애 주입 시험으로 확인하고 수용했다. 중복 방지 키 검증과 장애 복구 통합 검증은 다음 검수 대상으로 남겼다.",
               completed=["CDC 체크포인트 저장"], pending=["중복 방지 키 검증", "장애 복구 통합 검증"])
        update("dedup", when(11, 20, 14), "milestone", "검수 기록: 중복 방지 키 검증을 통과했다. 장애 복구 통합 검증은 남아 있다. 통합 대상 범위는 추가 협의 중이며 항목별 난이도·가중치 합의도 미완료다.",
               completed=["중복 방지 키 검증"], pending=["장애 복구 통합 검증"])
    elif pattern == "insufficient_comments":
        update("vague", when(11, 22, 15), "unverified", "확인 중입니다.")
        # Deliberately inaccessible authoring fact: reducers and public rendering must ignore it.
        a.emit(key + ":unpublished-draft", key, "development", "development_private", when(11, 21),
               {"deliverables": ["승인되지 않은 초안"]})
    elif pattern == "long_running_incremental":
        update("start", when(0, 10), "started", "다중 원천 계약과 재시도 격리 설계를 시작했다. 여러 릴리즈에 나눠 검증할 예정이다.")
        for suffix, at, item in (("contract", when(1, 12, 11), "계약 샘플"),
                                  ("retry", when(4, 18, 13), "재시도 격리"),
                                  ("latency", when(8, 21, 14), "지연 관측")):
            update(suffix, at, "milestone", f"단계 검수: {item} 산출물을 수용했다. 다중 원천 회귀 검증은 후속 검수 일정에 남겼다.",
                   completed=[item], pending=["다중 원천 회귀 검증"])
        update("october", when(11, 16, 11), "update", "다중 원천 회귀 검증을 계속 수행 중이다. 12월·3월·7월에 수용한 세 산출물 외 새 완료 항목은 없다.",
               pending=["다중 원천 회귀 검증"])
    elif pattern == "dependency_wait":
        update("start", when(0, 7), "started", "연결 계약 초안을 작성하기 시작했다.")
        update("draft", when(2, 15, 13), "milestone", "검토자가 연결 계약 초안을 수용했다. 실제 연결 시험은 원천 방화벽 승인 후 진행한다.", completed=["연결 계약 초안"])
        update("blocked", when(11, 6, 14), "blocked", "원천 방화벽 승인 대기 때문에 연결 시험을 진행할 수 없다. 의존 요청 DL-50002는 원천 운영자의 승인을 기다리고 있다.",
               blockers=[{"key": "DL-50002", "reason": "원천 방화벽 승인"}], pending=["원천 방화벽 승인"])
    elif pattern == "reopened_rework":
        update("start", when(0, 8), "started", "역순 이벤트 검증 구현을 시작했다.")
        accepted = when(8, 20, 16)
        update("accept", accepted, "milestone", "당시 합의한 역순 이벤트 검증을 통과해 산출물을 수용했다.", completed=["역순 이벤트 검증"], pending=[])
        a.field(key, "development", accepted + timedelta(minutes=5), "status", "In Progress", "Resolved")
        reopened = when(11, 10)
        a.field(key, "development", reopened, "status", "Resolved", "Reopened")
        update("regression", reopened, "rework", "운영과 같은 역순 입력에서 회귀 결함이 재현되어 재오픈했다. 앞서 수용한 역순 이벤트 검증은 철회하고 역순 이벤트 회귀 수정을 다시 검증해야 한다.",
               invalidate=["역순 이벤트 검증"], pending=["역순 이벤트 회귀 수정"])
    elif pattern == "handoff":
        old_index = a.rng(key).randrange(8)
        new_index = (old_index+1)%8
        update("start", when(0, 9), "started", "스키마 매핑 변경과 인수 자료 작성에 착수했다.")
        handed = when(11, 14, 13)
        a.field(key, "development", handed, "assignee", f"etl.user{old_index:02d}", f"etl.user{new_index:02d}")
        update("handoff", handed, "handoff", f"etl.user{old_index:02d}가 스키마 매핑 인수인계를 완료했고 etl.user{new_index:02d}가 인수 자료를 확인했다. 수신팀 통합 검증은 진행 중이다.",
               completed=["스키마 매핑 인수인계"], pending=["수신팀 통합 검증"],
               contributor=f"etl.user{old_index:02d}", recipient=f"etl.user{new_index:02d}")
    elif pattern == "administrative_update":
        update("start", when(0, 12), "started", "쿼리 계획 재현과 성능 회귀 검증을 시작했다.")
        update("repro", when(10, 15, 11), "milestone", "쿼리 계획 재현 절차를 검수자가 수용했다. 성능 회귀 검증은 미완료다.", completed=["쿼리 계획 재현"], pending=["성능 회귀 검증"])
        admin = when(11, 31, 16)
        a.field(key, "development", admin, "priority", "P2-Major", "P3-Minor")
        update("admin", admin, "administrative", "문서 보관 분류와 우선순위를 정리했다. 코드 변경은 없으며 성능 회귀 시험을 재실행하지 않았다.")


def _operations(a):
    kinds = ("load", "modify", "reload", "suspend", "delete", "federation")
    names = {"load": "신규 적재", "modify": "컬럼·쿼리 변경", "reload": "재적재", "suspend": "적재 중단", "delete": "데이터 삭제", "federation": "연합 연결"}
    inventory = [f"prod.crm.m{month+1:02d}_{operation}_{slot:02d}" for month in range(12)
                 for operation in ("modify", "reload", "suspend", "delete") for slot in range(12)]
    a.document("etl-opening-inventory", a.spec.start - timedelta(days=1),
               "<p>기간 시작 전 운영 자산 대장. 아래 테이블은 이미 운영 적재 중이며 신규 적재 실적이 아니다: "
               + ", ".join(inventory) + "</p>", kind="inventory", extra={"tables": inventory})
    for month, (start, end) in enumerate(a.spec.months):
        for slot in range(72):
            key, operation = f"DL-{31000+month*72+slot}", kinds[slot//12]
            created = start + timedelta(days=slot%24, hours=9)
            if month == 0 and slot == 11:
                created = start
            target = f"prod.crm.m{month+1:02d}_{operation}_{slot%12:02d}"
            targets = [target] if operation != "federation" else [f"polaris.prod.m{month+1:02d}_view{slot%12}", f"external.prod.m{month+1:02d}_table{slot%6}"]
            if month == 0 and slot == 0:
                targets = ["prod.crm.customers", "prod.crm.orders_pending"]
            elif month == 0 and slot == 1:
                targets = ["prod.crm.customers", "test.crm.customers"]
            elif month == 0 and slot == 2:
                targets = ["prod.crm.customers"]
            a.issue(key, "operations", created, f"{names[operation]} 요청 {month+1:02d}-{slot+1:02d}",
                f"요청 유형: {names[operation]}. 요청 대상 {', '.join(targets)}. "
                "티켓 종료는 접수 범위의 처리 종료이며 각 대상의 실행과 검증 내역은 댓글의 실행번호와 승인 기록을 확인한다.",
                operation=operation, targets=targets,
                links=[{"type": "Relates", "key": f"DL-{41000+month*48+slot%48}", "dir": "inward"}])
            completed = created + timedelta(days=2)
            if month == 0 and slot == 11:
                completed = start
            elif month == 0 and slot == 12:
                completed = end
            tables = targets if operation != "federation" else []
            edges = [(targets[0], targets[1])] if operation == "federation" else []
            ref, detail = None, None
            if month == 0 and slot == 0:
                tables = ["prod.crm.customers"]
                detail = "실행번호 RUN-CUSTOMERS: prod.crm.customers 적재·검증 완료. prod.crm.orders_pending은 원천 권한이 없어 실행하지 않았다. 접수 티켓은 부분 처리 종료로 Resolved."
                ref = "RUN-CUSTOMERS"
            elif month == 0 and slot == 1:
                tables = ["test.crm.customers"]
                detail = "실행번호 RUN-TEST-CUSTOMERS: test.crm.customers만 적재·검증 완료. prod.crm.customers는 DL-31000의 RUN-CUSTOMERS와 같은 실행을 재인용한 것이므로 신규 실행 아님. 환경별 별개 테이블."
                ref = "RUN-TEST-CUSTOMERS"
            elif month == 0 and slot == 2:
                tables = ["prod.crm.customers"]
                completed = a.spec.start + timedelta(days=2, hours=9)
                ref, detail = "RUN-CUSTOMERS", "공동 보고: DL-31000의 실행번호 RUN-CUSTOMERS, prod.crm.customers 한 번의 적재를 인수인계. 별도 실행하지 않았다."
            if month == 0 and slot == 0:
                # Noise on both sides places the decisive record beyond a 100-row preview.
                for n in range(105):
                    old, new = ("P2-Major", "P3-Minor") if n % 2 == 0 else ("P3-Minor", "P2-Major")
                    a.field(key, "operations", created + timedelta(minutes=n), "priority", old, new, f"pre-{n}")
                    a.emit(f"{key}:pre:{n}", key, "operations", "note", created + timedelta(minutes=n),
                           {"text": f"접수 첨부 {n+1}: 파티션 크기와 샘플 마스킹 점검. 실제 적재의 승인 기록은 별도 검증 회의 후 남긴다."})
                for n in range(105):
                    when = completed + timedelta(days=1, minutes=n)
                    old, new = ("P3-Minor", "P2-Major") if n % 2 == 0 else ("P2-Major", "P3-Minor")
                    a.field(key, "operations", when, "priority", old, new, str(n))
                    a.emit(f"{key}:post:{n}", key, "operations", "note", when,
                           {"text": f"보관 점검 {n+1}: checksum과 보관 기간을 확인. 추가 실행 없음."})
            if slot % 17 == 16:
                a.emit(key + ":unknown", key, "operations", "note", completed,
                       {"evidence_missing": True, "text": "접수 검토 중. 실행 로그와 검증 승인이 없어 실제 완료 여부 및 수량을 확정할 수 없다."})
            else:
                a.execution(key, completed, operation, tables, ref=ref, edges_add=edges, text=detail)
                a.field(key, "operations", completed + timedelta(minutes=10), "status", "In Progress", "Resolved")
            if month == 0 and slot == 3:
                correction = end + timedelta(days=4, hours=10)
                a.emit(key + ":correction", key, "operations", "correction", completed,
                    {"corrects": f"RUN-{key[3:]}", "text": f"정정: 실행번호 RUN-{key[3:]} 완료 보고는 리허설 결과를 운영 결과로 오인. {target} 운영 실행은 없었다. 업무 발생일은 기존 보고일이며 이번 정정 기록일과 구분한다."}, recorded=correction)
            if month == 0 and slot == 4:
                a.field(key, "operations", completed + timedelta(days=5), "status", "Resolved", "Reopened")
            if month == 0 and slot == 5:
                a.emit(key + ":cancel", key, "operations", "note", completed + timedelta(days=1),
                       {"text": "후속 재적재 요청은 취소했다. 앞서 검증한 최초 적재 실행은 실제 수행됐으며 취소 대상이 아니다."})
    a.emit("customer-rename", "DL-31006", "operations", "rename", a.spec.start + timedelta(days=10),
           {"old": "prod.crm.customer_v1", "new": "prod.crm.customers", "text": "변경 승인 RENAME-CUSTOMER: prod.crm.customer_v1 → prod.crm.customers. 동일 물리 테이블의 이름 변경을 DBA가 확인. test.crm.customers는 별도 환경이다."})
    a.execution("DL-31060", a.spec.start + timedelta(days=20), "federation", [], ref="FED-SALES", edges_add=[
        ("polaris.prod.sales", "external.prod.crm_customers"), ("polaris.prod.sales", "external.prod.crm_orders"),
        ("polaris.prod.marketing", "external.prod.crm_customers")])
    a.execution("DL-31132", a.spec.months[1][0] + timedelta(days=20), "federation", [], ref="FED-SALES-DETACH",
                edges_remove=[("polaris.prod.sales", "external.prod.crm_orders")])
    a.document("etl-monthly-2025-11", a.spec.months[1][0] + timedelta(hours=12),
               "<p>2025년 11월 발행본. DL-31003 운영 적재 완료로 보고. DL-31000은 두 대상 중 고객만 검증 완료.</p>")
    a.document("etl-monthly-2025-11", a.spec.months[1][0] + timedelta(days=5, hours=12),
               "<p>정정 발행본. DL-31003은 리허설 오인으로 운영 실적에서 제외. 최초 발행본의 문구는 버전 1에 보존한다.</p>", version=2)


def _simple_and_other(a):
    for month, (start, _) in enumerate(a.spec.months):
        for slot in range(48):
            key = f"DL-{41000+month*48+slot}"
            subject = ("적재 주기 문의", "컬럼 의미 설명", "집계 값 확인", "권한 신청 안내")[slot%4]
            created = start + timedelta(days=slot%24, hours=11)
            a.issue(key, "simple_voc", created, f"{subject} {month+1:02d}-{slot+1:02d}",
                f"사용자 문의: {subject}. prod.crm.customers의 기준시각과 필터를 설명해 달라는 요청. "
                "이 티켓에서는 적재·변경·삭제를 실행하지 않는다. 운영 변경은 연결된 별도 요청에서 승인한다.",
                links=[{"type": "Relates", "key": f"DL-{21000+(month*48+slot)%800}", "dir": "outward"},
                       {"type": "Relates", "key": f"DL-{31000+month*72+slot%72}", "dir": "outward"}])
            answer = created + timedelta(days=1)
            missing = month == 11 and slot == 47
            a.emit(key + ":answer", key, "simple_voc", "note", answer,
                   {"verified_answer": not missing, "evidence_missing": missing,
                    "text": "답변·확인 기록이 없어 실제 문의 해결 여부를 확정할 수 없다. 운영 실행 요청은 아니다."
                        if missing else f"{subject} 답변: KST 기준 파티션과 원천 UTC 지연을 구분하고 조회 필터를 안내했다. "
                                        "문의자가 답변 확인을 마쳤다. 테이블 언급은 설명 자료이며 운영 실행은 없다."})
            if slot % 13 != 12:
                a.field(key, "simple_voc", answer + timedelta(hours=1), "status", "In Progress", "Resolved")
    modules = ("Catalog", "Query", "Security", "Storage", "Platform")
    topics = ("고객 메타데이터 검색", "쿼리 계획 캐시", "마스킹 정책 상속", "오브젝트 압축", "배포 로그 보관")
    for index in range(7755):
        key, module = f"DL-{50000+index}", modules[index%5]
        if index == 2:
            created = a.spec.start + timedelta(days=4, hours=10)
            a.issue(key, "other", created, "원천 방화벽 승인 대기", "Security 모듈 원천 방화벽 승인 요청. ETL 연결 계약 DL-21015의 연결 시험에 필요한 선행 조건이다. 보안 담당 검토와 원천 운영자 승인 둘 다 필요하다.",
                    module="Security", links=[{"type": "Blocks", "key": "DL-21015", "dir": "outward"}])
            a.emit(key + ":approval-pending", key, "other", "note", a.spec.months[11][0] + timedelta(days=4, hours=14),
                   {"text": "보안 담당 검토는 마쳤으나 원천 운영자 승인은 아직 접수되지 않았다. 방화벽은 열지 않았으며 DL-21015 연결 시험은 대기 중이다."})
            a.field(key, "other", created + timedelta(days=1), "priority", "P2-Major", "P1-Critical")
            continue
        rng = a.rng(key)
        month = rng.randrange(12)
        created = a.spec.months[month][0] + timedelta(days=rng.randrange(20), hours=10)
        topic = topics[index%5]
        a.issue(key, "other", created, f"{topic} 검증 {index+1:04d}",
            f"{module} 모듈의 {topic}. prod.crm.customers와 유사한 검색어를 포함하지만 ETL 운영 실행 요청은 아니다. "
            f"현상: {topic} 응답에서 환경 필터가 누락될 수 있음. 검증 항목: 권한 경계, 페이지 순서, null 값 처리. "
            f"분석 세션 {index+1}의 재현 로그와 수정 계획을 댓글에 보관한다.", module=module,
            kind="Bug" if index%3 == 0 else "Improvement", sp=rng.choice([1, 2, 3, 5]),
            links=[{"type": "Relates", "key": f"DL-{31000+index%864}", "dir": "outward"}] if index%19 == 0 else [])
        a.emit(key + ":analysis", key, "other", "note", created + timedelta(days=1),
               {"text": f"재현 분석 {index+1}: {topic}에서 tenant 필터와 캐시 키의 일치 여부를 확인. "
                        f"대상 모듈 {module}; ETL 댓글의 실행번호를 이 이슈의 실적으로 재사용하지 않는다."})
        a.field(key, "other", created + timedelta(days=2), "status", "In Progress", "Resolved" if index%4 else "Reopened")


def build_etl_world(spec=None):
    spec = spec or YearWorldSpec()
    a = _Author(spec)
    _development(a)
    _operations(a)
    _simple_and_other(a)
    _development_reports(a)
    events = tuple(a.events)
    world, sources, documents = project(events, spec)
    counts = dict(Counter(event.stream for event in events if event.kind == "issue"))
    counts["total"] = len(world.issues)
    digest = hashlib.sha256(json.dumps({"issues": world.issues, "users": world.users, "currentUser": world.ME,
        "documents": documents, "plan": world.plan}, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    manifest = {"version": VERSION, "seed": spec.seed, "timezone": "Asia/Seoul", "currentUser": world.ME, "start": stamp(spec.start),
                "end": stamp(spec.end), "observed_until": stamp(spec.observed_until), "counts": counts,
                "data_sha256": digest, "event_count": len(events), "public_source_count": len(sources),
                "randomness": "SHA-256(seed|entity-key), independent per entity", "statistics": "synthetic defaults"}
    oracle = EventOracle(events, spec.observed_until)
    manifest["development_lifecycle_coverage"] = {name: [key] for name, key in DEVELOPMENT_LIFECYCLES.items()}
    manifest["monthly_active_development"] = {start.strftime("%Y-%m"): len(oracle.active_development(start, end))
                                              for start, end in spec.months}
    manifest["development_report_coverage"] = ["linked", "unlinked_context", "many_tasks_one_item", "one_task_many_reports",
        "other_module_environment", "ambiguous_mapping", "repeated_reporting", "ambiguous_duplicate", "late_revision", "jira_contradiction"]
    return EtlWorldBundle(spec, world, oracle, manifest, sources, documents)

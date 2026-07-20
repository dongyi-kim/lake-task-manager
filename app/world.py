"""
Fake world — 단일 결정적 Jira/Confluence 데이터 세계.
fake 서버(HTTP)와 mock 모드(in-process)가 이 world 를 공유한다.

- config/wbs_config.yaml(module→WBS→epic) + people.yaml 로부터 결정적으로 생성.
- 모든 이슈(Epic/Story/Task/Bug/Sub-task)는 world.issues 에 canonical 형태로 존재.
- 인덱스: by_label, by_assignee, epic_children.
- Jira REST 직렬화 + Confluence/activity 소스 제공.
"""

import hashlib
import random
from datetime import date, timedelta
from functools import lru_cache

from . import worldcontent as wc
from .settings import get_settings, load_people, load_plan

# 사내 워크플로 상태 (Open/In Progress/Resolved/Closed/Reopened) → 내부 cat
_STATUS_NAMES = {"todo": ["Open", "Open", "Reopened"],
                 "inprogress": ["In Progress"],
                 "done": ["Resolved", "Resolved", "Closed"]}
_STATUS_ID = {"Open": "1", "In Progress": "3", "Reopened": "4", "Resolved": "5", "Closed": "6"}
# 내부 cat → 실 Jira DC statusCategory (key=new/indeterminate/done). prod 와 동일해야 함.
_JIRA_CAT = {
    "todo": {"id": 2, "key": "new", "colorName": "blue-gray", "name": "To Do"},
    "inprogress": {"id": 4, "key": "indeterminate", "colorName": "yellow", "name": "In Progress"},
    "done": {"id": 3, "key": "done", "colorName": "green", "name": "Done"},
}
# 사내 이슈타입: Bug, Epic, Improvement, New Feature, Story, Task, Sub-Task (id 는 fake /issuetype 와 일치)
_TYPE_ID = {"Bug": "1", "Epic": "2", "Improvement": "3", "New Feature": "4",
            "Story": "5", "Task": "6", "Sub-Task": "7"}
SUBTASK_TYPE = "Sub-Task"
_STORYLIKE = {"Story", "Task", "Improvement", "New Feature"}   # SP 보유 + subtask 가능
_CHILD_TYPES = ["Story", "Task", "Bug", "Improvement", "New Feature"]
_VIT_ROOT_TYPES = ["Epic", "Task", "Story", "Improvement"]
_VIT_SIZES = [5, 2, 4, 0, 6, 3, 1]          # 모듈별 PMO_VIT 현안 개수(다양성)
_CHILD_TYPES_W = [6, 4, 2, 2, 1]            # 가중치


def _rng(*parts):
    seed = int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)
    return random.Random(seed)


def _cat(rng, maturity):
    return "done" if rng.random() < maturity else ("inprogress" if rng.random() < 0.5 else "todo")


# 사용자 표시이름 합성 풀 — displayName "{본명} {소속회사명}". 회사는 SKCC 다수 + 협력사 소수
# (공백 포함 'SK주식회사 C&C' 로 본명 파싱[names.real_name=첫어절]의 다어절 회사 견고성도 커버).
_SURNAMES = ["김", "이", "박", "최", "정", "강", "조", "윤", "장", "임",
             "한", "오", "서", "신", "권", "황", "안", "송", "류", "홍"]
_GIVEN = ["도윤", "서준", "하준", "지호", "예준", "민재", "수아", "지우", "서연", "하은",
          "은우", "지훈", "현우", "유진", "다은", "준서", "시우", "민서", "채원", "지안"]
_COMPANIES = ["SKCC", "SKCC", "SKCC", "SKCC", "코어씨앤아이", "데이터메이커", "SK주식회사 C&C"]


def _shash(s):
    """id → 결정적 정수(PYTHONHASHSEED 무관). 본명/회사 배정용."""
    h = 0
    for ch in s:
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    return h


def _iid(key):
    """이슈 키 → 결정적 숫자 id(실 Jira issue.id 형태). fake server 의 _iid 와 동일 규칙."""
    return str(int(hashlib.md5(key.encode()).hexdigest()[:8], 16))


class World:
    def __init__(self, plan, people, today):
        self.today = today
        self.plan = plan
        self.people = people
        self.project = plan.get("project_key", "LAKE")
        s = get_settings()
        self.sp_field = s.sp_field_id
        self.epic_link_field = s.epic_link_field_id

        self.modules = list(plan["modules"])
        # 컴포넌트 id 맵 (fake /project/{k}/components 의 id 규칙 100+idx 와 일치)
        self._comp_ids = {m: str(100 + i) for i, m in enumerate(self.modules + ["사용자 VoC"])}
        self.users = self._make_users()
        self.issues = {}                 # key -> canonical issue
        self.attachments = {}            # id -> 첨부(jira820 store.attachments 형태)
        self._counter = 5000             # 생성 키 DL-5001+ (config epic id DL-1xx 와 충돌 회피)

        self._build_wbs_epics()
        self._build_extra_epics()     # WBS 밖 일반 epic
        self._build_standalone()      # epic 소속 아닌 독립 task/story
        self._build_history()         # 1~6월 생성·종료된 과거 완료 이슈 (대량)
        self._build_vit()
        self._build_voc()
        self._build_links()           # 이슈 링크(relates to 등) — '관련 Task' 용
        self._build_attachments()     # 첨부파일 — '첨부파일' 패널 용
        self._index()
        self._build_activity()
        self._build_confluence()

    # ── 사용자 ──
    def _make_users(self):
        # 사내 관례: displayName = "{본명} {소속회사명}", id = "{회사코드}.{사번}"(x*=개발/i*=운영).
        # 본명·회사는 id 로 결정적 배정(같은 사람=항상 같은 이름). 회사명은 대부분 SKCC + 협력사 소수.
        users = {"pmo": {"name": "pmo", "displayName": "PMO Office"},
                 "lead": {"name": "lead", "displayName": "정한울 SKCC"}}
        taken = {"정한울"}                       # 본명 중복 방지(데모 가독성) — id 정렬로 결정적
        for uid in sorted({u for ids in self.people.values() for u in ids}):
            h = _shash(uid)
            g = h // 7
            nm = _SURNAMES[h % len(_SURNAMES)] + _GIVEN[g % len(_GIVEN)]
            while nm in taken:                    # 충돌 시 given 을 결정적으로 다음 후보로
                g += 1
                nm = _SURNAMES[h % len(_SURNAMES)] + _GIVEN[g % len(_GIVEN)]
            taken.add(nm)
            co = _COMPANIES[(h // 3) % len(_COMPANIES)]
            users[uid] = {"name": uid, "realName": nm, "company": co,
                          "displayName": f"{nm} {co}"}
        return users

    def _dn(self, uid):
        """사용자 표시이름(displayName). changelog 의 fromString/toString 용."""
        return (self.users.get(uid) or {}).get("displayName") or uid

    def _pool(self, module):
        return self.people.get(module, []) or ["pmo"]

    def _newkey(self):
        self._counter += 1
        return f"{self.project}-{self._counter}"

    def _dt(self, d, hm=None):
        return d.isoformat() + "T" + (hm or "09:00") + ":00.000+0000"

    # ── 이슈 생성 헬퍼 ──
    def _make_issue(self, rng, itype, module, epic_key=None, parent_key=None,
                    label_pmo=False, summary=None, component=None, assignee=None,
                    created=None, resolved=None, force_cat=None):
        pool = self._pool(module)
        assignee = assignee or pool[rng.randrange(len(pool))]
        reporter = rng.choice(["pmo", "lead"] + pool)
        maturity = rng.uniform(0.15, 0.85)
        created = created or (self.today - timedelta(days=rng.randint(5, 175)))
        if resolved is not None:                       # 과거 종료 override
            cat = "done"
        else:
            cat = force_cat or _cat(rng, maturity)
            if cat == "done":                          # 완료일을 created~today 전체에 분산
                span = max((self.today - created).days, 1)
                resolved = created + timedelta(days=rng.randint(1, span))
        updated = resolved or (created + timedelta(days=rng.randint(0, max((self.today - created).days, 1))))
        if updated > self.today:
            updated = self.today
        due = None if rng.random() < 0.25 else (created + timedelta(days=rng.randint(40, 160)))
        def _tm():   # 결정적 업무시간 hh:MM (뉴스/활동 시간표시용)
            return "%02d:%02d" % (rng.randint(8, 20), rng.choice([0, 10, 15, 20, 30, 40, 45, 50]))
        status_name = rng.choice(_STATUS_NAMES[cat])
        # SP: story-like 는 값 or 누락(None), Bug 는 0, Epic/Sub-Task 는 None
        if itype in _STORYLIKE:
            sp = None if rng.random() < 0.12 else rng.choice([1, 2, 3, 5, 8])
        elif itype == "Bug":
            sp = 0
        else:
            sp = None
        labels = []
        if itype in _STORYLIKE and rng.random() < 0.15:
            labels.append("mock")
        if label_pmo:
            labels.append("PMO_VIT")

        key = epic_key if itype == "Epic" and epic_key else self._newkey()
        ncom = rng.randint(0, 4) if itype != SUBTASK_TYPE else rng.randint(0, 1)
        comments = []
        for (k, t) in wc.comments(rng, pool, ncom):
            # rng 호출 순서(choice→randint)를 원본과 동일하게 유지해 world 결정성 보존.
            author = rng.choice(pool + ["pmo", "lead"])
            ccreated = self.today - timedelta(days=rng.randint(0, 13))
            # 시각은 rng 를 쓰지 않고 결정적으로 파생(업무시간대) → world 시퀀스 불변.
            hh = 9 + (ccreated.toordinal() + len(comments)) % 9
            mm = ((ccreated.toordinal() * 3 + len(comments) * 7) % 6) * 10
            comments.append({"author": author, "kind": k, "text": t, "body": t,
                             "created": ccreated, "tcreated": "%02d:%02d" % (hh, mm)})
        worklog = []
        if cat != "todo":
            for _ in range(rng.randint(0, 3)):
                worklog.append({"author": assignee,
                                "date": self.today - timedelta(days=rng.randint(0, 7)),
                                "seconds": 3600 * rng.randint(1, 6)})

        # 변경 이력(changelog) — 티켓 다이얼로그 타임라인용.
        # ★ rng 를 쓰지 않고 이미 정해진 값에서 결정적으로 파생한다(world 시퀀스 불변 → mock==local 유지).
        changelog = []

        def _cl(day, items, who=None):
            o = day.toordinal()
            changelog.append({"author": who or assignee, "date": day,
                              "time": "%02d:%02d" % (9 + o % 8, (o % 6) * 10), "items": items})

        _h = sum(map(ord, key))
        if cat != "todo":                       # 착수 = Open → In Progress
            _cl(created + timedelta(days=max((updated - created).days, 0) // 3),
                [{"field": "status", "fieldtype": "jira",
                  "from": "1", "fromString": "Open", "to": "3", "toString": "In Progress"}])
        if resolved:                            # 완료 = In Progress → (현재 상태) + 해결
            _cl(resolved,
                [{"field": "status", "fieldtype": "jira",
                  "from": "3", "fromString": "In Progress", "to": "5", "toString": status_name},
                 {"field": "resolution", "fieldtype": "jira",
                  "from": None, "fromString": None, "to": "1", "toString": "Done"}])
        if reporter != assignee and _h % 3 == 0:   # 일부 티켓만 담당자 재지정
            # 실 Jira 규격: from/to = username, fromString/toString = **표시명**
            _cl(created + timedelta(days=1),
                [{"field": "assignee", "fieldtype": "jira",
                  "from": reporter, "fromString": self._dn(reporter),
                  "to": assignee, "toString": self._dn(assignee)}],
                who=reporter)
        if _h % 4 == 0:                         # 잡음: 설명 수정 — 타임라인에서 제외돼야 한다(필터 검증용)
            _cl(updated, [{"field": "description", "fieldtype": "jira",
                           "from": None, "fromString": "(이전 설명)", "to": None, "toString": "(수정된 설명)"}])

        self.issues[key] = {
            "key": key, "project": self.project, "type": itype,
            "summary": summary or self._summary(rng, itype, module),
            "description": wc.description(rng, itype, reporter),
            "module": module, "component": component or module,
            "assignee": assignee, "reporter": reporter,
            "statusCategory": cat, "statusName": status_name,
            "labels": labels, "sp": sp,
            "epicKey": epic_key if itype != "Epic" else None,
            "parentKey": parent_key,
            "created": created, "updated": updated, "resolved": resolved, "due": due,
            "tcreated": _tm(), "tresolved": _tm(), "tupdated": _tm(),
            "comments": comments, "worklog": worklog, "subtasks": [], "changelog": changelog,
            "links": [],
        }
        # 실무처럼 **Sub-Task 는 설명을 안 쓰는 경우가 흔하다** — 1/3 은 빈 설명으로.
        # (다이얼로그의 '상위 티켓 설명'이 겨냥하는 케이스)
        # ★ 반드시 dict 생성 **뒤에** 비운다 — wc.description(rng,…) 을 앞으로 빼면 _summary(rng,…) 와
        #   rng 호출 순서가 바뀌어 world 구성 자체가 달라진다(타입·부모가 통째로 밀림).
        if itype == SUBTASK_TYPE and sum(map(ord, key)) % 3 == 0:
            self.issues[key]["description"] = ""
        return key

    _SUMMARY = {
        "Epic": ["실시간 처리 안정화", "메타데이터 표준화", "대용량 적재 최적화", "쿼리 성능 개선"],
        "Story": ["신규 커넥터 추가", "대시보드 위젯", "API 스펙 확정", "캐시 전략 적용"],
        "Task": ["환경 구성", "배포 파이프라인 개선", "데이터 품질 룰 추가", "스키마 마이그레이션"],
        "Bug": ["NPE 수정", "경계값 오류 수정", "동시성 이슈 해결", "타임아웃 조정"],
        "Improvement": ["로그 포맷 개선", "쿼리 튜닝", "리트라이 정책 보강", "에러 메시지 개선"],
        "New Feature": ["증분 CDC 지원", "롤백 API", "실시간 알림", "셀프서비스 조회"],
        "Sub-Task": ["단위 테스트", "코드 리뷰 반영", "QA 확인", "릴리스 노트"],
    }

    def _summary(self, rng, itype, module):
        return f"[{module}] " + rng.choice(self._SUMMARY.get(itype, ["작업"]))

    def _add_subtasks(self, rng, parent_key, module, n):
        for _ in range(n):
            sk = self._make_issue(rng, SUBTASK_TYPE, module, parent_key=parent_key)
            self.issues[parent_key]["subtasks"].append(sk)

    # ── WBS epics + 자식 ──
    def _build_wbs_epics(self):
        epic_module = {}
        for w in self.plan["wbs"]:
            for e in w["epics"]:
                epic_module.setdefault(e["key"], w["module"])
        for ekey, module in epic_module.items():
            rng = _rng("epic", ekey)
            self._make_issue(rng, "Epic", module, epic_key=ekey)   # Epic 이름은 생성 풀에서(=Jira)
            for _ in range(rng.randint(4, 9)):
                ct = rng.choices(_CHILD_TYPES, weights=_CHILD_TYPES_W)[0]
                ck = self._make_issue(rng, ct, module, epic_key=ekey)
                if ct in _STORYLIKE and rng.random() < 0.4:
                    self._add_subtasks(rng, ck, module, rng.randint(1, 2))

    # ── WBS 밖 일반 Epic (현안 아님) + 자식 ──
    def _build_extra_epics(self):
        for module in self.modules:
            rng = _rng("xepic", module)
            for _ in range(rng.randint(2, 5)):
                ek = self._newkey()
                self._make_issue(rng, "Epic", module, epic_key=ek)
                for _ in range(rng.randint(5, 12)):
                    ct = rng.choices(_CHILD_TYPES, weights=_CHILD_TYPES_W)[0]
                    ck = self._make_issue(rng, ct, module, epic_key=ek)
                    if ct in _STORYLIKE and rng.random() < 0.4:
                        self._add_subtasks(rng, ck, module, rng.randint(1, 2))

    # ── Epic 소속 아닌 독립 Task/Story (진행중·완료 혼합) ──
    def _build_standalone(self):
        for module in self.modules:
            pool = self._pool(module)
            rng = _rng("solo", module)
            for i in range(rng.randint(8, 16)):
                t = rng.choices(_CHILD_TYPES, weights=_CHILD_TYPES_W)[0]
                k = self._make_issue(rng, t, module, assignee=pool[i % len(pool)])
                if t in _STORYLIKE and rng.random() < 0.35:
                    self._add_subtasks(rng, k, module, rng.randint(1, 2))

    # ── 1~6월에 생성·종료된 과거 완료 이슈 (대량, Closed/Resolved) ──
    def _build_history(self):
        jan1 = date(2026, 1, 1)
        for module in self.modules:
            pool = self._pool(module)
            rng = _rng("hist", module)
            for i in range(rng.randint(12, 24)):
                created = jan1 + timedelta(days=rng.randint(0, 120))       # Jan~중순 May
                resolved = created + timedelta(days=rng.randint(5, 110))   # 종료
                if resolved >= self.today:
                    resolved = self.today - timedelta(days=rng.randint(15, 150))
                if resolved <= created:
                    resolved = created + timedelta(days=7)
                t = rng.choices(["Task", "Story", "Bug", "Improvement"], weights=[5, 4, 3, 2])[0]
                k = self._make_issue(rng, t, module, assignee=pool[i % len(pool)],
                                     created=created, resolved=resolved)
                if t in _STORYLIKE and rng.random() < 0.45:
                    for _ in range(rng.randint(1, 3)):
                        scr = created + timedelta(days=rng.randint(0, 15))
                        srv = min(scr + timedelta(days=rng.randint(3, 60)), resolved)
                        sk = self._make_issue(rng, SUBTASK_TYPE, module, parent_key=k,
                                              assignee=pool[i % len(pool)], created=scr, resolved=srv)
                        self.issues[k]["subtasks"].append(sk)

    # ── PMO_VIT 현안 (모듈별 다양 개수, 조상/자손 dedup 케이스 포함) ──
    def _build_vit(self):
        dedup_done = False
        for mi, module in enumerate(self.modules):
            size = _VIT_SIZES[mi % len(_VIT_SIZES)]
            for i in range(size):
                rng = _rng("vit", module, i)
                rtype = rng.choices(_VIT_ROOT_TYPES, weights=[5, 3, 2, 2])[0]
                if rtype == "Epic":
                    ekey = self._newkey()               # Epic 루트 key = LAKE-#
                    root = self._make_issue(rng, "Epic", module, epic_key=ekey, label_pmo=True)
                    # 자식들 (epicKey=root)
                    child_keys = []
                    for _ in range(rng.randint(3, 8)):
                        ct = rng.choices(_CHILD_TYPES, weights=_CHILD_TYPES_W)[0]
                        ck = self._make_issue(rng, ct, module, epic_key=root)
                        child_keys.append(ck)
                        if ct in _STORYLIKE and rng.random() < 0.5:
                            self._add_subtasks(rng, ck, module, rng.randint(1, 2))
                    # dedup 케이스: 자식 하나도 PMO_VIT (조상이 이미 VIT → 스킵되어야 함)
                    if not dedup_done and child_keys:
                        self.issues[child_keys[0]]["labels"].append("PMO_VIT")
                        dedup_done = True
                else:
                    root = self._make_issue(rng, rtype, module, label_pmo=True)
                    for _ in range(rng.randint(2, 5)):
                        self._add_subtasks(rng, root, module, 1)

    # ── VoC 티켓 (Component=VoC, 고객의 소리성 업무) ──
    def _build_voc(self):
        for module in self.modules:
            for pid in self._pool(module):
                rng = _rng("voc", pid)
                for _ in range(rng.randint(0, 5)):
                    self._make_issue(rng, rng.choice(["Task", "Bug", "Story"]), module,
                                     component="사용자 VoC", assignee=pid)

    # 첨부 샘플 — 실제 바이트까지 넣어 다운로드/썸네일 경로도 dev 에서 동작하게 한다(아주 작게).
    _ATT_SPECS = [("설계_검토.png", "image/png"), ("배포_체크리스트.md", "text/markdown"),
                  ("성능_측정.csv", "text/csv"), ("화면_시안.svg", "image/svg+xml")]
    _PNG_1X1 = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000a49444154"
        "789c6360000002000100ffff03000006000557bfabd40000000049454e44ae426082")

    def _build_attachments(self):
        """일부 이슈에 첨부를 단다 — 결정적(키 해시 기반, rng 미사용)."""
        for key in sorted(self.issues):
            it = self.issues[key]
            if it["type"] == "Epic":
                continue
            h = _shash(key)
            n = (h % 5) - 2                       # 대부분 0, 일부 1~2개
            if n <= 0:
                continue
            ids = []
            for i in range(n):
                fn, mime = self._ATT_SPECS[(h + i) % len(self._ATT_SPECS)]
                aid = f"{_iid(key)}{i}"
                data = self._PNG_1X1 if mime == "image/png" else (
                    f"# {fn}\n{key} 관련 산출물 (dev 샘플)\n".encode("utf-8"))
                self.attachments[aid] = {
                    "id": aid, "issueKey": key, "filename": fn, "mimeType": mime,
                    "size": len(data), "data": data,
                    "author": it["assignee"],
                    "created": it["created"],
                }
                ids.append(aid)
            it["attachments"] = ids

    def _build_links(self):
        """이슈 링크(relates to / blocks / duplicates) — 같은 모듈 안에서 몇 쌍 연결.
        양방향으로 넣는다: A=outward / 상대 B=inward (실 Jira 와 동일하게 양쪽에서 보인다).
        ★ rng 를 쓰지 않고 정렬된 키에서 결정적으로 뽑는다 → world 시퀀스 불변."""
        by_mod = {}
        for k, it in sorted(self.issues.items()):
            if it["type"] in ("Epic", SUBTASK_TYPE):
                continue                      # Epic·Sub-Task 는 제외(계보로 이미 보임)
            by_mod.setdefault(it["module"], []).append(k)
        kinds = ["Relates", "Blocks", "Duplicate"]
        n = 0
        for mod in sorted(by_mod):
            ks = by_mod[mod]
            for i in range(0, len(ks) - 1, 5):        # 모듈 안에서 5개마다 한 쌍
                a, b = ks[i], ks[(i + 2) % len(ks)]
                if a == b:
                    continue
                kind = kinds[n % len(kinds)]
                n += 1
                self.issues[a]["links"].append({"type": kind, "dir": "outward", "key": b})
                self.issues[b]["links"].append({"type": kind, "dir": "inward", "key": a})

    # ── 인덱스 ──
    def _index(self):
        self.by_label = {}
        self.by_assignee = {}
        self.epic_children = {}
        for k, it in self.issues.items():
            for lb in it["labels"]:
                self.by_label.setdefault(lb, []).append(k)
            self.by_assignee.setdefault(it["assignee"], []).append(k)
            if it["epicKey"]:
                self.epic_children.setdefault(it["epicKey"], []).append(k)

    # ── 활동(activity) : 이슈 이벤트를 인력별로 집계 ──
    def _build_activity(self):
        ev = {}
        def add(user, date_, time_, kind, key, summary):
            ev.setdefault(user, []).append({"date": date_, "time": time_, "kind": kind, "key": key, "summary": summary})
        for it in self.issues.values():
            add(it["reporter"], it["created"], it.get("tcreated"), "created", it["key"], it["summary"])
            for c in it["comments"]:
                add(c["author"], c["created"], it.get("tupdated"), "commented", it["key"], it["summary"])
            for w in it["worklog"]:
                add(w["author"], w["date"], it.get("tupdated"), "logged work", it["key"], it["summary"])
            if it["resolved"]:
                add(it["assignee"], it["resolved"], it.get("tresolved"), "resolved", it["key"], it["summary"])
                add(it["assignee"], it["resolved"], it.get("tresolved"), "transitioned", it["key"], it["summary"])
        for u in ev:
            ev[u].sort(key=lambda e: (e["date"].isoformat(), e.get("time") or ""), reverse=True)
        self.activity = ev

    def _build_confluence(self):
        conf = {}
        for module, ids in self.people.items():
            for uid in ids:
                rng = _rng("conf", uid)
                pages = []
                for _ in range(rng.randint(0, 4)):
                    _t = wc.conf_title(rng)
                    pages.append({"title": _t, "space": wc.conf_space(rng),
                                  "action": wc.conf_action(rng), "body": wc.conf_body(rng, _t),
                                  "date": self.today - timedelta(days=rng.randint(0, 13)),
                                  "time": "%02d:%02d" % (rng.randint(8, 19), rng.choice([0, 15, 30, 45]))})
                pages.sort(key=lambda p: p["date"], reverse=True)
                conf[uid] = pages
        self.confluence = conf

    # ── Jira REST 직렬화 (실 Jira DC 8.20.8 형태) ──
    def _status_obj(self, cat, name):
        jc = _JIRA_CAT[cat]
        sid = _STATUS_ID.get(name, "1")
        return {"self": f"/rest/api/2/status/{sid}", "description": "",
                "iconUrl": f"/images/icons/statuses/{jc['key']}.png",
                "name": name, "id": sid,
                "statusCategory": {"self": f"/rest/api/2/statuscategory/{jc['id']}",
                                   "id": jc["id"], "key": jc["key"],
                                   "colorName": jc["colorName"], "name": jc["name"]}}

    def _issuetype_obj(self, name):
        tid = _TYPE_ID.get(name, "0")
        slug = name.lower().replace(" ", "").replace("-", "")
        return {"self": f"/rest/api/2/issuetype/{tid}", "id": tid, "description": "",
                "iconUrl": f"/secure/viewavatar?avatarType=issuetype&avatarId=10300&type={slug}",
                "name": name, "subtask": name == SUBTASK_TYPE, "avatarId": 10300}

    def _user_obj(self, uid):
        u = self.users.get(uid, {"name": uid, "displayName": uid})
        n = u["name"]
        return {"self": f"/rest/api/2/user?username={n}",
                "name": n, "key": n, "emailAddress": f"{n}@example.com",
                "avatarUrls": {
                    "48x48": f"/secure/useravatar?ownerId={n}&avatarId=10122",
                    "32x32": f"/secure/useravatar?size=medium&ownerId={n}&avatarId=10122",
                    "24x24": f"/secure/useravatar?size=small&ownerId={n}&avatarId=10122",
                    "16x16": f"/secure/useravatar?size=xsmall&ownerId={n}&avatarId=10122"},
                "displayName": u["displayName"], "active": True, "timeZone": "Asia/Seoul"}

    def jira_fields(self, it):
        f = {
            "summary": it["summary"], "description": it["description"],
            "issuetype": self._issuetype_obj(it["type"]),
            "status": self._status_obj(it["statusCategory"], it["statusName"]),
            "assignee": self._user_obj(it["assignee"]),
            "reporter": self._user_obj(it["reporter"]),
            "components": [{"self": f"/rest/api/2/component/{self._comp_ids.get(it['component'], '0')}",
                            "id": self._comp_ids.get(it["component"], "0"), "name": it["component"]}],
            "labels": it["labels"],
            "created": self._dt(it["created"], it.get("tcreated")),
            "updated": self._dt(it["updated"], it.get("tupdated")),
            "resolutiondate": self._dt(it["resolved"], it.get("tresolved")) if it["resolved"] else None,
            "duedate": it["due"].isoformat() if it["due"] else None,
            "timespent": sum(wl.get("seconds", 0) for wl in it.get("worklog", [])) or None,   # 표준 Time Tracking(초)
            self.sp_field: it["sp"],
            self.epic_link_field: it["epicKey"],
        }
        if it["parentKey"]:
            p = self.issues.get(it["parentKey"])
            if p:
                f["parent"] = {"id": _iid(p["key"]), "key": p["key"], "fields": {
                    "summary": p["summary"],
                    "status": self._status_obj(p["statusCategory"], p["statusName"]),
                    "issuetype": self._issuetype_obj(p["type"])}}
        f["subtasks"] = [{"id": _iid(sk), "key": sk, "fields": {
            "summary": self.issues[sk]["summary"],
            "status": self._status_obj(self.issues[sk]["statusCategory"], self.issues[sk]["statusName"]),
            "issuetype": self._issuetype_obj(self.issues[sk]["type"])}}
            for sk in it["subtasks"] if sk in self.issues]
        return f

    def jira_issue(self, key):
        it = self.issues.get(key)
        return {"key": key, "fields": self.jira_fields(it)} if it else None

    def jira_comments(self, key):
        it = self.issues.get(key)
        if not it:
            return []
        out = []
        for i, c in enumerate(it["comments"]):
            au = self._user_obj(c["author"])
            when = self._dt(c["created"], c.get("tcreated"))
            out.append({"self": f"/rest/api/2/issue/{key}/comment/{key}-c{i}",
                        "id": f"{key}-c{i}", "author": au, "updateAuthor": au,
                        "body": f"({c['kind']}) {c['text']}",
                        "created": when, "updated": when})
        out.sort(key=lambda c: c["created"], reverse=True)
        return out


@lru_cache(maxsize=1)
def get_world():
    return World(load_plan(), load_people(), date.today())

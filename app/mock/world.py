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
import re
from datetime import date, timedelta
from functools import lru_cache

from app.mock import worldcontent as wc
from app.infra.settings import get_settings, load_people, load_plan

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
# UI 회귀 검증 픽스처 전용 모듈. 랜덤 생성기는 이 모듈을 건너뛰어,
# TEST 모듈에는 픽스처만 남는다(= 화면에서 바로 찾을 수 있다).
FIX_MODULE = "TEST"
# TEST 모듈 인력(= config/people.yaml 의 TEST). 실 인력과 섞이지 않게 별도 id.
FIX_USERS = ["test.ui01", "test.ui02"]
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


def _epic_short(summary):
    """요약에서 Epic 단축어를 만든다 — 앞 두 낱말 정도.

    실 Jira 의 Epic Name 은 사람이 직접 적는 값이라 규칙이 없다. dev 에서는 '요약과는 다른
    짧은 이름' 이라는 성질만 지키면 화면 검증에 충분하다(둘을 같이 보여 주는 게 이 값의 용도다).
    """
    s = re.sub(r"^\[[^\]]*\]\s*", "", summary or "").strip()
    w = s.split()
    return " ".join(w[:2]) if w else (s[:12] or "Epic")


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
        # 랜덤 생성 대상 모듈 — TEST 제외(픽스처만 남기기 위해)
        self.gen_modules = [m for m in self.modules if m != FIX_MODULE]
        # 컴포넌트 id 맵 (fake /project/{k}/components 의 id 규칙 100+idx 와 일치)
        self._comp_ids = {m: str(100 + i) for i, m in enumerate(self.modules + ["사용자 VoC"])}
        self.users = self._make_users()
        self.issues = {}                 # key -> canonical issue
        self.sprints = {}                # id -> 스프린트(jira820 store.sprints 형태)
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
        # ★ 픽스처는 맨 마지막 — 위 자동 생성기들이 픽스처 내용을 덮어쓰지 않게
        self._build_ui_fixtures()     # UI 회귀 검증용 Epic + 하위 티켓
        self._build_mytask_fixtures() # '내 Task' 화면 픽스처(담당 조합·Epic 없음·마감 초과)
        self._build_dataset_fixtures()  # 데이터셋 지식 픽스처(테이블 하나의 이력을 여러 티켓에 분산)
        self._priorities()            # 우선순위 — 픽스처가 지정한 것은 그대로 두고 나머지만 채운다
        self._sprints()               # 스프린트 — '스프린트 내 티켓만' 필터 검증용
        self._index()
        self._build_activity()
        self._build_confluence()
        self._build_dataset_docs()    # ★ _build_confluence 뒤 — 그쪽이 self.confluence 에 대입한다

    # ── 사용자 ──
    def _make_users(self):
        # 사내 관례: displayName = "{본명} {소속회사명}", id = "{회사코드}.{사번}"(x*=개발/i*=운영).
        # 본명·회사는 id 로 결정적 배정(같은 사람=항상 같은 이름). 회사명은 대부분 SKCC + 협력사 소수.
        users = {"pmo": {"name": "pmo", "displayName": "PMO Office"},
                 "lead": {"name": "lead", "displayName": "정한울 SKCC"}}
        # UI 픽스처 담당자는 이름을 고정한다 — 합성 본명을 주면 화면에서 실제 인력과 구분이 안 된다.
        for i, uid in enumerate(FIX_USERS):
            users[uid] = {"name": uid, "realName": f"UI픽스처{i + 1:02d}", "company": "TEST",
                          "displayName": f"UI픽스처{i + 1:02d} TEST"}
        taken = {"정한울"}                       # 본명 중복 방지(데모 가독성) — id 정렬로 결정적
        for uid in sorted({u for ids in self.people.values() for u in ids} - set(FIX_USERS)):
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
                if resolved > self.today:              # created==today 면 span=1 이라 내일이 된다
                    resolved = self.today
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
        # 코멘트·워크로그는 **티켓 생애주기(created ~ 종료/오늘)** 안에서 일어난다.
        # 예전엔 `today - 0~13일` / `0~7일` 고정이라, 반년 전에 만든 티켓에 2주 전 코멘트만
        # 달리는 시간 역전이 생겼다 — 과거 맥락을 읽는 소비자(요약·추천)가 쓸 수 없는 데이터다.
        life = max(((resolved or self.today) - created).days, 0)
        ncom = rng.randint(0, 4) if itype != SUBTASK_TYPE else rng.randint(0, 1)
        comments = []
        for (k, t) in wc.comments(rng, pool, ncom):
            # rng 호출 순서(choice→randint)를 원본과 동일하게 유지해 world 결정성 보존.
            author = rng.choice(pool + ["pmo", "lead"])
            ccreated = created + timedelta(days=rng.randint(0, life))
            # 시각은 rng 를 쓰지 않고 결정적으로 파생(업무시간대) → world 시퀀스 불변.
            hh = 9 + (ccreated.toordinal() + len(comments)) % 9
            mm = ((ccreated.toordinal() * 3 + len(comments) * 7) % 6) * 10
            comments.append({"author": author, "kind": k, "text": t, "body": t,
                             "created": ccreated, "tcreated": "%02d:%02d" % (hh, mm)})
        comments.sort(key=lambda c: (c["created"], c["tcreated"]))   # 스레드는 시간순으로 읽힌다
        worklog = []
        if cat != "todo":
            for _ in range(rng.randint(0, 3)):
                worklog.append({"author": assignee,
                                "date": created + timedelta(days=rng.randint(0, life)),
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

        summary_txt = summary or self._summary(rng, itype, module)
        self.issues[key] = {
            "key": key, "project": self.project, "type": itype,
            "summary": summary_txt,
            "description": wc.description(rng, itype, reporter),
            "module": module, "component": component or module,
            "assignee": assignee, "reporter": reporter,
            "statusCategory": cat, "statusName": status_name,
            "labels": labels, "sp": sp,
            "epicKey": epic_key if itype != "Epic" else None,
            # Epic Name = 보드 칸에 들어가는 **단축어**. 요약(summary)은 문장이라 칸에 안 들어간다.
            # 실 Jira 에서도 둘은 별개 필드이고, 사람들은 보통 이 단축어로 Epic 을 부른다.
            "epicName": _epic_short(summary_txt) if itype == "Epic" else None,
            "parentKey": parent_key,
            "created": created, "updated": updated, "resolved": resolved, "due": due,
            "tcreated": _tm(), "tresolved": _tm(), "tupdated": _tm(),
            "comments": comments, "worklog": worklog, "subtasks": [], "changelog": changelog,
            "links": [], "remotelinks": [],
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
            if module == FIX_MODULE:       # 픽스처 Epic — _build_ui_fixtures 가 만든다
                continue
            rng = _rng("epic", ekey)
            self._make_issue(rng, "Epic", module, epic_key=ekey)   # Epic 이름은 생성 풀에서(=Jira)
            for _ in range(rng.randint(4, 9)):
                ct = rng.choices(_CHILD_TYPES, weights=_CHILD_TYPES_W)[0]
                ck = self._make_issue(rng, ct, module, epic_key=ekey)
                if ct in _STORYLIKE and rng.random() < 0.4:
                    self._add_subtasks(rng, ck, module, rng.randint(1, 2))

    # ── WBS 밖 일반 Epic (현안 아님) + 자식 ──
    def _build_extra_epics(self):
        for module in self.gen_modules:
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
        for module in self.gen_modules:
            pool = self._pool(module)
            rng = _rng("solo", module)
            for i in range(rng.randint(8, 16)):
                t = rng.choices(_CHILD_TYPES, weights=_CHILD_TYPES_W)[0]
                k = self._make_issue(rng, t, module, assignee=pool[i % len(pool)])
                if t in _STORYLIKE and rng.random() < 0.35:
                    self._add_subtasks(rng, k, module, rng.randint(1, 2))

    # ── 지난 12개월에 생성·종료된 과거 완료 이슈 (대량, Closed/Resolved) ──
    def _build_history(self):
        # 오늘 기준 12개월 창. 예전엔 date(2026,1,1) 하드코딩이라 (a) 해가 바뀌면 창이 어긋나고
        # (b) 올해 계획 이전의 이력이 없어 "작년에 비슷한 시도가 있었다" 를 찾을 수 없었다.
        # 과거 맥락을 뒤지는 소비자에게는 이 구간이 본편이다.
        start = self.today - timedelta(days=365)
        for module in self.gen_modules:
            pool = self._pool(module)
            rng = _rng("hist", module)
            for i in range(rng.randint(12, 24)):
                created = start + timedelta(days=rng.randint(0, 300))      # 12개월 전 ~ 2개월 전
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
                        # 하위 이슈는 부모 구간(created~resolved) 안에서 시작해야 한다.
                        # 시작을 +0~15일로 고정하면 부모가 5일 만에 끝난 경우 부모 완료일보다
                        # 늦게 시작 = 생성 전에 완료된 것으로 잡힌다.
                        _room = max((resolved - created).days - 1, 0)
                        scr = created + timedelta(days=min(rng.randint(0, 15), _room))
                        srv = min(scr + timedelta(days=rng.randint(3, 60)), resolved)
                        sk = self._make_issue(rng, SUBTASK_TYPE, module, parent_key=k,
                                              assignee=pool[i % len(pool)], created=scr, resolved=srv)
                        self.issues[k]["subtasks"].append(sk)

    # ── PMO_VIT 현안 (모듈별 다양 개수, 조상/자손 dedup 케이스 포함) ──
    def _build_vit(self):
        dedup_done = False
        for mi, module in enumerate(self.gen_modules):
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
        made = []
        for module in self.gen_modules:
            for pid in self._pool(module):
                rng = _rng("voc", pid)
                for _ in range(rng.randint(0, 5)):
                    k = self._make_issue(rng, rng.choice(["Task", "Bug", "Story"]), module,
                                         component="사용자 VoC", assignee=pid)
                    if k:
                        made.append(k)          # _make_issue 는 **키**를 돌려준다
        # VoC 는 보통 Epic 이 없지만 **가끔 Epic 이 배정된다**. 그때는 그 Epic 소속으로 세야 하므로
        # (워크로드 Epic 분포) 그 경우를 데이터에 심어 둔다.
        # ★ rng 미사용 — 키 해시로 결정적으로 고른다(world 시퀀스 불변).
        epics_of = {}
        for k, x in sorted(self.issues.items()):
            if x["type"] == "Epic" and x.get("module"):
                epics_of.setdefault(x["module"], []).append(k)
        for k in made:
            it = self.issues[k]
            cands = epics_of.get(it["module"]) or []
            if cands and sum(map(ord, k)) % 3 == 0:              # 대략 1/3
                it["epicKey"] = cands[sum(map(ord, k)) % len(cands)]

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

    # ── UI 회귀 검증 픽스처 ──────────────────────────────────────────
    # 목적: UI 를 손볼 때마다 "이 케이스를 볼 수 있는 티켓"을 world 에서 뒤지지 않도록,
    #       검증 포인트별 티켓을 고정 키(DL-9000~)로 만들어 둔다. 제목이 곧 검증 항목이다.
    # 격리: wbs_config 에 없는 Epic + PMO_VIT 라벨 없음 + people.yaml 밖 담당자
    #       → WBS·현안·워크로드 집계에 섞이지 않는다(검색·티켓 다이얼로그에서만 보임).
    UI_EPIC = "DL-9000"

    def _fx(self, key, itype, summary, **over):
        """픽스처 이슈 한 건 — 기본값 채우고 over 로 덮어쓴다(rng 미사용 = world 시퀀스 불변)."""
        d0 = self.today - timedelta(days=30)
        it = {
            "key": key, "project": self.project, "type": itype, "summary": summary,
            "description": "", "module": FIX_MODULE, "component": FIX_MODULE,
            "assignee": FIX_USERS[0], "reporter": "lead",
            "statusCategory": "inprogress", "statusName": "In Progress",
            "labels": ["ui-fixture"], "sp": None,
            "epicKey": None if itype == "Epic" else self.UI_EPIC, "parentKey": None,
            # Epic Name(단축어) — 안 채우면 목록에서 요약이 그대로 이름 자리에 앉아, 단축어와
            # 요약을 나란히 보여 주는 화면이 같은 글자를 두 번 그린다(구별에 아무 도움이 안 된다).
            "epicName": _epic_short(summary) if itype == "Epic" else None,
            "created": d0, "updated": self.today, "resolved": None, "due": None,
            "tcreated": "09:00", "tupdated": "18:00", "tresolved": None,
            "comments": [], "worklog": [], "subtasks": [], "changelog": [],
            "links": [], "attachments": [], "remotelinks": [],
        }
        it.update(over)
        self.issues[key] = it
        return it

    def _fx_attach(self, key, filename, mime, data, created=None, size=None):
        aid = f"fx{_iid(key)}{len(self.issues[key]['attachments'])}"
        self.attachments[aid] = {
            "id": aid, "issueKey": key, "filename": filename, "mimeType": mime,
            "size": size or len(data), "data": data,
            "author": self.issues[key]["assignee"],
            "created": created or self.issues[key]["created"],
        }
        self.issues[key]["attachments"].append(aid)

    def _build_ui_fixtures(self):
        conf = "https://confluence.corp.example"
        self._fx(self.UI_EPIC, "Epic", "[UI] UI 회귀 검증 픽스처",
                 epicName="UI 회귀",
                 labels=["ui-fixture", "PMO_VIT"],
                 description=("UI 를 수정할 때 **검증 포인트별로** 열어볼 티켓 모음이다.\n"
                              "하위 티켓 제목이 곧 검증 항목이므로, 화면을 고친 뒤 해당 티켓만 열어 확인하면 된다.\n\n"
                              "{info}\n찾는 법: WBS 의 *TEST* 모듈 · 현안(PMO_VIT) 목록 · 인력워크로드의 *TEST* 모듈.\n"
                              "이 모듈에는 랜덤 데이터가 없어 픽스처만 보인다.\n{info}"))

        # 1) 설명 리치 요소 총집합
        self._fx("DL-9001", "Task", "[UI] 설명 리치요소 — 표·코드·인용·패널·콜아웃·이미지·링크",
                 description=(
                     "h2. 표\n"
                     "||모듈||역할||가중치||\n|Ingestion|수집|3|\n|Catalog|메타|2|\n\n"
                     "h2. 코드\n{code}\nmake deploy ENV=staging\n{code}\n\n"
                     "h2. 인용\n{quote}\n인용은 따옴표 글리프로 구분된다(색만으로 구분하지 않는다).\n{quote}\n\n"
                     "h2. 패널/콜아웃\n{panel:title=완료 기준}\n* 하위 SP 롤업 100%\n{panel}\n"
                     # 6종 전부 — 타입별 아이콘이 실제로 다르게 나오는지 한 화면에서 본다
                     "{note}\n note — 연필 아이콘\n{note}\n"
                     "{info}\n info — 원 안 i\n{info}\n"
                     "{tip}\n tip — 전구\n{tip}\n"
                     "{success}\n success — 전구(tip 과 동일 계열)\n{success}\n"
                     "{warning}\n warning — 느낌표 삼각형\n{warning}\n"
                     "{error}\n error — 원 안 X\n{error}\n\n"
                     "h2. 이미지\n!ticket-sample.svg!\n\n"
                     "h2. 링크\n티켓 [DL-5003|" + conf.replace('confluence', 'jira') + "/browse/DL-5003], "
                     "문서 [설계 노트|" + conf + "/spaces/DL/pages/42013/설계+노트], 멘션 [~pmo]."))

        # 2) Heading 레벨 구분
        self._fx("DL-9002", "Task", "[UI] Heading 1~4 레벨 구분 바",
                 description="h1. H1 제목\n본문\n\nh2. H2 제목\n본문\n\nh3. H3 제목\n본문\n\nh4. H4 제목\n본문")

        # 3) 긴 제목 말줄임
        self._fx("DL-9003", "Task",
                 "[UI] 아주 긴 제목 말줄임 확인 — " + ("가나다라마바사아자차카타파하 " * 6).strip(),
                 description="제목이 헤더·계보·형제·검색 결과에서 어떻게 잘리는지 본다.")

        # 4) 관련 Task — 서술형 링크 문구 + 언급 티켓
        self._fx("DL-9004", "Task", "[UI] 관련 Task — 서술형 링크문구 축약 + 본문 언급 티켓",
                 description=("사내 Jira 는 링크 문구를 서술형으로 준다. 배지가 'blocks' 로 짧게 나와야 한다.\n"
                              "본문 언급: [DL-5005|" + conf.replace('confluence', 'jira') + "/browse/DL-5005]"),
                 links=[{"type": "Blocks", "dir": "outward", "key": "DL-9005"},
                        {"type": "Duplicate", "dir": "inward", "key": "DL-9006"},
                        {"type": "Relates", "dir": "outward", "key": "DL-9001"}])

        # 5) 관련문서 — 초안(edit) 링크 · 같은 문서 중복 URL · display 형태
        self._fx("DL-9005", "Task", "[UI] 관련문서 — 편집(초안) 링크 · 중복 URL · display 형태",
                 description=(
                     "초안(편집) 링크: [배포 계획서|" + conf + "/pages/resumedraft.action?draftId=98765&draftShareId=a-b]\n"
                     "같은 문서 3형태(1건으로 합쳐져야 함):\n"
                     "* [설계 노트|" + conf + "/spaces/DL/pages/42013/설계+노트]\n"
                     "* [제목이 바뀐 링크|" + conf + "/spaces/DL/pages/42013/changed-title?src=nav]\n"
                     "* [구형 URL|" + conf + "/pages/viewpage.action?pageId=42013#s1]\n"
                     "display 형태: [운영 가이드|" + conf + "/display/DL/운영+가이드]"),
                 # Jira remote link — 본문 언급과 별개 경로. '설계 노트'는 본문에도 있어 중복 제거 대상,
                 # Web link 는 새로 추가돼야 한다.
                 remotelinks=[
                     {"url": conf + "/spaces/DL/pages/42013/설계+노트", "title": "설계 노트(remote)",
                      "application": {"type": "com.atlassian.confluence", "name": "Confluence"}},
                     {"url": "https://wiki.corp.example/runbook/deploy", "title": "배포 런북 (Web)"},
                 ])

        # 6) 첨부파일
        fx6 = self._fx("DL-9006", "Task", "[UI] 첨부파일 — 이미지·문서·큰 용량",
                       description="첨부 칩의 파일명·업로드일시·용량 표기와 이미지/문서 아이콘 구분을 본다.")
        self._fx_attach("DL-9006", "화면_시안.png", "image/png", self._PNG_1X1)
        self._fx_attach("DL-9006", "배포_절차서.md", "text/markdown", b"# deploy\nsteps\n" * 40)
        # 큰 용량 '표기' 검증 — 실제 바이트를 들고 있을 이유가 없어 size 만 크게 준다
        self._fx_attach("DL-9006", "성능_측정_대용량.csv", "text/csv",
                        b"a,b,c\n" * 20, size=2_487_193)
        fx6["updated"] = self.today

        # 7) 코멘트 다수
        self._fx("DL-9007", "Task", "[UI] 코멘트 다수 — 멘션·문서링크·긴 본문",
                 assignee=FIX_USERS[1],
                 description="코멘트 영역의 아바타·간격·링크 뱃지를 본다.",
                 comments=[{"author": a, "kind": "note", "text": t, "body": t,
                            "created": self.today - timedelta(days=i),
                            "tcreated": "1%d:0%d" % (i % 10, i % 10)}
                           for i, (a, t) in enumerate([
                               ("pmo", "리뷰 부탁드립니다. [~lead] 확인 후 회신 주세요."),
                               ("lead", "문서 참고: [설계 노트|" + conf + "/spaces/DL/pages/42013/설계+노트]"),
                               ("pmo", "관련 티켓 [DL-9001|" + conf.replace('confluence', 'jira') + "/browse/DL-9001] 도 함께 보세요."),
                               ("lead", "긴 본문 확인용. " + ("문장이 길어질 때 줄바꿈과 여백을 본다. " * 12)),
                           ])])

        # 8) 마감 초과 / 임박
        self._fx("DL-9008", "Task", "[UI] 마감 초과(D+) — 기한 붉은 강조",
                 due=self.today - timedelta(days=9), statusCategory="inprogress", statusName="In Progress")
        self._fx("DL-9009", "Task", "[UI] 마감 임박(D-2)", assignee=FIX_USERS[1],
                 due=self.today + timedelta(days=2))

        # 9) 타임라인 — 담당자/상태 변경 이력
        self._fx("DL-9010", "Task", "[UI] 타임라인 — 담당자 변경·상태 변경·해결 이력",
                 assignee=FIX_USERS[1],
                 statusCategory="done", statusName="Resolved",
                 resolved=self.today - timedelta(days=3), tresolved="16:20",
                 changelog=[
                     {"author": FIX_USERS[0], "date": self.today - timedelta(days=25), "time": "10:00",
                      "items": [{"field": "assignee", "fieldtype": "jira",
                                 "from": FIX_USERS[0], "fromString": self._dn(FIX_USERS[0]),
                                 "to": FIX_USERS[1], "toString": self._dn(FIX_USERS[1])}]},
                     {"author": FIX_USERS[1], "date": self.today - timedelta(days=20), "time": "11:30",
                      "items": [{"field": "status", "fieldtype": "jira", "from": "1",
                                 "fromString": "Open", "to": "3", "toString": "In Progress"}]},
                     {"author": FIX_USERS[1], "date": self.today - timedelta(days=18), "time": "09:40",
                      "items": [{"field": "description", "fieldtype": "jira",
                                 "from": None, "fromString": "(이전)", "to": None, "toString": "(수정)"}]},
                     # 마감일 변경 — world 전체에 duedate 이벤트가 0건이라 타임라인 표기를 검증할 수 없었다
                     {"author": FIX_USERS[0], "date": self.today - timedelta(days=12), "time": "14:05",
                      "items": [{"field": "duedate", "fieldtype": "jira",
                                 "from": "2026-07-31", "fromString": "2026-07-31",
                                 "to": "2026-08-14", "toString": "2026-08-14"}]},
                     {"author": FIX_USERS[1], "date": self.today - timedelta(days=3), "time": "16:20",
                      "items": [{"field": "status", "fieldtype": "jira", "from": "3",
                                 "fromString": "In Progress", "to": "5", "toString": "Resolved"},
                                {"field": "resolution", "fieldtype": "jira",
                                 "from": None, "fromString": None, "to": "1", "toString": "Done"}]},
                 ])

        # 10) 라벨/컴포넌트 다수 · 미할당 · 설명 없음
        self._fx("DL-9011", "Task", "[UI] 라벨 다수 + 미할당 + 설명 없음",
                 # 줄바꿈까지 유발할 만큼 — 접혔을 때 '라벨' 키가 첫 줄에 붙는지 확인용
                 labels=["ui-fixture", "mock", "backend", "hotfix", "needs-review",
                         "long-label-example", "regression", "ui-verification",
                         "another-fairly-long-label", "q3-2026", "pmo-review"],
                 assignee=None, description="")

        # 12) 영역 구분선 — "=== 제목 ===" 로 설명을 여러 영역 카드로 쪼개는 기능.
        #     표/코드블럭/인용/리스트 안의 구분선은 무시돼야 하므로 함께 담아 둔다.
        #     줄 목록을 join 한다 — 소스에 리터럴 백슬래시가 섞이면 렌더가 통째로 깨진다.
        self._fx("DL-9017", "Task", "[UI] 영역 구분선(=== 제목 ===) 으로 설명 분할",
                 description="\n".join([
                     "안녕하세요",
                     "==== 신청정보 ====",
                     "이렇게 신청함",
                     "==== 부가정보 ======",
                     "부가적임",
                     "",
                     "=== 리치 요소 포함 영역 ===",
                     "||항목||값||",
                     "|기한|2026-08-01|",
                     "",
                     "{info}",
                     "영역 안에서도 콜아웃은 그대로.",
                     "{info}",
                     "",
                     "=== 아래는 무시되어야 하는 것들 ===",
                     "표·코드블럭 안의 구분선은 영역을 나누면 안 된다.",
                     "",
                     "||표 안||",
                     "|=== 표안 구분선 ===|",
                     "",
                     "{code}",
                     "=== 코드블럭 안 구분선 ===",
                     "{code}",
                     "{quote}",
                     "=== 인용 안 구분선 ===",
                     "{quote}",
                     "* === 리스트 안 구분선 ===",
                     "",
                     "== 등호 2개는 구분선이 아니다 ==",
                     "여기까지 마지막 영역.",
                 ]))

        # 13) VoC 시스템 주입 블록 — 영역 내용이 전부 'key : value' 면 표로 그린다.
        #     제목이 아니라 **내용 모양**으로 판정하므로 {%d} 번호 변형도 걸린다.
        #     ★ 'N 시스템정보' + 'N 테이블정보' 는 짝 → 한 행 2단으로 합쳐진다.
        #       (실 데이터는 중괄호 없이 '1 시스템정보' 형태다)
        #     ★ 마지막 영역도 kv 블록으로 둔다(끝 영역만 표가 안 되던 회귀 방지).
        self._fx("DL-9018", "Task", "[UI] VoC 주입 블록 — key : value 표 + 시스템/테이블 페어",
                 component="사용자 VoC",
                 description="\n".join([
                     "VoC 접수 건입니다. 아래는 시스템이 주입한 블록입니다.",
                     "",
                     "==================== 신청정보 ====================",
                     "신청자 : 홍길동 SKCC",
                     "요청 부서 : 데이터플랫폼",
                     "희망 완료일 : 2026-08-14",
                     "",
                     "==================== 요청내용 ====================",
                     "안녕하세요. 아래 테이블 적재 주기 변경 요청드립니다.",
                     "",
                     "==================== 1 시스템정보 ====================",
                     "시스템명 : LAKE Data Platform",
                     "URL : http://lake.corp.example:8080/dashboard",
                     "환경 : 운영",
                     "",
                     "==================== 1 테이블정보 ====================",
                     "스키마 : DW_MART",
                     "테이블명 : FCT_ORDER_DAILY",
                     "적재주기 : 일 1회 (02:00)",
                     "",
                     "==================== 2 시스템정보 ====================",
                     "시스템명 : MART Serving",
                     "환경 : 개발",
                     "",
                     "==================== 2 테이블정보 ====================",
                     "스키마 : MT_CORE",
                     "테이블명 : DIM_CUSTOMER",
                     "적재주기 : 월 1회",
                     "보존기간 : 60개월",
                 ]))

        # 11) Sub-Task 세트 — 설명 없음(상위 설명 자동 펼침) + 형제 목록
        # 부모에만 마감이 있다(하위 4개는 전부 마감 없음) — 하위가 부모 마감을 물려받아
        # 표시하는지(↑ 표식) 확인하는 조합. 실무에서 마감은 대개 부모에만 걸린다.
        parent = self._fx("DL-9012", "Task", "[UI] Sub-Task 부모 — 형제 목록/하위 마감 상속",
                          due=self.today + timedelta(days=5),
                          description=("h3. 부모 설명\n이 설명이 하위 Sub-Task 의 '상위 티켓 설명' 에 나와야 한다.\n"
                                       "{info}\n상위 설명 자동 펼침은 DL-9013 에서 확인.\n{info}"))
        subs = [
            ("DL-9013", "[UI] Sub-Task 설명 없음 — 상위 설명 자동 펼침", "", "todo", "Open"),
            ("DL-9014", "[UI] Sub-Task 설명 있음 — 상위 설명 접힘", "자체 설명이 있으므로 접혀 있어야 한다.", "inprogress", "In Progress"),
            ("DL-9015", "[UI] Sub-Task 완료 — 형제 정렬(완료 뒤로)", "완료 상태.", "done", "Resolved"),
            ("DL-9016", "[UI] Sub-Task 미착수", "", "todo", "Open"),
        ]
        for k, title, desc, cat, st in subs:
            self._fx(k, SUBTASK_TYPE, title, description=desc, parentKey="DL-9012",
                     epicKey=None, statusCategory=cat, statusName=st,
                     resolved=(self.today - timedelta(days=2)) if cat == "done" else None,
                     tresolved="15:00" if cat == "done" else None)
            parent["subtasks"].append(k)

    # ── '내 Task' 화면 픽스처 (DL-9020~) ────────────────────────────────────
    # 이 화면은 "내가 담당한 것"이 Task 일 수도 Sub-Task 일 수도 있고, 하위에 남의 것이 섞이고,
    # Epic 이 없을 수도 있고, 마감이 지났을 수도 있다 — 그 조합이 전부 있어야 검증이 된다.
    # 세션 사용자(myself)는 FIX_USERS[0] 로 맞춰 둔다(fakebridge 의 current_user).
    ME = FIX_USERS[0]
    MATE = FIX_USERS[1]

    MY_EPIC = "DL-9019"

    def _build_mytask_fixtures(self):
        d = self.today
        # 전용 Epic — UI 회귀 Epic(DL-9000)에 섞으면 그쪽 검증이 흐려진다.
        self._fx(self.MY_EPIC, "Epic", "[내Task] 내 Task 화면 픽스처",
                 epicName="내 Task 화면", epicKey=None)

        def due(days):
            return d + timedelta(days=days)

        # 1) 내 Task + 하위에 내 것과 남의 것이 섞임 (동료 서브 집계 칩 검증)
        p1 = self._fx("DL-9020", "Task", "[내Task] 내 Task — 하위에 동료 Sub 섞임",
                      assignee=self.ME, priority="P1-Critical", due=due(3), epicKey=self.MY_EPIC,
                      description="담당=나. 하위 4개 중 2개가 동료 것.")
        for k, t, who, cat, st, pri, dd in [
            ("DL-9021", "[내Task] 내 Sub — 완료", self.ME, "done", "Resolved", "P2-Major", -2),
            ("DL-9022", "[내Task] 내 Sub — 진행중·임박", self.ME, "inprogress", "In Progress", "P1-Critical", 2),
            ("DL-9023", "[내Task] 동료 Sub — 진행중", self.MATE, "inprogress", "In Progress", "P2-Major", 4),
            ("DL-9024", "[내Task] 동료 Sub — 미착수", self.MATE, "todo", "Open", "P3-Minor", 8),
        ]:
            self._fx(k, SUBTASK_TYPE, t, parentKey="DL-9020", epicKey=None, assignee=who,
                     statusCategory=cat, statusName=st, priority=pri, due=due(dd),
                     resolved=(d - timedelta(days=2)) if cat == "done" else None,
                     tresolved="15:00" if cat == "done" else None)
            p1["subtasks"].append(k)

        # 2) 남의 Task 인데 내가 Sub 담당 — 상위 진척 시각화 검증
        p2 = self._fx("DL-9025", "Task", "[내Task] 남의 Task — 내가 Sub 만 담당",
                      assignee=self.MATE, priority="P2-Major", due=due(10), epicKey=self.MY_EPIC,
                      description="담당=동료. 이 안에서 내 몫만 뽑혀 보여야 한다.")
        for k, t, who, cat, st, pri, dd in [
            ("DL-9026", "[내Task] 내 Sub — 리뷰 대기·내일 마감", self.ME, "inprogress", "In Progress", "P2-Major", 1),
            ("DL-9027", "[내Task] 동료 Sub — 완료", self.MATE, "done", "Resolved", "P2-Major", -5),
        ]:
            self._fx(k, SUBTASK_TYPE, t, parentKey="DL-9025", epicKey=None, assignee=who,
                     statusCategory=cat, statusName=st, priority=pri, due=due(dd),
                     resolved=(d - timedelta(days=5)) if cat == "done" else None,
                     tresolved="15:00" if cat == "done" else None)
            p2["subtasks"].append(k)

        # 3) Epic 없는 내 Task ('Epic 없음'을 일급 상태로 다루는지) + 마감 초과
        self._fx("DL-9028", "Task", "[내Task] Epic 없는 내 Task — 마감 초과",
                 assignee=self.ME, epicKey=None, priority="P1-Critical", due=due(-1),
                 statusCategory="inprogress", statusName="In Progress",
                 description="Epic 미지정 + D+1. 목록 최상단에 와야 한다.")
        # 4) Epic 없는 내 Task — 오늘 마감, 하위 없음
        self._fx("DL-9029", "Task", "[내Task] Epic 없는 내 Task — 오늘 마감",
                 assignee=self.ME, epicKey=None, priority="P3-Minor", due=due(0),
                 statusCategory="todo", statusName="Open")
        # 5) 마감이 아예 없는 내 Task (정렬에서 맨 뒤로 밀리는지)
        self._fx("DL-9030", "Task", "[내Task] 마감 없는 내 Task",
                 # 마감도 우선순위도 없는 티켓 — 'Unclassified' 표시와 맨 뒤 정렬을 함께 검증
                 assignee=self.ME, priority="Unclassified", due=None, epicKey=self.MY_EPIC,
                 statusCategory="todo", statusName="Open")
        # 6) 내가 등록(reporter)했지만 담당은 동료 — '내가 등록' 스코프에서만 보여야 한다
        self._fx("DL-9032", "Task", "[내Task] 내가 등록·담당은 동료 — reporter 스코프 검증",
                 assignee=self.MATE, reporter=self.ME, epicKey=self.MY_EPIC,
                 priority="P1-Critical", due=due(5), statusCategory="todo", statusName="Open")
        # 7) 축 필터 검증 — 이 두 건이 없으면 '2주 내 갱신'·'1달' 필터가 늘 같은 결과라 확인이 안 된다.
        self._fx("DL-9033", "Task", "[내Task] 오래 방치된 할당 — '2주 내 갱신' 에서 빠져야",
                 assignee=self.ME, epicKey=self.MY_EPIC, priority="P3-Minor", due=due(20),
                 statusCategory="todo", statusName="Open",
                 created=d - timedelta(days=120), updated=d - timedelta(days=60))
        self._fx("DL-9034", "Task", "[내Task] 20일 전 완료 — '1주' 엔 없고 '1달' 엔 있어야",
                 assignee=self.ME, epicKey=self.MY_EPIC, priority="P2-Major", due=due(-20),
                 statusCategory="done", statusName="Resolved",
                 created=d - timedelta(days=60), updated=d - timedelta(days=20),
                 resolved=d - timedelta(days=20), tresolved="15:00")
        # 8) Epic 없는 사용자 VoC — 'Epic 없음' 이 아니라 **전용 Epic('사용자 VoC')** 으로 묶이고
        #    자기 시그니처 색·뱃지를 가져야 한다. 이게 없으면 그 규칙을 화면에서 확인할 수 없다.
        self._fx("DL-9035", "Task", "[내Task] Epic 없는 사용자 VoC — 전용 Epic 취급",
                 assignee=self.ME, epicKey=None, component="사용자 VoC",
                 priority="P2-Major", due=due(4), statusCategory="todo", statusName="Open")
        # 10) 첨부 파일 종류 — 뱃지 라벨·색(언어 포함)을 눈으로 확인하는 자리.
        #     확장자별 색을 고치면 여기부터 열어라. 코드·문서·표·압축·미디어를 한 티켓에 모아 둔다.
        #     ★ 본문/코멘트에도 [^파일명] 으로 넣는다 — 첨부 목록의 칩과 **본문 안의 칩**은 렌더
        #       경로가 달라(목록=프론트, 본문=서버 HTML) 한쪽만 맞고 다른 쪽이 틀린 적이 있다.
        self._fx("DL-9036", "Task", "[UI] 첨부 파일 종류 — 확장자 뱃지·언어 색 확인",
                 assignee=self.ME, priority="P2-Major", due=due(6),
                 statusCategory="inprogress", statusName="In Progress",
                 description=("h3. 산출물\n"
                              "설계서 [^아키텍처설계.pdf] · 일정 [^배포일정.xlsx] · 보고 [^주간보고.docx]\n"
                              "코드 [^ingest.py] · [^rollup.ts] · [^Schema.java] · [^migrate.sql]\n"
                              "설정 [^values.yaml] · [^pipeline.json] · 로그 [^error.log]\n"
                              "묶음 [^snapshot.zip] · 화면 [^flow.png]"))
        for fn, mime in [
            ("아키텍처설계.pdf", "application/pdf"),
            ("배포일정.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("주간보고.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            ("발표자료.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
            ("요건정의.hwp", "application/x-hwp"),
            ("ingest.py", "text/x-python"),
            ("rollup.ts", "text/typescript"),
            ("chart.js", "text/javascript"),
            ("Schema.java", "text/x-java"),
            ("worker.go", "text/x-go"),
            ("migrate.sql", "application/sql"),
            ("run.sh", "application/x-sh"),
            ("values.yaml", "text/yaml"),
            ("pipeline.json", "application/json"),
            ("index.html", "text/html"),
            ("theme.css", "text/css"),
            ("error.log", "text/plain"),
            ("측정치.csv", "text/csv"),
            ("snapshot.zip", "application/zip"),
            ("flow.png", "image/png"),
            ("demo.mp4", "video/mp4"),
        ]:
            self._fx_attach("DL-9036", fn, mime, b"x", size=1024 * (7 + len(fn)))
        self.issues["DL-9036"]["comments"] = [{
            "author": self.ME,
            "body": ("리뷰 부탁드립니다. 코드는 [^ingest.py] 와 [^rollup.ts] 이고, "
                     "설계는 [^아키텍처설계.pdf] 입니다.\n"
                     "결과 데이터는 [^측정치.csv] · 묶음은 [^snapshot.zip] 에 있습니다."),
            "created": d - timedelta(days=1), "time": "10:20",
        }]

        # 9) 완료된 내 Task — 기본 목록에서 빠져야 한다
        self._fx("DL-9031", "Task", "[내Task] 완료된 내 Task — 기본 목록에서 제외",
                 assignee=self.ME, priority="P2-Major", due=due(-3), epicKey=self.MY_EPIC,
                 statusCategory="done", statusName="Resolved",
                 resolved=d - timedelta(days=1), tresolved="15:00")

    # ── 데이터셋 지식 픽스처 (DL-9040~) ────────────────────────────
    # 목적: "이 테이블 지금 적재주기가 몇이지?" 처럼 **한 티켓에 답이 없는** 질문을 만든다.
    #       답의 조각을 VoC 요청 / Job 개발 / 장애 / 주기 변경 changelog / 스키마 변경 /
    #       Confluence 분석 문서 / **다른 테이블 티켓의 코멘트** 에 일부러 흩어 둔다.
    #       에이전트가 이걸 모아 답하는지가 지식 추론 검증의 과제다.
    # 격리: 자체 Epic(DL-9040) — DL-9000 자식은 '[UI]' 접두어가 강제된다(test_ui_fixtures).
    #       WBS 미등록 + PMO_VIT 없음 → 진척·현안 집계에 안 섞인다. 단 **모듈·담당은 실제**다
    #       ("적재 job 작업자가 누구냐"에 답하려면 people.yaml 의 실 인력이어야 한다).
    # ★ rng 미사용 → world 시퀀스 불변.
    DATA_EPIC = "DL-9040"
    CONF_BASE = "https://confluence.corp.example"

    def _conf_url(self, title, space="DL"):
        """Confluence 페이지 URL — id 는 jira820 이 (title, space) 해시로 **파생**시킨다.
        world 가 id 를 정할 수 없으므로 티켓의 remotelink 는 같은 규칙으로 계산해 맞춘다."""
        pid = int(hashlib.md5(f"{title}|{space}".encode("utf-8")).hexdigest()[:8], 16)
        return f"{self.CONF_BASE}/spaces/{space}/pages/{pid}/{title.replace(' ', '+')}"

    def _cmt(self, author, text, days_ago, time_="14:20"):
        """픽스처 코멘트 한 건 — DL-9007 과 같은 6키 형태.
        kind/text 를 빠뜨리면 jira_comments() 직렬화가 KeyError 로 죽는다(DL-9036 의 전례)."""
        return {"author": author, "kind": "note", "text": text, "body": text,
                "created": self.today - timedelta(days=days_ago), "tcreated": time_}

    def _chg(self, author, days_ago, field, before, after, time_="10:00"):
        """필드 변경 이력 한 건. 적재주기·스키마처럼 **사내 데이터 속성**도 여기 남는다 —
        UI 타임라인의 allow-list(TIMELINE_FIELDS)에는 없지만 changelog 원문에는 있다."""
        return {"author": author, "date": self.today - timedelta(days=days_ago), "time": time_,
                "items": [{"field": field, "fieldtype": "jira",
                           "from": None, "fromString": before, "to": None, "toString": after}]}

    def _build_dataset_fixtures(self):
        d = self.today
        E = self.DATA_EPIC
        self._fx(E, "Epic", "[데이터] 데이터셋 카탈로그 지식 픽스처",
                 module="ETL", component="ETL", assignee="skcc.x1042", reporter="lead",
                 labels=["dataset-fixture"],
                 description="테이블 단위 지식이 여러 티켓·문서에 흩어져 있는 상황을 재현한다.")

        def fx(key, itype, summary, tbl, **over):
            over.setdefault("epicKey", E)
            over.setdefault("labels", ["dataset-fixture", f"tbl-{tbl}"])
            return self._fx(key, itype, summary, **over)

        # ── ① fdc.fdc_trace_summary_ic — 최고 밀도(조각 7개, 모듈 ETL) ──
        t1 = "fdc_trace_summary_ic"
        fx("DL-9041", "Task", "[VoC] FDC trace 요약(fdc.fdc_trace_summary_ic) 신규 적재 요청", t1,
           component="사용자 VoC", module="사용자 VoC",
           assignee="skcc.x1042", reporter="lead",
           statusCategory="done", statusName="Closed",
           created=d - timedelta(days=210), resolved=d - timedelta(days=180),
           tresolved="17:30", updated=d - timedelta(days=180),
           # 실 VoC 는 시스템이 주입한 'key : value' 블록으로 온다(DL-9018 과 같은 모양).
           description="\n".join([
               "VoC 접수 건입니다. FDC trace 요약 테이블을 LAKE 에 신규 적재해 주세요.",
               "",
               "==================== 신청정보 ====================",
               "신청자 : 박지훈 SKCC",
               "요청 부서 : FDC 엔지니어링",
               "",
               "==================== 1 시스템정보 ====================",
               "시스템명 : FDC Trace Collector",
               "환경 : 운영",
               "",
               "==================== 1 테이블정보 ====================",
               "스키마 : FDC",
               "테이블명 : FDC_TRACE_SUMMARY_IC",
               "희망 적재주기 : 2시간 1회",
               "보존기간 : 24개월",
           ]))

        fx("DL-9042", "Task", "[ETL] fdc.fdc_trace_summary_ic 신규 적재 Job 개발", t1,
           module="ETL", component="ETL", assignee="skcc.x1042", reporter="skcc.x1103",
           statusCategory="done", statusName="Resolved",
           created=d - timedelta(days=178), resolved=d - timedelta(days=150),
           tresolved="18:10", updated=d - timedelta(days=150),
           links=[{"type": "Relates", "key": "DL-9041"}],
           description="\n".join([
               "VoC(DL-9041) 요청에 따라 fdc.fdc_trace_summary_ic 적재 Job 을 신규 개발한다.",
               "",
               "h3. Job 정보",
               "* Job 명: etl_fdc_trace_summary_ic_2h",
               "* Airflow DAG: dag_fdc_trace_summary_ic",
               "* 소스: FDC_TRACE_RAW (FDC Trace Collector)",
               "* 적재주기: 2시간 1회",
               "* 운영 담당: skcc.x1042",
               "",
               "h3. 초기 스키마 (7개 컬럼)",
               "|| 컬럼 || 타입 || 설명 ||",
               "| LOT_ID | STRING | 로트 식별자 |",
               "| EQP_ID | STRING | 설비 식별자 |",
               "| RECIPE_ID | STRING | 레시피 식별자 |",
               "| TRACE_TS | TIMESTAMP | 계측 시각 |",
               "| VALUE_AVG | DOUBLE | 구간 평균 |",
               "| VALUE_STD | DOUBLE | 구간 표준편차 |",
               "| PART_DT | STRING | 파티션 키(일자) |",
           ]))

        # 장애 티켓 — 코멘트 6건(원인·조치가 코멘트에만 있다. get_ticket 기본 5건 상한을 넘긴다)
        fx("DL-9043", "Bug", "[ETL] fdc.fdc_trace_summary_ic 적재 지연 — 06:00 배치 4시간 지연", t1,
           module="ETL", component="ETL", assignee="skcc.i2011", reporter="skcc.x1042",
           priority="P1-Critical",
           statusCategory="done", statusName="Closed",
           created=d - timedelta(days=95), resolved=d - timedelta(days=92),
           tresolved="11:05", updated=d - timedelta(days=92),
           links=[{"type": "Blocks", "key": "DL-9044"}],
           description="06:00 배치가 4시간 지연되어 후속 리포트가 밀렸다. 원인 파악 필요.",
           comments=[
               self._cmt("skcc.i2011", "소스 파티션 스캔이 폭증했습니다. FDC_TRACE_RAW 의 "
                                       "일자 파티션이 안 걸린 채 풀스캔되고 있었습니다.", 94, "09:12"),
               self._cmt("skcc.x1042", "핫픽스로 PART_DT 파티션 프루닝을 넣어 재기동했습니다. "
                                       "지연 4시간 → 12분으로 회복.", 94, "13:40"),
               self._cmt("skcc.i2011", "재발 감시를 위해 DataDog 알람 임계치를 30분으로 낮췄습니다.", 93, "10:05"),
               self._cmt("lead", "2시간 주기로는 지연이 나면 리포트가 통째로 밀립니다. "
                                 "주기 단축을 검토해 주세요.", 93, "15:20"),
               self._cmt("skcc.x1042", "주기 단축은 별도 티켓으로 진행하겠습니다.", 92, "09:30"),
               self._cmt("skcc.i2011", "종료 처리합니다. 근본 조치는 주기 단축 티켓에서 이어집니다.", 92, "11:05"),
           ])

        # ★ "현재 적재주기" 의 정답이 사는 티켓 — changelog·코멘트·본문 3중 기록
        fx("DL-9044", "Task", "[ETL] fdc.fdc_trace_summary_ic 적재주기 변경 (2시간 → 30분)", t1,
           module="ETL", component="ETL", assignee="skcc.x1042", reporter="lead",
           statusCategory="done", statusName="Resolved",
           created=d - timedelta(days=88), resolved=d - timedelta(days=80),
           tresolved="16:00", updated=d - timedelta(days=80),
           description="지연 장애(DL-9043) 후속. 적재주기를 2시간 1회에서 30분 1회로 단축한다.",
           changelog=[self._chg("skcc.x1042", 80, "적재주기", "2시간 1회", "30분 1회", "16:00")],
           comments=[
               self._cmt("skcc.x1042", "30분 주기로 변경 적용 완료했습니다. Job 이름도 "
                                       "etl_fdc_trace_summary_ic_2h → etl_fdc_trace_summary_ic_30m "
                                       "으로 바꿨습니다. 운영 담당은 그대로 저(skcc.x1042)입니다.", 80, "16:10"),
           ])

        fx("DL-9045", "Task", "[ETL] fdc.fdc_trace_summary_ic 스키마 변경 — CHAMBER_ID 컬럼 추가", t1,
           module="ETL", component="ETL", assignee="skcc.x1103", reporter="skcc.x1042",
           statusCategory="done", statusName="Resolved",
           created=d - timedelta(days=45), resolved=d - timedelta(days=40),
           tresolved="14:30", updated=d - timedelta(days=40),
           description="\n".join([
               "챔버 단위 분석 요구로 CHAMBER_ID 컬럼을 추가한다. 컬럼 수 7개 → 8개.",
               "",
               "{code:sql}",
               "ALTER TABLE fdc.fdc_trace_summary_ic ADD COLUMN CHAMBER_ID STRING;",
               "{code}",
           ]),
           changelog=[self._chg("skcc.x1103", 40, "스키마", "7개 컬럼",
                                "8개 컬럼 (CHAMBER_ID 추가)", "14:30")])

        doc1 = "[데이터카탈로그] fdc_trace_summary_ic 테이블 특성 분석"
        fx("DL-9046", "Task", "[Catalog] fdc.fdc_trace_summary_ic 테이블 특성 분석 및 카탈로그 등록", t1,
           module="Catalog", component="Catalog", assignee="skcc.x1210", reporter="skcc.x1042",
           statusCategory="done", statusName="Resolved",
           created=d - timedelta(days=42), resolved=d - timedelta(days=38),
           tresolved="17:00", updated=d - timedelta(days=38),
           # 본문 언급과 remotelink 는 **같은 /pages/{id}/ 형태**여야 문서 중복 제거가 1건으로 접는다
           description=f"테이블 특성 분석 결과를 카탈로그에 등록했다.\n"
                       f"분석 문서: [{doc1}|{self._conf_url(doc1)}]",
           remotelinks=[{"url": self._conf_url(doc1), "title": doc1,
                         "application": {"type": "com.atlassian.confluence", "name": "Confluence"}}])

        # 데이터 포맷 첨부 — "컬럼이 뭐고, 이 테이블 관련 행이 있나" 를 시험한다.
        # 표를 통째로 프롬프트에 붓지 않고 **컬럼 + 관련 행**만 뽑는지가 요점이다.
        _csv = "테이블,모듈,담당,등록여부,행수\nfdc.fdc_trace_summary_ic,ETL,skcc.x1042,등록,120000000\nyms.yms_lot_yield_daily,Catalog,skcc.i2044,등록,8400000\neqp.eqp_sensor_raw_1s,Observability,skcc.i2200,미등록,980000000\nwip.wip_lot_track_hist,ETL,skcc.i2011,미등록,15000000\nqms.qms_defect_code_mst,Catalog,skcc.i2044,등록,1200\n"
        self._fx_attach("DL-9046", "메타데이터_등록현황.csv", "text/csv",
                        _csv.encode("utf-8"))
        _json = ('{"검사일": "2026-08-01", "대상": "DL 프로젝트 전체", "결과": ['
                 '{"table": "fdc.fdc_trace_summary_ic", "컬럼수": 8, "설명누락": 0,'
                 ' "판정": "통과"},'
                 '{"table": "eqp.eqp_sensor_raw_1s", "컬럼수": 0, "설명누락": 0,'
                 ' "판정": "스키마 미등록"},'
                 '{"table": "wip.wip_lot_track_hist", "컬럼수": 6, "설명누락": 3,'
                 ' "판정": "보완 필요"}]}')
        self._fx_attach("DL-9046", "카탈로그_점검_결과.json", "application/json",
                        _json.encode("utf-8"))

        fx("DL-9047", "Task", "[ETL] fdc.fdc_trace_summary_ic 30분 적재 안정화 모니터링", t1,
           module="ETL", component="ETL", assignee="skcc.x1042", reporter="lead",
           statusCategory="inprogress", statusName="In Progress",
           created=d - timedelta(days=30), due=d + timedelta(days=10),
           updated=d - timedelta(days=3),
           description="30분 주기 전환 후 지연·중복 적재 여부를 2주간 관찰한다.")

        # ── ② eqp.eqp_sensor_raw_1s — 중밀도. **스키마·문서 없음(의도)** ──
        t2 = "eqp_sensor_raw_1s"
        fx("DL-9050", "Task", "[Observability] eqp.eqp_sensor_raw_1s 실시간 수집 파이프라인 구축", t2,
           module="Observability", component="Observability",
           assignee="skcc.i2200", reporter="lead",
           statusCategory="done", statusName="Resolved",
           created=d - timedelta(days=160), resolved=d - timedelta(days=130),
           tresolved="18:00", updated=d - timedelta(days=130),
           description="\n".join([
               "설비 센서 원천을 실시간으로 수집한다.",
               "",
               "* Job 명: str_eqp_sensor_raw_1s",
               "* 소스: Kafka 토픽 eqp.sensor.raw",
               "* 적재주기: 실시간 스트리밍(1초 마이크로배치)",
               "* 보존기간: 30일",
               "* 운영 담당: skcc.i2200",
           ]))

        fx("DL-9051", "Bug", "[Observability] eqp.eqp_sensor_raw_1s 컨슈머 랙 증가", t2,
           module="Observability", component="Observability",
           assignee="skcc.x1560", reporter="skcc.i2200",
           statusCategory="done", statusName="Closed",
           created=d - timedelta(days=70), resolved=d - timedelta(days=68),
           tresolved="15:40", updated=d - timedelta(days=68),
           description="피크 시간대에 컨슈머 랙이 누적된다.",
           comments=[
               self._cmt("skcc.x1560", "파티션 수를 12 → 24 로 늘려 해소했습니다.", 69, "11:20"),
               # ④ mes.mes_wip_move_hist 의 **유일한 언급** — 이것 말고는 world 어디에도 없다
               self._cmt("skcc.x1560", "참고로 성격이 비슷한 mes.mes_wip_move_hist 도 같이 봐야 할 수 "
                                       "있다는 얘기가 나왔는데, 그쪽은 우리 적재 대상이 아닙니다.", 68, "15:30"),
           ])

        fx("DL-9052", "Task", "[Observability] eqp.eqp_sensor_raw_1s 보존기간 30일 → 90일 변경", t2,
           module="Observability", component="Observability",
           assignee="skcc.i2200", reporter="lead",
           statusCategory="inprogress", statusName="In Progress",
           created=d - timedelta(days=20), updated=d - timedelta(days=5),
           description="분석 요구로 보존기간을 90일로 늘린다. 적재주기(실시간)는 변경하지 않는다.",
           changelog=[self._chg("skcc.i2200", 5, "보존기간", "30일", "90일", "11:00")])

        # ── ③ yms.yms_lot_yield_daily — 담당이 코멘트에만, ①과의 비교 코멘트 보유 ──
        t3 = "yms_lot_yield_daily"
        fx("DL-9060", "Task", "[Catalog] yms.yms_lot_yield_daily 일배치 적재 및 카탈로그 등록", t3,
           module="Catalog", component="Catalog", assignee="skcc.i2044", reporter="skcc.x1210",
           statusCategory="done", statusName="Resolved",
           created=d - timedelta(days=140), resolved=d - timedelta(days=120),
           tresolved="17:20", updated=d - timedelta(days=120),
           description="\n".join([
               "로트 단위 일별 수율 집계 테이블을 적재한다.",
               "",
               "* Job 명: etl_yms_lot_yield_daily",
               "* 적재주기: 일 1회 (03:00)",
               "",
               "h3. 스키마 (6개 컬럼)",
               "LOT_ID, PROD_ID, LINE_ID, YIELD_PCT, DEFECT_CNT, BASE_DT",
           ]))

        fx("DL-9061", "Task", "[Catalog] yms.yms_lot_yield_daily 적재주기 변경 (일 1회 → 4시간 1회)", t3,
           module="Catalog", component="Catalog", assignee="skcc.x1210", reporter="lead",
           statusCategory="done", statusName="Resolved",
           created=d - timedelta(days=60), resolved=d - timedelta(days=55),
           tresolved="16:40", updated=d - timedelta(days=55),
           description="수율 모니터링 주기 단축 요구로 일 1회 → 4시간 1회로 변경한다.",
           changelog=[self._chg("skcc.x1210", 55, "적재주기", "일 1회 (03:00)", "4시간 1회", "16:40")],
           # 운영 담당 정보가 **코멘트에만** 있는 케이스
           comments=[self._cmt("skcc.x1210", "변경 적용했습니다. Job 운영 담당은 변경 후에도 "
                                             "skcc.i2044 그대로입니다.", 55, "16:45")])

        # ★ 교차 비교 — ①의 사실이 **다른 테이블 티켓의 코멘트**에만 적혀 있다
        fx("DL-9062", "Task",
           "[Catalog] yms.yms_lot_yield_daily 와 fdc.fdc_trace_summary_ic 지표 정합성 비교", t3,
           module="Catalog", component="Catalog", assignee="skcc.x1210", reporter="skcc.i2044",
           statusCategory="inprogress", statusName="In Progress",
           created=d - timedelta(days=15), updated=d - timedelta(days=2),
           description="두 테이블을 조인해 수율과 계측값의 상관을 보려 한다. 시간축 정합성 확인이 선결.",
           comments=[
               self._cmt("skcc.x1103", "fdc.fdc_trace_summary_ic 는 30분 주기(Job "
                                       "etl_fdc_trace_summary_ic_30m, 담당 skcc.x1042)라 "
                                       "yms.yms_lot_yield_daily(4시간)와 시간축이 맞지 않습니다. "
                                       "조인하려면 30분 → 4시간 리샘플이 필요합니다.", 3, "10:40"),
               self._cmt("skcc.i2044", "리샘플 기준은 4시간 평균으로 맞추겠습니다.", 2, "09:15"),
           ])

        # ── ⑤ 기술 주제 — **테이블이 아닌 것도 똑같이 흩어져 있다.**
        # "Schema Registry 우리 정책이 뭐지?" 의 답은 도입 검토(결정) → 장애(정책 강화) →
        # 전환 작업(진행 중) → 표준 문서에 나뉘어 있고, 결정적 사실인 '현재 정책'은
        # **장애 티켓의 changelog + 코멘트**에만 있다.
        t5 = "schema-registry"
        fx("DL-9070", "Task", "[DevOps] Kafka Schema Registry 도입 검토", t5,
           module="DevOps", component="DevOps", assignee="skcc.x1501", reporter="lead",
           statusCategory="done", statusName="Resolved",
           created=d - timedelta(days=150), resolved=d - timedelta(days=135),
           tresolved="17:10", updated=d - timedelta(days=135),
           description="\n".join([
               "Kafka 토픽 스키마를 중앙에서 관리하기 위해 Schema Registry 도입을 검토했다.",
               "",
               "h3. 결정 사항",
               "* 제품: Confluent Schema Registry",
               "* 직렬화 포맷: Avro",
               "* 초기 호환성 정책: BACKWARD",
               "* 운영 담당: skcc.x1501",
           ]))

        fx("DL-9071", "Bug", "[Observability] 스키마 호환성 위반으로 컨슈머 대량 실패", t5,
           module="Observability", component="Observability",
           assignee="skcc.i2200", reporter="skcc.x1560", priority="P1-Critical",
           statusCategory="done", statusName="Closed",
           created=d - timedelta(days=50), resolved=d - timedelta(days=47),
           tresolved="14:00", updated=d - timedelta(days=47),
           description="프로듀서가 필드를 삭제해 배포하자 컨슈머가 대량 실패했다.",
           # ★ '현재 호환성 정책'의 정답은 여기 changelog + 코멘트에만 있다
           changelog=[self._chg("skcc.x1501", 47, "호환성 정책", "BACKWARD", "FULL", "14:00")],
           comments=[
               self._cmt("skcc.i2200", "Schema Registry 의 BACKWARD 정책은 필드 삭제를 막지 "
                                       "못합니다. 삭제된 필드를 읽던 컨슈머가 전부 죽었습니다.", 48, "10:30"),
               self._cmt("skcc.x1501", "호환성 정책을 BACKWARD → FULL 로 강화 적용했습니다. "
                                       "이제 필드 삭제·추가 양방향이 막힙니다. 레지스트리 운영 "
                                       "담당은 계속 저(skcc.x1501)입니다.", 47, "14:05"),
           ])

        fx("DL-9072", "Task", "[ETL] 프로듀서 Avro 직렬화 전환", t5,
           module="ETL", component="ETL", assignee="skcc.x1103", reporter="skcc.x1501",
           statusCategory="inprogress", statusName="In Progress",
           created=d - timedelta(days=40), due=d + timedelta(days=14),
           updated=d - timedelta(days=4),
           description="JSON 직렬화 프로듀서를 Avro + Schema Registry 로 전환한다. "
                       "전체 9개 토픽 중 6개 완료, 3개 남았다.")

        # ── ⑤ wip.wip_lot_track_hist — **담당 이관 + 폐기 예정**(다른 실패 유형) ──
        # 함정: 최초 담당(skcc.x1103)이 인수인계됐다. 옛 담당을 현재 담당으로 답하면 오답이다.
        #       "아직 쓰는 테이블이냐"의 답(폐기 예정)은 문서에만 있다.
        t6 = "wip_lot_track_hist"
        fx("DL-9080", "Task", "[ETL] wip.wip_lot_track_hist 적재 Job 구축", t6,
           module="ETL", component="ETL", assignee="skcc.x1103", reporter="lead",
           statusCategory="done", statusName="Closed",
           created=d - timedelta(days=300), resolved=d - timedelta(days=286),
           tresolved="16:00", updated=d - timedelta(days=286),
           description="\n".join([
               "MES WIP 이동 이력을 로트 단위로 적재한다.",
               "",
               "* 적재주기: 일 1회 (03:00)",
               "* 적재 Job: etl_wip_lot_track_hist",
               "* 최초 담당: skcc.x1103",
           ]))

        fx("DL-9081", "Task", "[ETL] wip.wip_lot_track_hist 운영 인수인계", t6,
           module="ETL", component="ETL", assignee="skcc.i2011", reporter="skcc.x1103",
           statusCategory="done", statusName="Closed",
           created=d - timedelta(days=90), resolved=d - timedelta(days=86),
           tresolved="11:00", updated=d - timedelta(days=86),
           description="wip.wip_lot_track_hist 운영을 이관한다.",
           # ★ '현재 담당'의 정답은 이 changelog 뿐 — 최초 구축 티켓만 보면 틀린다
           changelog=[self._chg("skcc.x1103", 86, "운영 담당", "skcc.x1103", "skcc.i2011", "11:00")],
           comments=[
               self._cmt("skcc.i2011", "인수 완료했습니다. 지금부터 wip.wip_lot_track_hist "
                                       "운영 담당은 저(skcc.i2011)입니다.", 86, "11:05"),
           ])

        fx("DL-9082", "Bug", "[ETL] wip.wip_lot_track_hist 중복 로트 적재", t6,
           module="ETL", component="ETL", assignee="skcc.i2011", reporter="skcc.i2011",
           priority="P2-Major", statusCategory="done", statusName="Closed",
           created=d - timedelta(days=30), resolved=d - timedelta(days=27),
           tresolved="15:30", updated=d - timedelta(days=27),
           description="일 1회 배치가 재실행되며 같은 로트가 두 번 적재됐다.",
           comments=[
               self._cmt("skcc.i2011", "원인은 배치 재시도 시 이전 파티션을 지우지 않은 것입니다. "
                                       "덮어쓰기로 고쳤습니다. 참고로 이 테이블은 신규 "
                                       "wip.wip_lot_track_v2 로 대체 예정이라 추가 개선은 "
                                       "하지 않습니다.", 27, "15:35"),
           ])

        # ── ⑦ DL-9090 — **한 티켓의 진척**을 묻는 질문용(다른 축의 시험) ──
        # "이 티켓 지금 어디까지 됐어?"의 답은 티켓 필드에 없다. 조각이 네 군데다:
        #   ① 티켓 자체 변동(상태·담당·마감 연기)  ② 코멘트의 진행 보고
        #   ③ 결과를 적는 유관 문서의 최근 수정   ④ 하위 Sub-Task 완료·상위/링크 티켓 변화
        # 필드 상태(In Progress)만 보고 "진행 중입니다"로 끝내면 답이 아니다.
        t7, doc7 = "lineage_ui", "[설계] 리니지 뷰어 1차"
        fx("DL-9090", "Task", "[Workbench] 데이터 리니지 뷰어 1차 오픈", t7,
           module="Workbench", component="Workbench",
           assignee="skcc.x1402", reporter="lead", priority="P1-Critical",
           statusCategory="inprogress", statusName="In Progress",
           created=d - timedelta(days=45), due=d + timedelta(days=7),
           updated=d - timedelta(days=1),
           description="\n".join([
               "테이블 간 리니지를 화면에서 탐색할 수 있게 한다. 1차 범위는 조회 전용이다.",
               "",
               "h3. 완료 기준",
               "* 테이블 상세에서 업스트림/다운스트림 2홉 조회",
               f"* 결과·이슈는 [{doc7}|{self._conf_url(doc7)}] 문서에 기록한다",
           ]),
           links=[{"type": "Blocks", "key": "DL-9092"}],
           subtasks=["DL-9093", "DL-9094", "DL-9095"],
           # 본문 언급 + remotelink 를 같은 URL 로 — 결과를 적는 문서가 진척의 근거다
           remotelinks=[{"url": self._conf_url(doc7), "title": doc7,
                         "application": {"type": "com.atlassian.confluence",
                                         "name": "Confluence"}}],
           changelog=[
               self._chg("skcc.x1402", 38, "status", "To Do", "In Progress", "09:30"),
               self._chg("lead", 12, "마감", (d - timedelta(days=7)).isoformat(),
                         (d + timedelta(days=7)).isoformat(), "17:00"),
               self._chg("lead", 5, "우선순위", "P2-Major", "P1-Critical", "09:00"),
           ],
           comments=[
               self._cmt("skcc.x1402", "1차 착수했습니다. 그래프 렌더는 기존 WBS 뷰 컴포넌트를 "
                                       "재사용합니다.", 38, "09:40"),
               self._cmt("skcc.x1450", "업스트림 2홉까지 조회 붙였습니다. 다운스트림은 API 가 "
                                       "느려서 DL-9092 결과를 기다리는 중입니다.", 16, "18:20"),
               self._cmt("lead", "다운스트림 지연 때문에 마감을 한 주 미뤘습니다. 대신 "
                                 "우선순위를 P1 으로 올립니다.", 12, "17:05"),
               self._cmt("skcc.x1402", "DL-9092 해결돼서 다운스트림도 붙였습니다. 남은 건 "
                                       "성능 측정과 문서 정리입니다. 결과는 설계 문서에 "
                                       "적고 있습니다.", 1, "11:15"),
           ])

        for i, (sub, who, days_done) in enumerate([
                ("[Workbench] 리니지 그래프 렌더 컴포넌트", "skcc.x1402", 30),
                ("[Workbench] 업스트림 2홉 조회 연동", "skcc.x1450", 16),
                ("[Workbench] 다운스트림 조회 연동", "skcc.x1450", None)]):
            k = f"DL-909{3 + i}"
            fx(k, "Sub-Task", sub, t7, module="Workbench", component="Workbench",
               parentKey="DL-9090", assignee=who, reporter="skcc.x1402",
               created=d - timedelta(days=40),
               **({"statusCategory": "done", "statusName": "Closed",
                   "resolved": d - timedelta(days=days_done), "tresolved": "18:00",
                   "updated": d - timedelta(days=days_done)} if days_done else
                  {"statusCategory": "inprogress", "statusName": "In Progress",
                   "updated": d - timedelta(days=1)}))

        fx("DL-9092", "Bug", "[Runtime] 리니지 다운스트림 조회 API 응답 20초", t7,
           module="Runtime", component="Runtime", assignee="skcc.x1315",
           reporter="skcc.x1450", priority="P1-Critical",
           statusCategory="done", statusName="Closed",
           created=d - timedelta(days=20), resolved=d - timedelta(days=3),
           tresolved="16:40", updated=d - timedelta(days=3),
           description="다운스트림 2홉 조회가 20초 이상 걸려 화면이 멈춘다.",
           comments=[
               self._cmt("skcc.x1315", "인덱스를 추가해 1.2초로 줄였습니다. DL-9090 다운스트림 "
                                       "작업 진행 가능합니다.", 3, "16:45"),
           ])

        # ── ⑥ qms.qms_defect_code_mst — **티켓이 하나도 없는 대상**(문서만) ──
        # 함정: 티켓 검색은 0건이다. 여기서 "기록 없음"으로 끝내면 오답 — 문서에 다 있다.
        #       티켓 0건인 대상도 문서를 읽어 답해야 한다.

        # 교차 언급 — 주제와 무관해 보이는 티켓(보존기간 변경)의 코멘트에 정책이 다시 나온다
        self.issues["DL-9052"]["comments"] = [
            self._cmt("skcc.x1560", "보존기간을 늘리면 과거 스키마로 쓰인 메시지도 오래 남습니다. "
                                    "Schema Registry 호환성 정책이 FULL 이라 읽기는 되지만, "
                                    "구버전 스키마 정리는 별도로 봐야 합니다.", 4, "11:10"),
        ]

    def _build_dataset_docs(self):
        """데이터셋 분석 문서 — 기존 uid 의 문서 목록에 덧붙인다(작성자 = 실 인력).
        ★ `_build_confluence()` 가 `self.confluence` 에 **대입**하므로 반드시 그 뒤에 부른다.
        제목은 유일해야 한다 — jira820 이 (title, space) 로 중복 제거한다(먼저 쓴 쪽이 이긴다)."""
        d = self.today
        pages = [
            ("skcc.x1210", "[데이터카탈로그] fdc_trace_summary_ic 테이블 특성 분석", "DL",
             ["엔지니어링", "파이프라인"], 40, "\n".join([
                 "fdc.fdc_trace_summary_ic 는 FDC 계측 trace 를 로트·설비 단위로 요약한 테이블이다.",
                 "",
                 "h2. 적재 현황",
                 "* 현재 적재주기: 30분 1회 (이전 2시간 1회, 지연 장애 후속으로 단축)",
                 "* 적재 Job: etl_fdc_trace_summary_ic_30m (Airflow DAG dag_fdc_trace_summary_ic)",
                 "* 운영 담당: skcc.x1042",
                 "* 소스: FDC_TRACE_RAW",
                 "",
                 "h2. 스키마 (8개 컬럼)",
                 "LOT_ID, EQP_ID, RECIPE_ID, TRACE_TS, VALUE_AVG, VALUE_STD, PART_DT, CHAMBER_ID",
                 "파티션 키는 PART_DT(일자)다. CHAMBER_ID 는 챔버 단위 분석 요구로 나중에 추가됐다.",
                 "",
                 "h2. 관련 티켓",
                 "DL-9042(Job 개발), DL-9044(주기 변경), DL-9045(스키마 변경)",
             ])),
            ("skcc.i2044", "[데이터카탈로그] yms_lot_yield_daily 산출 로직", "DL",
             ["표준·정책", "데이터 거버넌스"], 25, "\n".join([
                 "yms.yms_lot_yield_daily 는 로트 단위 일별 수율 집계 테이블이다.",
                 "",
                 "* 현재 적재주기: 4시간 1회",
                 "* 적재 Job: etl_yms_lot_yield_daily",
                 "* 스키마 6개 컬럼: LOT_ID, PROD_ID, LINE_ID, YIELD_PCT, DEFECT_CNT, BASE_DT",
                 "* YIELD_PCT = (양품 수량 / 투입 수량) * 100, 소수 둘째 자리 반올림",
             ])),
            ("skcc.x1042", "[데이터카탈로그] LAKE 적재주기 변경 절차", "DL",
             ["표준·정책"], 70, "\n".join([
                 "적재주기를 바꿀 때 지켜야 할 절차와 기록 방식을 정리한다.",
                 "",
                 "h2. Job 명명 규칙",
                 "* 배치: etl_<테이블명>_<주기>  (예: etl_fdc_trace_summary_ic_30m)",
                 "* 스트리밍: str_<테이블명>    (예: str_eqp_sensor_raw_1s)",
                 "",
                 "h2. 기록 규칙",
                 "주기를 바꾸면 변경 티켓의 changelog('적재주기' 필드)와 코멘트 양쪽에 남긴다.",
                 "따라서 **현재 주기는 가장 최근 변경 기록**을 보면 된다. 변경 기록이 없으면",
                 "최초 구축 티켓에 적힌 주기가 현재 주기다.",
             ])),
            ("skcc.x1501", "[표준·정책] Kafka 스키마 호환성 정책", "DL",
             ["표준·정책"], 30, "\n".join([
                 "Schema Registry(Confluent) 운영 기준을 정리한다.",
                 "",
                 "* 직렬화 포맷: Avro",
                 "* 현재 호환성 정책: FULL (도입 시 BACKWARD 였으나 컨슈머 대량 실패 후 강화)",
                 "* 레지스트리 운영 담당: skcc.x1501",
                 "* 신규 토픽은 스키마 등록 후에만 프로듀싱을 허용한다.",
             ])),
            # ★ 이 테이블은 **티켓이 하나도 없다** — 티켓 검색 0건에서 멈추면 답을 못 낸다.
            ("skcc.i2044", "[데이터카탈로그] qms_defect_code_mst 코드 마스터 정의", "DL",
             ["표준·정책", "데이터 거버넌스"], 18, "\n".join([
                 "qms.qms_defect_code_mst 는 품질 불량 코드의 마스터 테이블이다.",
                 "티켓으로 관리되지 않고 QMS 원천에서 주 1회 동기화된다.",
                 "",
                 "h2. 적재 현황",
                 "* 현재 적재주기: 주 1회 (월요일 05:00)",
                 "* 적재 Job: etl_qms_defect_code_mst_w",
                 "* 운영 담당: skcc.i2044",
                 "",
                 "h2. 스키마 (5개 컬럼)",
                 "DEFECT_CD, DEFECT_NM, CATEGORY_CD, USE_YN, UPD_DT",
                 "DEFECT_CD 가 기본키다. USE_YN='N' 은 폐기된 코드로 조회에서 제외한다.",
             ])),
            # ★ DL-9090 의 **결과를 적는 문서** — 진척을 물으면 이 문서의 최근 수정도 근거다
            ("skcc.x1402", "[설계] 리니지 뷰어 1차", "DL",
             ["엔지니어링"], 2, "\n".join([
                 "DL-9090 리니지 뷰어 1차 개발의 설계와 진행 결과를 기록한다.",
                 "",
                 "h2. 진행 결과 (최종 수정 기준)",
                 "* 그래프 렌더 컴포넌트: 완료 (WBS 뷰 컴포넌트 재사용)",
                 "* 업스트림 2홉 조회: 완료",
                 "* 다운스트림 2홉 조회: 완료 — DL-9092 인덱스 추가로 20초 → 1.2초",
                 "* 남은 일: 성능 측정(2홉 100 노드 기준), 사용 가이드 작성",
                 "",
                 "h2. 미결",
                 "3홉 이상 확장은 1차 범위 밖이다. 2차에서 다룬다.",
             ])),
            # 폐기 예정 판단의 유일한 출처 — 티켓 코멘트에는 '대체 예정'만 스친다
            ("skcc.i2011", "[데이터카탈로그] wip_lot_track_hist 폐기 계획", "DL",
             ["엔지니어링"], 12, "\n".join([
                 "wip.wip_lot_track_hist 는 wip.wip_lot_track_v2 로 대체한다.",
                 "",
                 "* 현재 적재주기: 일 1회 (03:00) — 폐기 시점까지 유지",
                 "* 운영 담당: skcc.i2011 (2026년 인수인계, 최초 구축은 skcc.x1103)",
                 "* 폐기 예정: v2 병행 운영 3개월 후 중단. 신규 참조는 v2 를 쓸 것",
             ])),
        ]
        for uid, title, space, anc, days_ago, body in pages:
            self.confluence.setdefault(uid, []).append({
                "title": title, "space": space, "ancestors": anc, "action": "edited",
                "body": body, "date": d - timedelta(days=days_ago), "time": "15:00"})
        for uid in {p[0] for p in pages}:
            self.confluence[uid].sort(key=lambda p: p["date"], reverse=True)

    # 우선순위 — 실 Jira 는 모든 이슈가 priority 를 갖는다. 없으면 '내 Task' 정렬의 한 축이
    # 통째로 죽어 화면 검증이 안 된다. ★ rng 미사용(키 해시에서 결정적으로) → world 시퀀스 불변.
    # 사내 체계 그대로 — 'P{n}-이름' + 미분류. dev 를 표준 스킴(Highest/High/…)으로 두면
    # P 접두사 파싱도, 미분류 표식도 화면에서 한 번도 검증되지 않는다.
    _PRIORITIES = ["P0-Blocker", "P1-Critical", "P2-Major", "P2-Major",
                   "P3-Minor", "P4-Trivial", "Unclassified"]

    def _priorities(self):
        for k, it in self.issues.items():
            if it.get("priority"):
                continue                      # 픽스처가 지정한 값은 건드리지 않는다
            it["priority"] = self._PRIORITIES[sum(map(ord, k)) % len(self._PRIORITIES)]

    # 스프린트 — 실 Jira 는 대부분 보드/스프린트를 쓴다. dev 에 하나도 없으면
    # '스프린트에 속한 티켓만' 필터를 눈으로 검증할 수 없다(늘 0건).
    # ★ rng 미사용 — 키 해시로 결정적 배정(world 시퀀스 불변). 일부러 **일부만** 넣는다
    #   (스프린트 밖 티켓이 있어야 필터가 의미를 갖는다).
    def _sprints(self):
        d = self.today
        self.sprints = {
            1: {"id": 1, "name": "Sprint 24", "state": "closed", "boardId": 1,
                "startDate": d - timedelta(days=28), "endDate": d - timedelta(days=14)},
            2: {"id": 2, "name": "Sprint 25", "state": "active", "boardId": 1,
                "startDate": d - timedelta(days=13), "endDate": d + timedelta(days=1)},
            3: {"id": 3, "name": "Sprint 26", "state": "future", "boardId": 1,
                "startDate": d + timedelta(days=2), "endDate": d + timedelta(days=16)},
        }
        for k, it in sorted(self.issues.items()):
            if it["type"] == "Epic":
                continue                       # Epic 은 스프린트에 넣지 않는다(실무 관례)
            h = sum(map(ord, k))
            if h % 10 < 3:
                continue                       # 약 30% 는 스프린트 밖
            # 완료된 것은 지난 스프린트, 나머지는 활성/차기로 — 상태와 어긋나지 않게
            sid = 1 if it["statusCategory"] == "done" and h % 2 == 0 else (2 if h % 5 else 3)
            it["sprints"] = [sid]

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
                for _ in range(rng.randint(1, 6)):        # 문서 수 확충(검색 결과가 풍부하도록)
                    _t = wc.conf_title(rng)
                    _sp = wc.conf_space(rng)
                    pages.append({"title": _t, "space": _sp,
                                  "ancestors": wc.conf_ancestors(rng, _sp),   # 상위 폴더 경로
                                  "action": wc.conf_action(rng), "body": wc.conf_body(rng, _t),
                                  # 문서도 지난 12개월에 분산한다. 전부 최근 2주에 몰려 있으면
                                  # "그때 이런 결정이 있었다" 가 성립하지 않는다.
                                  "date": self.today - timedelta(days=rng.randint(0, 365)),
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

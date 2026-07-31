"""bulk.py — Bulk 티켓 생성의 **검증 규칙**(순수 로직).

Jira 호출은 하지 않는다. 실제 값 조회는 호출부가 `Lookup` 로 주입한다 — 그래야 이 규칙을
Jira 없이 테스트할 수 있고, 조회 결과를 **부모/종류별로 메모이즈**해 N배 왕복을 막을 수 있다.

포맷(설계 의도):
  · `create_child` 인자와 1:1 이라 변환이 얇다. Jira REST 에 가깝게 두되 우리 규칙만 얹었다.
  · mode 는 단일 — **Task 와 Sub-Task 를 한 번에 만들지 않는다.** JSON 안에서 방금 만든 티켓을
    부모로 참조할 수 없다는 뜻이기도 하다(Sub-Task 의 parent 는 이미 존재해야 한다).
  · mode=task 는 `epic` 키가 **반드시 존재**해야 한다. Epic 없이 만들 땐 `"epic": null` 을 명시한다 —
    '빠뜨린 것'과 '의도적으로 없는 것'을 구분하지 못하면 조용히 미아 티켓이 쌓인다.

오류는 **항목 인덱스 + 필드명 + 사유**로 돌려준다(화면이 그대로 목록으로 그린다).
"""

import re

__all__ = ["MODES", "ITEM_FIELDS", "validate_bulk", "to_create_kwargs"]

MODES = ("task", "subtask")

# 항목이 가질 수 있는 키 — 이 밖의 키는 '경고'(무시하고 생성은 진행)
ITEM_FIELDS = {"summary", "type", "epic", "parent", "priority", "duedate",
               "assignee", "components", "labels", "description"}

_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 본문에서 못 만드는 것 — Bulk 에는 파일 업로드 경로가 없어 **첨부·이미지는 불가**하고,
# 링크는 웹(http/https)만 살아 남는다. 막지는 않고(내용은 글자로 보존) 경고로 알린다.
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
MAX_ITEMS = 100          # 한 번에 만들 수 있는 상한 — 실수로 수천 건을 밀어 넣는 사고 방지


def _err(idx, field, msg):
    return {"index": idx, "field": field, "message": msg}


def _is_str_list(v):
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def validate_bulk(mode, items, lookup=None):
    """검증 결과 `{"ok": bool, "errors": [...], "warnings": [...]}`.

    lookup 이 None 이면 **형태 검사만**(스키마). 주면 실값까지 대조한다.
    lookup 은 다음을 갖춘 객체(없는 메서드는 그 검사를 건너뛴다):
      · badge(key)        → {"key","type",...} | None      (티켓 존재/타입)
      · child_types(key)  → ["Sub-Task", ...]              (그 부모 밑에 만들 수 있는 타입)
      · task_types()      → ["Task", "Bug", ...]           (최상위로 만들 수 있는 타입)
      · priorities()      → ["P1-Critical", ...]
      · components()      → ["ETL", ...]
      · user_exists(id)   → bool
      · may_edit(key)     → bool
    """
    errors, warnings = [], []

    if mode not in MODES:
        return {"ok": False,
                "errors": [_err(None, "mode", "mode 는 'task' 또는 'subtask' 여야 합니다.")],
                "warnings": []}
    if not isinstance(items, list):
        return {"ok": False, "errors": [_err(None, "items", "items 는 배열이어야 합니다.")],
                "warnings": []}
    if not items:
        return {"ok": False, "errors": [_err(None, "items", "만들 항목이 없습니다.")],
                "warnings": []}
    if len(items) > MAX_ITEMS:
        return {"ok": False,
                "errors": [_err(None, "items", f"한 번에 최대 {MAX_ITEMS}건까지 만들 수 있습니다 (현재 {len(items)}건).")],
                "warnings": []}

    sub = mode == "subtask"
    # 실값 조회 캐시 — 같은 부모가 여러 번 나와도 한 번만 묻는다
    memo_badge, memo_types = {}, {}
    prio_names = comp_names = task_types = None

    def badge(k):
        if k not in memo_badge:
            memo_badge[k] = lookup.badge(k) if hasattr(lookup, "badge") else None
        return memo_badge[k]

    def kid_types(k):
        if k not in memo_types:
            memo_types[k] = lookup.child_types(k) if hasattr(lookup, "child_types") else None
        return memo_types[k]

    for i, it in enumerate(items):
        if not isinstance(it, dict):
            errors.append(_err(i, None, "각 항목은 객체(JSON object)여야 합니다."))
            continue

        for k in it:
            if k not in ITEM_FIELDS:
                warnings.append(_err(i, k, f"알 수 없는 필드 '{k}' — 무시됩니다."))

        # ── 필수: summary ─────────────────────────────────────────────
        summary = it.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            errors.append(_err(i, "summary", "제목(summary)은 비어 있지 않은 문자열이어야 합니다."))

        # ── 필수: type ────────────────────────────────────────────────
        itype = it.get("type")
        if not isinstance(itype, str) or not itype.strip():
            errors.append(_err(i, "type", "이슈 타입(type)은 필수입니다."))
            itype = None
        else:
            itype = itype.strip()

        # ── 부모 연결: mode 별로 필수 키가 다르다 ──────────────────────
        parent_key = None
        if sub:
            if "parent" not in it:
                errors.append(_err(i, "parent", "Sub-Task 는 상위 Task 키(parent)가 필수입니다."))
            else:
                pv = it.get("parent")
                if not isinstance(pv, str) or not _KEY_RE.match(pv.strip()):
                    errors.append(_err(i, "parent", "parent 는 'DL-123' 형태의 기존 티켓 키여야 합니다(null 불가)."))
                else:
                    parent_key = pv.strip()
            if "epic" in it:
                warnings.append(_err(i, "epic", "subtask 모드에서 epic 은 무시됩니다(상위는 parent 로 정합니다)."))
        else:
            if "epic" not in it:
                errors.append(_err(i, "epic",
                                   "epic 키가 필요합니다. Epic 없이 만들려면 \"epic\": null 을 명시하세요."))
            else:
                ev = it.get("epic")
                if ev is None:
                    parent_key = None                       # 의도적으로 Epic 없음
                elif not isinstance(ev, str) or not _KEY_RE.match(ev.strip()):
                    errors.append(_err(i, "epic", "epic 은 'DL-123' 형태의 Epic 키 또는 null 이어야 합니다."))
                else:
                    parent_key = ev.strip()
            if "parent" in it:
                warnings.append(_err(i, "parent", "task 모드에서 parent 는 무시됩니다(상위는 epic 으로 정합니다)."))

        # ── 선택 필드 형태 ────────────────────────────────────────────
        due = it.get("duedate")
        if due is not None and due != "":
            if not isinstance(due, str) or not _DATE_RE.match(due.strip()):
                errors.append(_err(i, "duedate", "duedate 는 'YYYY-MM-DD' 형식이어야 합니다."))
        for f in ("priority", "assignee", "description"):
            v = it.get(f)
            if v is not None and not isinstance(v, str):
                errors.append(_err(i, f, f"{f} 는 문자열이어야 합니다."))
        for f in ("components", "labels"):
            v = it.get(f)
            if v is not None and not _is_str_list(v):
                errors.append(_err(i, f, f"{f} 는 문자열 배열이어야 합니다. 예: [\"ETL\"]"))

        # 본문: 첨부/이미지는 만들 수 없고 링크는 웹만 — 글자로는 남으니 경고로만 알린다.
        desc = it.get("description")
        if isinstance(desc, str) and desc:
            if _MD_IMAGE_RE.search(desc):
                warnings.append(_err(i, "description",
                                     "이미지·파일 첨부는 Bulk 로 만들 수 없습니다. 웹 링크(http/https)만 살아납니다."))
            for m in _MD_LINK_RE.finditer(desc):
                url = m.group(1)
                if not url.lower().startswith(("http://", "https://")):
                    warnings.append(_err(i, "description",
                                         f"'{url}' 는 웹 링크가 아니라 링크로 만들지 않습니다(글자로 남습니다)."))
                    break

        if lookup is None:
            continue

        # ── 실값 대조 ─────────────────────────────────────────────────
        if parent_key:
            b = badge(parent_key)
            fld = "parent" if sub else "epic"
            if not b:
                errors.append(_err(i, fld, f"{parent_key} 티켓을 찾을 수 없습니다."))
            else:
                ptype = (b.get("type") or "")
                if sub and ptype == "Epic":
                    errors.append(_err(i, "parent",
                                       f"{parent_key} 는 Epic 입니다. Sub-Task 의 상위는 Task 여야 합니다."))
                elif not sub and ptype != "Epic":
                    errors.append(_err(i, "epic",
                                       f"{parent_key} 는 Epic 이 아니라 {ptype} 입니다."))
                else:
                    allowed = kid_types(parent_key)
                    if allowed is not None and itype and itype not in allowed:
                        errors.append(_err(i, "type",
                                           f"{parent_key} 밑에는 '{itype}' 을(를) 만들 수 없습니다. "
                                           f"가능: {', '.join(allowed) or '없음'}"))
                if hasattr(lookup, "may_edit") and not lookup.may_edit(parent_key):
                    errors.append(_err(i, fld, f"{parent_key} 에 만들 권한이 없습니다(담당자·보고자 또는 매니저만)."))
        elif not sub and itype:
            if task_types is None and hasattr(lookup, "task_types"):
                task_types = lookup.task_types()
            if task_types is not None and itype not in task_types:
                errors.append(_err(i, "type",
                                   f"Epic 없이 '{itype}' 은(는) 만들 수 없습니다. "
                                   f"가능: {', '.join(task_types) or '없음'}"))

        pr = (it.get("priority") or "").strip() if isinstance(it.get("priority"), str) else ""
        if pr and hasattr(lookup, "priorities"):
            if prio_names is None:
                prio_names = lookup.priorities()
            if prio_names is not None and pr not in prio_names:
                errors.append(_err(i, "priority",
                                   f"'{pr}' 우선순위가 없습니다. 가능: {', '.join(prio_names)}"))

        comps = it.get("components")
        if _is_str_list(comps) and comps and hasattr(lookup, "components"):
            if comp_names is None:
                comp_names = lookup.components()
            if comp_names is not None:
                for c in comps:
                    # 컴포넌트는 **막지 않는다** — 목록에 없는 이름을 쓰는 운영이 있다(사용자 확인).
                    # 그래도 오타는 잡아 줘야 하니 경고로 알리고, 실제 거절은 생성 결과가 말한다.
                    if c.strip() and c.strip() not in comp_names:
                        warnings.append(_err(i, "components",
                                             f"'{c}' 는 등록된 컴포넌트 목록에 없습니다"
                                             f"(오타가 아니면 그대로 진행). 등록된 값: {', '.join(comp_names)}"))

        asg = (it.get("assignee") or "").strip() if isinstance(it.get("assignee"), str) else ""
        if asg and hasattr(lookup, "user_exists") and not lookup.user_exists(asg):
            errors.append(_err(i, "assignee",
                               # 화면은 이 문구를 그대로 글자로 그린다 — 마크다운 별표를 넣으면 별표가 보인다.
                               f"'{asg}' 사용자를 찾을 수 없습니다. 사용자명은 이메일 @ 앞부분입니다"
                               f"(예: hong.gildong@company.com → hong.gildong)."))

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def to_create_kwargs(mode, item):
    """검증을 통과한 항목 → `create_child(**kwargs)` 인자. description 은 호출부가 변환해 넣는다."""
    parent = item.get("parent") if mode == "subtask" else item.get("epic")
    return {
        "parent_key": (parent or "").strip() or None,
        "itype": (item.get("type") or "").strip(),
        "summary": (item.get("summary") or "").strip(),
        "priority": (item.get("priority") or "").strip() or None,
        "duedate": (item.get("duedate") or "").strip() or None,
        "assignee": (item.get("assignee") or "").strip() or None,
        "components": [c for c in (item.get("components") or []) if str(c).strip()] or None,
        "labels": [x for x in (item.get("labels") or []) if str(x).strip()] or None,
    }

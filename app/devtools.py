# -*- coding: utf-8 -*-
"""개발자용 기능(dev tools) 레지스트리 + 안전한 스키마 덤프.

**왜 있나** — 사내 API(Bitbucket 등)의 실제 응답 형태를 개발 중에 확인해야 하는데,
사내 데이터는 외부로 반출할 수 없다. 그래서 앱 안에 **기본 꺼진** 진단 기능을 두고,
설정으로 켠 뒤 **필드 구조만**(값은 마스킹) 화면에 찍어 확인한다.

**관리 방식** — 모든 dev 기능은 여기 DEV_TOOLS 에 등록한다. **지금은 전부 열려 있다**
(config 스위치 없음). 노출 제어는 나중에 유저 역할 구분이 생기면 `enabled()` 한 곳에서
role 로 가른다 — 라우트 등록·게이팅이 전부 그 함수를 거치므로 여기만 바꾸면 된다.
"""

# name -> 한 줄 설명. 새 dev 기능은 여기 등록한다.
DEV_TOOLS = {
    "bitbucket_probe": "사내 Bitbucket 실제 REST 응답의 필드 구조를 확인(값 마스킹). "
                       "code/repo 검색 mock 을 실물에 맞추기 위한 일회성 진단.",
    "sso_status": "각 서비스(Jira/Confluence/Bitbucket)가 지금 인증됐는지 확인. "
                  "앱 로그인 순회 후 어느 서비스가 통과/실패했는지 화면에서 본다.",
}


# 지금은 **전부 열어둔다**(config 무관). 노출 제어는 '나중에' 유저 역할 구분이 생기면
# 여기 한 곳에서 role 을 보고 가른다. 그때까지는 모든 dev 기능이 항상 켜져 있다.
# (라우트 등록·엔드포인트 게이팅이 전부 이 함수 하나를 거치므로, 역할 훅은 여기만 바꾸면 된다.)
def enabled(settings, name, role=None):
    # TODO(역할): 유저 역할이 도입되면 role 로 가른다. 예) return role in _VISIBLE_TO.get(name, {"dev"})
    return name in DEV_TOOLS


def any_enabled(settings):
    return True


# ── 안전한 스키마 스켈레톤 ──────────────────────────────────────────
# 값을 지우고 '어떤 키에 어떤 타입이 오는가'만 남긴다. 화면에 찍어 소리내어 읽어도
# 사내 데이터가 새지 않게 한다. 리스트는 앞 1~2개만 병합해 구조를 보인다.

_MAX_STR = 24                     # 문자열은 이 길이까지만 힌트로(마스킹된 미리보기)
_LIST_SAMPLE = 2                  # 리스트는 앞 N개 항목 구조만


def schema_of(value, mask=True, _depth=0):
    """JSON 값 → 필드 구조 스켈레톤. mask=True 면 스칼라 값을 타입/길이로 대체."""
    if _depth > 8:
        return "…"
    if isinstance(value, dict):
        return {k: schema_of(v, mask, _depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        if not value:
            return []
        merged = _merge([schema_of(v, mask, _depth + 1) for v in value[:_LIST_SAMPLE]])
        tag = f"[{len(value)}개]"
        return [tag, merged]
    if not mask:
        return value
    if isinstance(value, str):
        prev = value[:_MAX_STR] + ("…" if len(value) > _MAX_STR else "")
        return f"str({len(value)}) «{prev}»"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if value is None:
        return "null"
    return type(value).__name__


def _merge(schemas):
    """리스트 샘플들의 구조를 합친다(키 합집합) — 항목마다 키가 조금씩 달라도 다 보이게."""
    if all(isinstance(s, dict) for s in schemas) and schemas:
        keys = {}
        for s in schemas:
            for k, v in s.items():
                keys.setdefault(k, v)
        return keys
    return schemas[0] if schemas else None


def key_tree(value, _prefix="", _out=None):
    """스키마를 'a.b.c : 타입' 평면 목록으로 — 구술로 읽기 쉽게."""
    _out = _out if _out is not None else []
    if isinstance(value, dict):
        for k, v in value.items():
            key = f"{_prefix}.{k}" if _prefix else k
            if isinstance(v, (dict, list)):
                key_tree(v, key, _out)
            else:
                _out.append(f"{key} : {v}")
    elif isinstance(value, list):
        # ['[N개]', {구조}] 형태
        inner = value[1] if len(value) == 2 else (value[0] if value else None)
        cnt = value[0] if value and isinstance(value[0], str) else ""
        key_tree(inner, f"{_prefix}[]{cnt}", _out)
    else:
        _out.append(f"{_prefix} : {value}")
    return _out


# ── 필요한 필드만 추리는 digest ──────────────────────────────────────
# 전체 스키마는 너무 크다. mock/렌더에 실제 쓰는 것만 찾아 '경로 = 샘플' 로 압축한다.

def _find_first_list(value, _path=""):
    """응답에서 '결과 배열'로 보이는 첫 리스트를 찾는다 — (경로, 항목들)."""
    if isinstance(value, list) and value and isinstance(value[0], (dict,)):
        return _path, value
    if isinstance(value, dict):
        # values/results/hits 를 우선, 없으면 아무 dict 리스트
        for pref in ("values", "results", "hits"):
            if isinstance(value.get(pref), list) and value[pref] and isinstance(value[pref][0], dict):
                return (_path + "." + pref).lstrip("."), value[pref]
        for k, v in value.items():
            got = _find_first_list(v, (_path + "." + k).lstrip("."))
            if got[1]:
                return got
    return _path, None


def _dig(item, names, _prefix="", _depth=0):
    """dict 를 3단까지 훑어 이름 후보와 맞는 첫 스칼라 필드를 찾는다 — (경로, 값).
    스칼라 직접 매칭을 우선하고(같은 깊이에서), 없으면 중첩으로 내려간다."""
    if not isinstance(item, dict) or _depth > 3:
        return None, None
    for k, v in item.items():                       # 이 깊이의 스칼라 먼저
        if k.lower() in names and not isinstance(v, (dict, list)):
            return (f"{_prefix}.{k}".lstrip("."), v)
    for k, v in item.items():                       # 그다음 중첩
        if isinstance(v, dict):
            got = _dig(v, names, f"{_prefix}.{k}".lstrip("."), _depth + 1)
            if got[0]:
                return got
    return None, None


def _sample(v):
    if isinstance(v, str):
        return v[:40] + ("…" if len(v) > 40 else "")
    return v


# 렌더/검색에 무의미한 잡음 키 — digest 에서 뺀다(읽을 게 확 줄어든다)
_NOISE_KEYS = {"links", "_links", "self", "avatarurl", "avatarurl16x16", "iconurl",
               "iconcssclass", "scope", "scmid", "statusmessage", "hierarchyid",
               "public", "forkable", "id"}


def _prune(value, _depth=0):
    """스키마에서 노이즈 키 제거 + 깊이 제한. mock 에 쓸 구조만 남긴다."""
    if _depth > 4:
        return "…"
    if isinstance(value, dict):
        return {k: _prune(v, _depth + 1) for k, v in value.items()
                if k.lower() not in _NOISE_KEYS}
    if isinstance(value, list):
        return [_prune(v, _depth + 1) for v in value[:2]]
    return value


def bitbucket_digest(resp, kind):
    """code/repo 검색 응답에서 **필요한 것만** — 결과 배열 위치 + 첫 항목의 (잡음 뺀) 구조.

    kind 는 라벨용. 첫 항목 하나의 구조만 보면 mock 을 맞출 수 있다.
    """
    path, items = _find_first_list(resp)
    out = {"종류": kind,
           "결과배열_경로": path or "(못 찾음)",
           "결과_개수": len(items) if items else 0,
           "최상위_키": list(resp.keys()) if isinstance(resp, dict) else "(dict 아님)"}
    if items:
        # 첫 항목의 구조(값 마스킹 + 노이즈 제거) — 이것만 읽어주면 됨
        out["첫_항목_구조"] = _prune(schema_of(items[0]))
    return out

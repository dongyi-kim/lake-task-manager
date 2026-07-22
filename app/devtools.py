# -*- coding: utf-8 -*-
"""개발자용 기능(dev tools) 레지스트리 + 안전한 스키마 덤프.

**왜 있나** — 사내 API(Bitbucket 등)의 실제 응답 형태를 개발 중에 확인해야 하는데,
사내 데이터는 외부로 반출할 수 없다. 그래서 앱 안에 **기본 꺼진** 진단 기능을 두고,
설정으로 켠 뒤 **필드 구조만**(값은 마스킹) 화면에 찍어 확인한다.

**관리 방식** — 모든 dev 기능은 여기 DEV_TOOLS 에 등록하고, config 의 `dev_tools`
(또는 env `LAKE_DEV_TOOLS`, 콤마구분)로 켠다. 안 켜면 라우트 자체가 안 붙는다.
운영 배포 시엔 목록을 비우면 전부 사라진다.
"""

# name -> 한 줄 설명. 새 dev 기능은 여기 등록한다.
DEV_TOOLS = {
    "bitbucket_probe": "사내 Bitbucket 실제 REST 응답의 필드 구조를 확인(값 마스킹). "
                       "code/repo 검색 mock 을 실물에 맞추기 위한 일회성 진단.",
}


def enabled(settings, name):
    return name in (getattr(settings, "dev_tools", None) or set())


def any_enabled(settings):
    return bool(getattr(settings, "dev_tools", None))


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

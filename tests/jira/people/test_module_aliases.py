"""모듈 별칭 표 — 사람이 적은 뜻의 매핑과, 그것을 쓰는 코드 판정.

왜 이 파일이 있나: 모듈은 **워크로드 집계의 축**이다. "쿼리 엔진"을 Runtime 으로 넘겨짚든
Workbench 로 두든 티켓은 멀쩡해 보이고 어디서도 안 터지며, 집계만 조용히 틀린다.
그래서 ① 매핑은 사람이 config 에 적고 ② 코드는 적힌 것만 보고 ③ 두 config 가 어긋나면
여기서 잡는다.
"""
import pytest

from app.infra.settings import (load_people, module_aliases, modules_in_text,
                                reload_people, resolve_module)


@pytest.fixture(autouse=True)
def _fresh():
    reload_people()
    yield
    reload_people()


def test_alias_keys_are_real_modules():
    """별칭 표의 키는 people.yaml 의 모듈이어야 한다.

    어긋난 채로 매핑이 살아 있으면 **존재하지 않는 모듈**로 담당을 찾다가 조용히 빈손이
    된다 — 모듈 목록 md 가 갈라져 있던 §5-e 와 같은 갈래라 같은 방식으로 막는다.
    """
    import yaml
    from app.infra.settings import CONFIG_DIR
    raw = yaml.safe_load((CONFIG_DIR / "module-aliases.yaml").read_text(encoding="utf-8"))
    mods = set(load_people().keys())
    assert set(raw) <= mods, f"people.yaml 에 없는 모듈: {set(raw) - mods}"


def test_alias_resolves_to_module():
    assert resolve_module("쿼리 엔진") == "Runtime"
    assert resolve_module("쿼리엔진") == "Runtime"      # 공백 표기 차이는 코드가 지운다
    assert resolve_module("리니지") == "Catalog"


def test_unknown_word_stays_unknown():
    """적히지 않은 말은 **못 찾은 것으로 둔다.** 못 찾는 쪽으로 틀리는 것이 맞는 실패다."""
    assert resolve_module("아무거나") == ""
    assert resolve_module("") == ""


def test_canonical_name_still_wins():
    """정식 이름은 별칭보다 먼저다 — 별칭 표가 정식 이름을 가리면 안 된다."""
    assert resolve_module("Runtime") == "Runtime"
    assert resolve_module("etl") == "ETL"


def test_modules_in_text_finds_two_modules_in_order():
    got = modules_in_text("리니지 뷰어 성능 측정하고 쿼리 엔진 쪽 인덱스도 손봐야 해")
    assert got == ["Catalog", "Runtime"]


def test_modules_in_text_is_empty_when_nothing_named():
    """흔한 낱말로 아무 요청이나 두 모듈에 걸치게 만들면 안 된다(별칭을 좁게 적는 이유)."""
    assert modules_in_text("이거 좀 빨리 처리해 주세요") == []


def test_alias_map_does_not_leak_unknown_modules(monkeypatch):
    import app.infra.settings as S
    real = S._read_yaml
    # 별칭 파일 읽기만 바꾼다 — people.yaml 까지 바꾸면 "없는 모듈"이 진짜 모듈이 돼
    # 검사 자체가 무의미해진다(처음 그렇게 짰다가 통과했다).
    monkeypatch.setattr(S, "_read_yaml", lambda p, *a, **k:
                        {"없는모듈": ["가짜"]} if "module-aliases" in str(p) else real(p, *a, **k))
    S._ALIAS_CACHE["map"] = None
    try:
        assert module_aliases() == {}
    finally:
        S._ALIAS_CACHE["map"] = None

"""names.py — 본명 추출 / 개발·운영 구분 (순수)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.names import real_name, staff_kind   # noqa: E402


def test_real_name_single_token_company():
    assert real_name("김도윤 SKCC") == "김도윤"


def test_real_name_multiword_company():
    # 회사명이 여러 어절이어도 첫 어절(본명)만.
    assert real_name("이서준 SK주식회사 C&C") == "이서준"


def test_real_name_system_account_and_empty():
    assert real_name("PMO Office") == "PMO"     # 회사 접미사 없어도 첫 토큰
    assert real_name(None) == ""
    assert real_name("") == ""


def test_real_name_with_english_name_in_parens():
    # 일부 계정은 '{본명}({영어이름}) {소속}' — 이 경우도 본명만.
    assert real_name("김도윤(David) SKCC") == "김도윤"
    assert real_name("김도윤(David)") == "김도윤"
    assert real_name("김도윤 (David) SKCC") == "김도윤"   # 이름과 괄호 사이 공백도 안전


def test_staff_kind_dev_ops():
    assert staff_kind("skcc.x1042") == "dev"
    assert staff_kind("skcc.i2011") == "ops"


def test_staff_kind_none_cases():
    assert staff_kind("skcc.z9") is None       # 알 수 없는 접두
    assert staff_kind("pmo") is None           # '.' 없는 시스템 계정
    assert staff_kind("") is None
    assert staff_kind(None) is None


def test_staff_kind_legacy_nonconforming_ids():
    # 규약 미준수 옛 계정: x/i 로 시작해도 뒤가 숫자가 아니면 판정 불가 → None(뱃지 생략).
    assert staff_kind("skcc.ian") is None      # i + 문자
    assert staff_kind("skcc.xavier") is None   # x + 문자
    assert staff_kind("skcc.john") is None     # x/i 아님
    assert staff_kind("john.doe") is None
    assert staff_kind("skcc.x") is None        # 숫자 없음
    # 규약 준수는 그대로 판정.
    assert staff_kind("skcc.x1") == "dev"
    assert staff_kind("skcc.i2011") == "ops"

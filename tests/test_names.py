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


def test_staff_kind_dev_ops():
    assert staff_kind("skcc.x1042") == "dev"
    assert staff_kind("skcc.i2011") == "ops"


def test_staff_kind_none_cases():
    assert staff_kind("skcc.z9") is None       # 알 수 없는 접두
    assert staff_kind("pmo") is None           # '.' 없는 시스템 계정
    assert staff_kind("") is None
    assert staff_kind(None) is None

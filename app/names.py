"""
Jira 사용자 이름/ID 파싱 — 순수 함수 (네트워크·환경 무관, mock/local/prod 공통).

우리 회사 Jira 관례:
- displayName = "{본명} {소속회사명}"  (예: "김도윤 SKCC", "이서준 SK주식회사 C&C")
- id          = "{회사코드}.{사번}"     (예: "skcc.x1042", "skcc.i2011")
  · 사번이 'x'+숫자 → 개발 인력, 'i'+숫자 → 운영 인력.

화면에는 본명만 노출(assignee·코멘트 작성자), 워크로드는 본명 + 개발/운영 뱃지.

※ 예외: 수년 전 생성된 옛 계정은 이 규약을 안 따른다(사번 형식이 아닌 id).
  이 경우 개발/운영을 판정할 수 없으므로 None → 뱃지 생략(프론트 v-if).
"""

import re

# 사번 = 'x' 또는 'i' + 숫자(1개 이상). 옛 계정('skcc.ian' 등 x/i+문자)은 매칭 안 됨.
_EMP_RE = re.compile(r"^([xi])\d+$")


def real_name(display):
    """displayName '{본명} {소속회사명}' → 본명.

    본명은 공백 없는 단일 토큰(한글 성명)이라 **첫 어절**이 본명이다.
    회사명이 여러 어절이어도('SK주식회사 C&C') 안전. 회사 접미사가 없는
    시스템 계정('PMO Office')은 첫 토큰을 그대로 돌려준다. None/빈값은 "".
    """
    if not display:
        return ""
    return display.split()[0]


def staff_kind(jira_id):
    """id '{회사코드}.{사번}' 의 사번으로 인력 구분 → 'dev' | 'ops' | None.

    사번 x+숫자 = 개발, i+숫자 = 운영. 아래는 모두 None(뱃지 생략):
      · '.' 이 없는 시스템 계정(pmo/lead)
      · 규약 미준수 옛 계정 — 사번 형식(x/i+숫자)이 아닌 id ('skcc.ian', 'skcc.john' 등)
    """
    if not jira_id or "." not in jira_id:
        return None
    emp = jira_id.rsplit(".", 1)[1].lower()
    m = _EMP_RE.match(emp)
    if not m:
        return None
    return "dev" if m.group(1) == "x" else "ops"

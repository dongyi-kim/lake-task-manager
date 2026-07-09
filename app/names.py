"""
Jira 사용자 이름/ID 파싱 — 순수 함수 (네트워크·환경 무관, mock/local/prod 공통).

우리 회사 Jira 관례:
- displayName = "{본명} {소속회사명}"  (예: "김도윤 SKCC", "이서준 SK주식회사 C&C")
- id          = "{회사코드}.{사번}"     (예: "skcc.x1042", "skcc.i2011")
  · 사번이 'x' 로 시작 → 개발 인력, 'i' 로 시작 → 운영 인력.

화면에는 본명만 노출(assignee·코멘트 작성자), 워크로드는 본명 + 개발/운영 뱃지.
"""


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
    """id '{회사코드}.{사번}' 의 사번 접두로 인력 구분 → 'dev' | 'ops' | None.

    사번 x* = 개발, i* = 운영. '.' 이 없거나(시스템 계정 pmo/lead) 그 외 접두는 None.
    """
    if not jira_id or "." not in jira_id:
        return None
    emp = jira_id.rsplit(".", 1)[1].lower()
    if emp.startswith("x"):
        return "dev"
    if emp.startswith("i"):
        return "ops"
    return None

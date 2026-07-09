"""
Epic 진척률 — 순수 계산. 네트워크/인증 의존 0.

입력: 정규화된 이슈 리스트 (Jira/mock 어디서 왔든 동일 형태)
    {"key", "type", "sp", "statusCategory", "labels"}
출력: Epic 단위 SP 롤업 dict.

규칙(../jira_test.py:epic_progress 와 동일):
  - 완료 판정 = statusCategory == "done"  (상태명 하드코딩 금지)
  - SP=0 은 수학적으로 무해, 추가로 Bug/Ops 이슈타입은 이중 안전장치로 제외
  - mock 라벨 SP 는 분모에 포함하되 별도 항목으로 가시화
  - SP 누락(None) 티켓 기본값: Bug -> 0, 나머지 -> 1
    (명시적으로 0 이 입력된 티켓은 그대로 0. '누락'과 '명시적 0' 을 구분)
"""

EXCLUDE_ISSUETYPES = {"Bug", "Ops", "운영"}
MOCK_LABEL = "mock"


def sp_of(issue):
    """SP 값. 누락(None)이면 기본값 적용: Bug -> 0, 나머지 -> 1."""
    sp = issue.get("sp")
    if sp is None:
        return 0 if issue.get("type") == "Bug" else 1
    return sp


def epic_progress(issues):
    total_sp = 0.0
    done_sp = 0.0
    mock_sp = 0.0
    counted = 0
    excluded = 0
    for it in issues:
        itype = it.get("type", "")
        if itype in EXCLUDE_ISSUETYPES:      # 이중 안전장치
            excluded += 1
            continue
        sp = sp_of(it)                       # 누락 시 기본값(Bug=0, 나머지=1)
        total_sp += sp
        counted += 1
        if MOCK_LABEL in (it.get("labels") or []):
            mock_sp += sp
        if it.get("statusCategory") == "done":
            done_sp += sp
    pct = (done_sp / total_sp * 100) if total_sp > 0 else 0.0
    return {
        "doneSp": round(done_sp, 1),
        "totalSp": round(total_sp, 1),
        "mockSp": round(mock_sp, 1),
        "progressPct": round(pct, 1),
        "countedIssues": counted,
        "excludedIssues": excluded,
    }


def count_by_type(issues):
    """이슈 타입별 개수 (기능3 워크로드용). Epic/Task/Sub-task 등."""
    out = {}
    for it in issues:
        t = it.get("type", "Unknown")
        out[t] = out.get(t, 0) + 1
    return out

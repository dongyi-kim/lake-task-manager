"""
Fake world 의 다양성 콘텐츠 풀 — description / comment / activity / confluence.
모든 함수는 random.Random(rng) 을 받아 결정적으로 문구를 생성한다.
언어는 한국어/영어만.
"""

ROLES = ["데이터 엔지니어", "분석가", "플랫폼 운영자", "서비스 개발자", "PMO"]

# ── description: issuetype 별로 서로 다른 양식 ──
_STORY_GOALS = [
    "대용량 테이블을 증분으로 적재", "스키마 변경을 무중단으로 반영", "쿼리 결과를 캐시로 가속",
    "권한을 역할 기반으로 제어", "리니지를 자동 수집", "대시보드에 실시간 위젯을 노출",
]
_STORY_BENEFIT = [
    "야간 배치 지연을 없앤다", "장애 시 롤백을 빠르게 한다", "분석가가 셀프서비스로 조회한다",
    "감사 추적을 남긴다", "운영 비용을 줄인다",
]
_BUG_SYMPTOM = [
    "대량 적재 시 NPE 발생", "경계값에서 집계가 어긋남", "동시 실행 시 데드락",
    "특정 타임존에서 날짜가 하루 밀림", "재시도 후 중복 레코드",
]
_TASK_ITEMS = [
    "설정 파라미터 외부화", "모니터링 지표 추가", "배포 스크립트 정리", "인덱스 재설계",
    "롤백 절차 문서화", "테스트 픽스처 보강", "알림 임계값 조정",
]
_SPIKE_Q = [
    "Iceberg vs Delta 벤치마크로 선택", "CDC 도구 3종 PoC 비교", "캐시 무효화 전략 검토",
    "권한 모델을 ABAC 로 갈지 조사",
]


def description(rng, itype):
    if itype == "Epic":
        return (f"[목표] {rng.choice(_STORY_GOALS)}.\n"
                f"[범위] 관련 파이프라인/스키마/권한 전반. 여러 파트가 가중치로 참여.\n"
                f"[완료 기준] 하위 티켓 SP 롤업 100% 및 운영 이관.")
    if itype == "Bug":
        return (f"[증상] {rng.choice(_BUG_SYMPTOM)}.\n"
                f"[재현] 1) 데이터 적재 2) 집계 실행 3) 결과 확인\n"
                f"[기대] 정상 집계  [실제] 불일치/예외\n"
                f"[환경] Jira DC 8.20.8 / 스테이징")
    if itype == "Sub-Task":
        return f"상위 작업의 세부 단계: {rng.choice(_TASK_ITEMS)}."
    if itype == "Task":
        picks = rng.sample(_TASK_ITEMS, k=min(3, len(_TASK_ITEMS)))
        return "[체크리스트]\n" + "\n".join(f"- [ ] {p}" for p in picks)
    if itype == "Spike":
        return f"[조사] {rng.choice(_SPIKE_Q)}.\n[산출물] 결정 기록(ADR) 1건."
    # Story (기본)
    role = rng.choice(ROLES)
    return (f"As a {role}, I want {rng.choice(_STORY_GOALS)} "
            f"so that {rng.choice(_STORY_BENEFIT)}.\n"
            f"[Acceptance]\n- 정상 경로 검증\n- 예외 처리\n- 관측성 지표 추가")


# ── comment: 유형 다양 ──
_COMMENT_TYPES = [
    ("standup", "데일리: 어제 {a} 진행, 오늘 마무리 예정. 블로커 없음."),
    ("blocker", "블로커: {m} 모듈 의존 API 대기 중. @{who} 확인 부탁."),
    ("question", "질문: 이 케이스 마감일 기준이 스프린트 종료인가요, 릴리스인가요?"),
    ("review", "리뷰: 로직 OK. 다만 예외 처리와 로그 레벨만 보완 요청."),
    ("qa", "QA: 회귀 3건 통과, 경계값 1건 재현되어 재수정 필요."),
    ("decision", "결정: 캐시 TTL 15분으로 합의. ADR 에 반영함."),
    ("mention", "@{who} 이 부분 스키마 영향 있어 크로스체크 부탁드립니다."),
    ("dependency", "선행: 카탈로그 등록이 먼저라 순서 조정했습니다."),
    ("transition", "상태 변경: In Progress → 리뷰 대기. PR 링크 첨부."),
]


def comments(rng, authors, n):
    """[(kind, text)] n개. author/date 는 world 가 채운다."""
    out = []
    for _ in range(n):
        kind, tmpl = rng.choice(_COMMENT_TYPES)
        who = rng.choice(authors) if authors else "pmo"
        text = tmpl.format(a=rng.choice(_TASK_ITEMS), m=rng.choice(["ETL", "Catalog", "Runtime"]),
                           who=who)
        out.append((kind, text))
    return out


# ── activity / confluence ──
ACTIVITY_KINDS = ["created", "commented", "transitioned", "assigned", "logged work", "resolved"]

_CONF_TITLES = [
    "아키텍처 결정 기록(ADR)", "스프린트 회고", "운영 런북", "설계 리뷰 노트",
    "온보딩 가이드", "장애 포스트모템", "API 명세 초안", "데이터 계약(Contract)",
]
_CONF_SPACES = ["DL", "PMO", "ARCH", "OPS"]
_CONF_ACTS = ["created", "edited", "commented"]


def conf_title(rng):
    return rng.choice(_CONF_TITLES)


def conf_space(rng):
    return rng.choice(_CONF_SPACES)


def conf_action(rng):
    return rng.choice(_CONF_ACTS)

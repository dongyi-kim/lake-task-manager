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


# 티켓 상세 다이얼로그 검증용 이미지 — 앱 static 제공(오프라인 렌더). mock/local 전용, prod 실데이터 무관.
_IMG = "/ticket-sample.svg"
# Confluence 9.x 신형 링크(뱃지) 검증용 — /spaces/{space}/pages/{id}/{title-slug}.
#   프론트가 슬러그(마지막 경로)를 문서 제목으로 씀(내부 <a> 텍스트 무시). space 는 jira 와 달라도 됨.
def _conf(title, pid, space="DATAENG"):
    slug = title.replace(" ", "+")
    return f"https://confluence.corp.example/spaces/{space}/pages/{pid}/{slug}"


def description(rng, itype, mention=None):
    """Jira wiki 마크업 description. 티켓 상세 다이얼로그(renderedFields→HTML)에서
    table/image/code/blockquote/panel/callout/list/heading + 맨션([~user])·Confluence 링크(뱃지)가
    골고루 나오도록 타입별로 구성. mention: 맨션으로 넣을 사용자 id(있으면)."""
    at = f"[~{mention}]" if mention else "담당자"
    if itype == "Epic":
        goal = rng.choice(_STORY_GOALS)
        return "\n".join([
            "h2. 목표",
            f"이 Epic 은 *{goal}* 을(를) 달성한다. 총괄 {at}.",
            "",
            "{panel:title=완료 기준}",
            "* 하위 티켓 SP 롤업 100%",
            "* 운영 이관 및 런북 작성",
            "{panel}",
            "",
            "h3. 범위 매트릭스",
            "||모듈||역할||가중치||",
            "|Ingestion|수집 파이프라인|3|",
            "|Catalog|메타데이터 등록|2|",
            "|Governance|권한/감사|1|",
            "",
            "{info}",
            f"설계 배경은 [문서 링크|{_conf('아키텍처 결정 기록', 42011)}] 참고. 담당 {at}.",
            "{info}",
        ])
    if itype == "Bug":
        sym = rng.choice(_BUG_SYMPTOM)
        return "\n".join([
            "h3. 증상",
            f"{sym}. 재현 로그는 아래 스택을 확인.",
            "",
            "{code:text}",
            "ERROR c.s.etl.Loader - batch failed",
            "java.lang.NullPointerException: rows == null",
            "\tat c.s.etl.Loader.merge(Loader.java:142)",
            "{code}",
            "",
            "h3. 재현 절차",
            "# 대량 데이터 적재",
            "# 집계 배치 실행",
            "# 결과 조회 → 불일치 확인",
            "",
            "{warning}",
            f"운영 반영 전 *반드시* 스테이징에서 회귀 확인. 데이터 유실 위험. {at} 확인 요망.",
            "{warning}",
            "",
            "||항목||값||",
            "|버전|Jira DC 8.20.8|",
            "|영역|스테이징|",
            "",
            f"상위 Epic [DL-101|https://jira.corp.example/browse/DL-101] · 런북 [장애 대응 절차|{_conf('장애 대응 절차', 42012, 'OPS')}]",
            f"스크린샷: !{_IMG}!",
        ])
    if itype == "Sub-Task":
        item = rng.choice(_TASK_ITEMS)
        return "\n".join([
            f"상위 작업의 세부 단계: *{item}*. 리뷰어 {at}.",
            "",
            "관련 명령:",
            "{code:bash}",
            "make deploy ENV=staging",
            "{code}",
        ])
    if itype == "Task":
        picks = rng.sample(_TASK_ITEMS, k=min(3, len(_TASK_ITEMS)))
        return "\n".join([
            "h3. 체크리스트",
            *[f"* {p}" for p in picks],
            "",
            "{note}",
            f"설정 값은 {{{{config/jira.yml}}}} 에 외부화한다. 가이드: [설정 가이드|{_conf('설정 가이드', 42013)}]. 담당 {at}.",
            "{note}",
        ])
    if itype == "Spike":
        q = rng.choice(_SPIKE_Q)
        return "\n".join([
            f"h3. 조사: {q}",
            "",
            "||옵션||장점||리스크||",
            "|A안|성숙도 높음|비용 큼|",
            "|B안|가벼움|기능 부족|",
            "",
            "{tip}",
            f"결정은 ADR 1건으로 남긴다. 참고: [벤치마크 노트|{_conf('벤치마크 노트', 42014, 'ARCH')}]. 검토 {at}.",
            "{tip}",
        ])
    # Story (기본)
    role = rng.choice(ROLES)
    return "\n".join([
        "{quote}",
        f"As a {role}, I want {rng.choice(_STORY_GOALS)} so that {rng.choice(_STORY_BENEFIT)}.",
        "{quote}",
        "",
        "h3. 인수 조건",
        "* 정상 경로 검증",
        "* 예외 처리 및 재시도",
        "* 관측성 지표 추가",
        "",
        f"기획 문서 [요구사항 정의서|{_conf('요구사항 정의서', 42015, 'PMO')}] 참고. 상위 [DL-101|https://jira.corp.example/browse/DL-101]. 리뷰 {at}.",
        "",
        "예시 응답:",
        "{code:json}",
        '{"status": "ok", "rows": 128}',
        "{code}",
        "",
        f"와이어프레임: !{_IMG}!",
    ])


# ── comment: 유형 다양 ──
# 일부 템플릿은 Jira wiki 맨션([~user])·Confluence 링크를 포함 → 상세 다이얼로그에서 뱃지로 렌더.
_COMMENT_TYPES = [
    ("standup", "데일리: 어제 {a} 진행, 오늘 마무리 예정. 블로커 없음."),
    ("blocker", "블로커: {m} 모듈 의존 API 대기 중. [~{who}] 확인 부탁."),
    ("question", "질문: 이 케이스 마감일 기준이 스프린트 종료인가요, 릴리스인가요?"),
    ("review", "리뷰: 로직 OK. 다만 예외 처리와 로그 레벨만 보완 요청. cc [~{who}]"),
    ("qa", "QA: 회귀 3건 통과, 경계값 1건 재현되어 재수정 필요."),
    ("decision", "결정: 캐시 TTL 15분으로 합의. 회의록 [minutes|https://confluence.corp.example/spaces/PMO/pages/42016/스프린트+회의록] 에 반영함."),
    ("mention", "[~{who}] 이 부분 스키마 영향 있어 크로스체크 부탁드립니다. 관련 문서 [설계 노트|https://confluence.corp.example/spaces/DATAENG/pages/42017/설계+노트]."),
    ("dependency", "선행: 카탈로그 등록([DL-101|https://jira.corp.example/browse/DL-101])이 먼저라 순서 조정했습니다."),
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
    "배포 파이프라인 가이드", "데이터 카탈로그 표준", "SLA/SLO 정의서", "보안 점검 체크리스트",
    "쿼리 성능 튜닝 노트", "스키마 마이그레이션 계획", "권한 관리 정책", "모니터링 대시보드 설명",
    "인시던트 대응 플레이북", "용량 산정 워크시트", "릴리스 노트 템플릿", "회의록 - 주간 싱크",
]
# 스페이스 키 → 표시 이름(경로 맨 끝, 루트)
_CONF_SPACES = ["DL", "PMO", "ARCH", "OPS"]
_CONF_SPACE_NAMES = {"DL": "데이터플랫폼", "PMO": "PMO", "ARCH": "아키텍처", "OPS": "운영"}
# 스페이스별 폴더 경로 후보 — [최상위 … 직계부모] 순(실 Confluence ancestors 순서).
# 일부러 깊이를 다양하게(0~3단) 둬서 '중간 생략(…)' UI 를 검증할 수 있게 한다.
_CONF_FOLDERS = {
    "DL":   [[], ["엔지니어링"], ["엔지니어링", "파이프라인"],
             ["엔지니어링", "파이프라인", "실시간 처리"], ["표준·정책"], ["표준·정책", "데이터 거버넌스"]],
    "PMO":  [[], ["프로젝트 관리"], ["프로젝트 관리", "주간 보고"], ["산출물"]],
    "ARCH": [[], ["설계"], ["설계", "결정 기록"], ["설계", "리뷰"]],
    "OPS":  [[], ["운영 절차"], ["운영 절차", "장애 대응"], ["운영 절차", "장애 대응", "포스트모템"], ["모니터링"]],
}
_CONF_ACTS = ["created", "edited", "commented"]


def conf_title(rng):
    return rng.choice(_CONF_TITLES)


def conf_space(rng):
    return rng.choice(_CONF_SPACES)


def conf_space_name(space):
    return _CONF_SPACE_NAMES.get(space, space)


def conf_ancestors(rng, space):
    """문서의 상위 폴더 경로 — [최상위 … 직계부모] 순(스페이스 루트는 제외, 경로 조립 시 붙인다)."""
    return list(rng.choice(_CONF_FOLDERS.get(space, [[]])))


def conf_action(rng):
    return rng.choice(_CONF_ACTS)


_CONF_BODY = [
    "{t} 문서. 배경·결정사항·영향 범위(파이프라인/스키마/권한)와 롤백 절차를 정리한다.",
    "{t} 관련 설계 논의와 대안 비교, 선택 근거(ADR)를 담는다. 후속 액션 아이템 포함.",
    "{t} 운영 절차와 체크리스트, 장애 시 대응 순서 및 모니터링 지표를 기술한다.",
    "{t} 스프린트 회고: 잘된 점/개선점/액션. 다음 스프린트 반영 사항을 기록한다.",
]


def conf_body(rng, title):
    """Confluence 페이지 본문(검색용 text). 제목을 포함해 text~ 검색이 걸리게 한다."""
    return rng.choice(_CONF_BODY).format(t=title)

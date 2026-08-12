# app/domain — 도메인 로직 (진척 롤업 · 기능별 조립)

PMO 대시보드의 **계산·조립 로직**. 정규화된 이슈를 입력받아 화면이 쓰는 모델을 만든다.
`progress`/`rollup` 은 **순수 함수**(네트워크·인증 의존 0), 나머지는 `client`(app/jira)를 얇게 orchestrate 한다.

## 파일
- **progress.py** — Epic SP 진척률 롤업(`epic_progress`, 순수). 완료 판정 `statusCategory=="done"`.
  또한 **단일 소스 상수**: `VOC_COMPONENT`(VoC 판정) · `CAT_MAP`/`norm_cat`(status→'todo/inprogress/done').
- **rollup.py** — Epic 진척률 + wbs_config → **WBS/Module/PMO 가중 조합**(순수).
- **workload.py** — 기능3 인력 워크로드 조립(모듈 목록·로스터·인력별 번들).
- **vit.py** — 기능2 PMO_VIT 현안 조립(shell/module/detail, 뉴스 컷오프, 평탄화).
- **mytasks.py** — '내 Task' 트리 빌드 + 우선순위(`pri_rank`)/카테고리 랭킹 + 롤업.
- **search.py** — 통합 검색(Jira/Confluence/Bitbucket) + 멘션/사용자 제안 + 인력 해석.
- **names.py** — `real_name`(displayName 에서 회사 제거) + `staff_kind`(x*/i* 개발/운영 뱃지).

## 규칙
- **progress.py·rollup.py 는 순수 함수** — 입력=정규화 이슈/plan, 출력=dict. 네트워크·인증·`client` 금지.
- status 이름 하드코딩 금지 → `norm_cat`(= progress) 사용. VoC 판정은 `VOC_COMPONENT` 단일 소스.
- 완료 판정·SP 기본값 등 도메인 규칙은 AGENTS.md §6 을 따른다.

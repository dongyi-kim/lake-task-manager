# Agent improvement research archive

LakeTaskManager Agent의 prompt 언어 구성, role contract, 구조적 품질, 성능 및 토큰 사용량을 비교한 실험 자료를 보관한다. 프로덕션 동작 규칙과 작성 표준은 [`app/agent/AGENT.md`](../../app/agent/AGENT.md)를 기준으로 하며, 이 디렉터리의 자료는 재현과 의사결정을 위한 연구 기록이다.

## 디렉터리

- `reports/`: 비교 보고서, 사람 관점 정성평가, 설계·성능 기록
- `evaluations/`: versioned battery의 경량 채점 보고서. commit·평가 version·manifest와 case별 점수 보존
- `scripts/`: 당시 실험군을 순차 실행한 보조 스크립트

## 주요 보고서

- [`AGENT-STRUCTURAL-QUALITY-TOKEN-V3-REPORT.md`](reports/AGENT-STRUCTURAL-QUALITY-TOKEN-V3-REPORT.md): V2와 V3의 최종 구조·품질·토큰 비교
- [`AGENT-ROLE-CONTRACT-V2-COMPARISON.md`](reports/AGENT-ROLE-CONTRACT-V2-COMPARISON.md): BASE, KO-R, V2 role contract 비교
- [`PROMPT-KO-REFACTORED-COMPARISON.md`](reports/PROMPT-KO-REFACTORED-COMPARISON.md): 한국어 재작성 버전 비교
- [`PROMPT-LANGUAGE-COMPARISON.md`](reports/PROMPT-LANGUAGE-COMPARISON.md): BASE, EN, KO, GUIDE 언어 구성 비교
- [`PROMPT-LANGUAGE-OUTPUTS.md`](reports/PROMPT-LANGUAGE-OUTPUTS.md): 언어 비교 실제 출력 전문

실제 답변의 차이, 정량 측정값, 사람 관점 평가는 위 보고서에 통합해 보존한다. 실행 로그와 raw JSON은
중복·로그성 산출물이므로 저장소에는 두지 않으며 `logs/`, `results/`는 `.gitignore` 대상이다. 필요하면
`scripts/`와 현재 배터리 도구로 다시 생성한다.

새 비교 실험은 [`app/agent/EVALUATION.md`](../../app/agent/EVALUATION.md)의 versioned 표준을 사용한다.
보고서와 PR Description에 protocol/rubric/battery version 및 측정 기준을 포함하고, version이 없는
과거 점수나 closure 결과를 섞은 점수는 현재 qualification 결과와 직접 증감 비교하지 않는다.

실행별 raw response·trace·usage·debug JSON은 `.cache/agent-evaluation/<runGroupId>/`에만 저장하고 git에
담지 않는다. Codex/Claude 직접 채점이 끝나면 `research/agent-improvement/evaluations/`에 경량 Markdown을
반드시 남긴다. 그래야 이후 같은 battery/case만 focused로 재실행해 candidate commit과 version이 일치하는
과거 결과를 찾을 수 있다.

경량 보고서는 공통 5축 점수만 남기지 않는다. suite·case별 특수 검토요소의 `pass/minor/major/na`,
실제 근거, `specializedReviewSpecSha256`도 포함한다. 히스토리 case라면 기대 ticket과 사건 순서, 조사
case라면 내부 source·외부 검색어·URL·검색 실패 한계까지 기록한다. 이 계약이 바뀐 결과를 과거 점수와
동일 기준으로 직접 비교하지 않는다.

배터리 raw의 `격리` 기록에서 case별 private cache, `worldSha256`, `providerStoreSha256` 보존 여부도
확인한다. 이전 case의 cache·대화 state·mock write가 다음 case에 남은 실행은 점수화하지 않는다.

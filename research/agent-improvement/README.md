# Agent improvement research archive

LakeTaskManager Agent의 prompt 언어 구성, role contract, 구조적 품질, 성능 및 토큰 사용량을 비교한 실험 자료를 보관한다. 프로덕션 동작 규칙과 작성 표준은 [`app/agent/AGENT.md`](../../app/agent/AGENT.md)를 기준으로 하며, 이 디렉터리의 자료는 재현과 의사결정을 위한 연구 기록이다.

## 디렉터리

- `reports/`: 비교 보고서, 사람 관점 정성평가, 설계·성능 기록
- `evaluations/`: versioned battery의 경량 채점 보고서. commit·평가 version·manifest와 case별 점수 보존
- `scripts/`: 당시 실험군을 순차 실행한 보조 스크립트

## 주요 보고서

- [`evaluations/maxis-role-component-20260921-policy-query.md`](evaluations/maxis-role-component-20260921-policy-query.md): v20 검색 전용 단위 분리; 실제 no-hit 5→0이나 정상 답변 0/11·지연 증가, 비활성 유지. 별도 Editor 후보와 다음 단위별 개선 포함
- [`evaluations/maxis-role-component-20260921-query-suffix.md`](evaluations/maxis-role-component-20260921-query-suffix.md): 선행 접미어 후보의 개선 실패와 native 정책 검색·Editor 오류 진단
- [`maxis-role-evaluation-20260917.md`](maxis-role-evaluation-20260917.md): 8개 Role·11개 고정 합성 사례의 실제 원격 응답 평가 경로; 운영 활성화 없는 독립 component 진단
- [`maxis-performance-baseline-20260917.md`](maxis-performance-baseline-20260917.md): 전체 성능 계획 1차 구현; 8개 Role·11개 오프라인 입력 진단, 정보 누락 재현과 기존 실측 재집계. 실 LLM/전체 업무 품질 미평가
- [`maxis-editor-writing-quality-20260916.md`](maxis-editor-writing-quality-20260916.md): v19 작성 계약 단순화; 직접 2.14→2.40/5, 유용한 결과 0/5로 비활성 유지
- [`maxis-editor-branches-quality-20260915.md`](maxis-editor-branches-quality-20260915.md): 이전 v16 본문·댓글·제어 분리, 실제 1요청1LLM·토큰49.6% 감소; 새 v15/v16 직접3.38→3.52/5, 완성초안0·필수질문1로 품질 미통과·비활성 유지
- [`maxis-editor-scope-quality-20260915.md`](maxis-editor-scope-quality-20260915.md): 이전 v15 대상·계층·출처 투영, 당시 baseline/v14/v15 보존; 직접 2.52→3.04→3.54/5, 최종 초안 1건·구체 확인 질문 1건, 비활성 유지
- [`maxis-editor-units-quality-20260914.md`](maxis-editor-units-quality-20260914.md): 이전 v13 원문 단위·서식 개선, 당시 baseline 대비 자동 0/5→3/5·직접 사용 가능 0/5→1/5; 비활성 유지
- [`maxis-editor-plan-quality-20260914.md`](maxis-editor-plan-quality-20260914.md): 앞선 v11 구조적 조립 실험, Guard 1/5·사용 가능 0/5, 직접 판독·실패 원인
- [`maxis-dify-quality-20260914.md`](maxis-dify-quality-20260914.md): MAXIS/Dify 실측 품질 실패·58-case 재사용·Editor v8/v9 비교
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
담지 않는다. 같은 run group·suite·repeat의 raw/`.claim`은 덮어쓰지 않고 재실행에 새 식별자를 쓴다.
Codex/Claude 직접 채점이 끝나면 `research/agent-improvement/evaluations/`에 경량 Markdown을
반드시 남긴다. 그래야 이후 같은 battery/case만 focused로 재실행해 candidate commit과 version이 일치하는
과거 결과를 찾을 수 있다.

경량 보고서는 공통 5축 점수만 남기지 않는다. suite·case별 특수 검토요소의 `pass/minor/major/na`,
실제 근거, `specializedReviewSpecSha256`도 포함한다. 히스토리 case라면 기대 ticket과 사건 순서, 조사
case라면 내부 source·외부 검색어·URL·검색 실패 한계까지 기록한다. 이 계약이 바뀐 결과를 과거 점수와
동일 기준으로 직접 비교하지 않는다.

배터리 raw의 `격리` 기록에서 case별 private cache, `worldSha256`, `providerStoreSha256` 보존 여부도
확인한다. 이전 case의 cache·대화 state·mock write가 다음 case에 남은 실행은 점수화하지 않는다.

# Agent improvement research archive

이 디렉터리에는 기존 Agent 연구 기록이 남아 있다. 기존 기록은 당시 구현·평가 조건에 관한
자료이며 현재 동작이나 품질 승인으로 간주하지 않는다. 프로덕션 규칙은
[`app/agent/AGENT.md`](../../app/agent/AGENT.md)를 따른다.

## 새 작업의 저장 원칙

- 최신 코드·계약·재사용 가능한 평가 harness와 test만 PR에 포함한다.
- 실행별 raw, 평가 결과, 직접 채점 보고서, 실험용 대체 구현과 캡처는 ignored
  `.cache/`에 보존한다. 최신 평가 결과도 tracked 문서로 만들지 않는다.
- 평가 원본은 `.cache/agent-evaluation/<runGroupId>/`에 두고 같은 실행을 덮어쓰지 않는다.
  결과 요약·제약·남은 작업은 PR 본문과 Changelog에서 관리한다.
- commit·평가 version·manifest·case·실패·재시도·직접 검토 근거를 함께 보존하며,
  비교 불가능한 실행이나 focused 재실행으로 과거 full-run 결과를 대체하지 않는다.

평가 방법은 [EVALUATION.md](../../app/agent/EVALUATION.md)를 따른다.
MAXIS의 현재 설계는 [적용 가이드](../../docs/MAXIS_DIFY_MIGRATION.md)에 있다.

## 기존 보고서

- [`AGENT-STRUCTURAL-QUALITY-TOKEN-V3-REPORT.md`](reports/AGENT-STRUCTURAL-QUALITY-TOKEN-V3-REPORT.md): V2와 V3의 최종 구조·품질·토큰 비교
- [`AGENT-ROLE-CONTRACT-V2-COMPARISON.md`](reports/AGENT-ROLE-CONTRACT-V2-COMPARISON.md): BASE, KO-R, V2 role contract 비교
- [`PROMPT-KO-REFACTORED-COMPARISON.md`](reports/PROMPT-KO-REFACTORED-COMPARISON.md): 한국어 재작성 버전 비교
- [`PROMPT-LANGUAGE-COMPARISON.md`](reports/PROMPT-LANGUAGE-COMPARISON.md): BASE, EN, KO, GUIDE 언어 구성 비교
- [`PROMPT-LANGUAGE-OUTPUTS.md`](reports/PROMPT-LANGUAGE-OUTPUTS.md): 언어 비교 실제 출력 전문

기존 `reports/`, `evaluations/`, `scripts/`는 이번 MAXIS 정리에서 변경하지 않는다.
이번 PR의 MAXIS 실행별 기록은 정리 전 Git 이력과 ignored cache에서 복구할 수 있다.

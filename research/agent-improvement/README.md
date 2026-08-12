# Agent improvement research archive

LakeTaskManager Agent의 prompt 언어 구성, role contract, 구조적 품질, 성능 및 토큰 사용량을 비교한 실험 자료를 보관한다. 프로덕션 동작 규칙과 작성 표준은 [`app/agent/AGENT.md`](../../app/agent/AGENT.md)를 기준으로 하며, 이 디렉터리의 자료는 재현과 의사결정을 위한 연구 기록이다.

## 디렉터리

- `reports/`: 비교 보고서, 사람 관점 정성평가, 설계·성능 기록
- `results/`: 실제 OpenAI API 배터리 응답 전문과 정량 측정 JSON
- `logs/`: 초기 언어 실험군 실행 로그
- `scripts/`: 당시 실험군을 순차 실행한 보조 스크립트

## 주요 보고서

- [`AGENT-STRUCTURAL-QUALITY-TOKEN-V3-REPORT.md`](reports/AGENT-STRUCTURAL-QUALITY-TOKEN-V3-REPORT.md): V2와 V3의 최종 구조·품질·토큰 비교
- [`AGENT-ROLE-CONTRACT-V2-COMPARISON.md`](reports/AGENT-ROLE-CONTRACT-V2-COMPARISON.md): BASE, KO-R, V2 role contract 비교
- [`PROMPT-KO-REFACTORED-COMPARISON.md`](reports/PROMPT-KO-REFACTORED-COMPARISON.md): 한국어 재작성 버전 비교
- [`PROMPT-LANGUAGE-COMPARISON.md`](reports/PROMPT-LANGUAGE-COMPARISON.md): BASE, EN, KO, GUIDE 언어 구성 비교
- [`PROMPT-LANGUAGE-OUTPUTS.md`](reports/PROMPT-LANGUAGE-OUTPUTS.md): 언어 비교 실제 출력 전문

`results/`에는 최종 채택 결과뿐 아니라 실패 원인을 좁히기 위한 focused, regression, closure 실행도 함께 남긴다. 최종 결론을 재계산할 때는 각 보고서에 명시된 primary run과 closure 대체 규칙을 사용한다.

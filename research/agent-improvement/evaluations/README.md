# Versioned Agent evaluations

Codex 또는 Claude가 `.cache/agent-evaluation/<runGroupId>/`의 raw output을 직접 읽고 작성한 경량 평가
보고서를 보관. raw JSON·trace·debug log는 이 디렉터리에 복사하지 않음

파일명 권장 형식: `YYYY-MM-DD-<candidate>-<suite-or-full>-<run-kind>.md`

각 보고서 필수 항목

- candidate commit과 `promptVersion`
- `protocolVersion`, `rubricVersion`, suite별 `batteryVersion`
- `batteryManifestSha256`, `dataManifestSha256`, `comparabilityKey`
- model routing, provider, run kind, repeat와 실행 case
- battery·case·축별 점수와 짧은 판단 근거
- raw cache 상대 경로와 과거 비교 보고서 경로

focused 재실행은 같은 case의 변화 확인용. 과거 full-run 점수를 교체하거나 qualification 근거로 표기하지 않음

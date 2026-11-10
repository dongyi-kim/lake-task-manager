# Versioned Agent evaluations

Codex 또는 Claude가 `.cache/agent-evaluation/<runGroupId>/`의 raw output을 직접 읽고 작성한 경량 평가
보고서를 보관. raw JSON·trace·debug log는 이 디렉터리에 복사하지 않음

파일명 권장 형식: `YYYY-MM-DD-<candidate>-<suite-or-full>-<run-kind>.md`

각 보고서 필수 항목

- candidate commit과 `promptVersion`
- `protocolVersion`, `rubricVersion`, suite별 `batteryVersion`
- `batteryManifestSha256`, `dataManifestSha256`, `benchmarkKey`, `candidateKey`,
  `comparabilityKey`, `evaluationHarnessTreeSha256`

Protocol 3.0부터 `benchmarkKey`는 동일 case/data/rubric/실행 정책을, `candidateKey`는
model/provider/structured backend/prompt/runtime tree를 식별한다. 서로 다른 모델 후보의
paired 비교는 `benchmarkKey`가 같을 때만 수행한다. `comparabilityKey`는 두 키를 결합한
동일 후보 반복 실행 식별자이며, 교차 모델 pairing 기준으로 사용하지 않는다.
평가 launcher·isolation·runner 코드가 dirty tree에서 바뀐 실행은
`evaluationHarnessTreeSha256`가 달라져 같은 benchmark로 묶이지 않는다.
- model routing, provider, run kind, repeat와 실행 case
- battery·case·축별 점수와 짧은 판단 근거
- raw cache 상대 경로와 과거 비교 보고서 경로

focused 재실행은 같은 case의 변화 확인용. 과거 full-run 점수를 교체하거나 qualification 근거로 표기하지 않음

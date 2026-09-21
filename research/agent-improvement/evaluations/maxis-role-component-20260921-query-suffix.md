# MAXIS Role component — 2026-09-21 정책 접미어 후보

## 판정

정책 검색어를 사용자 요청 뒤에 붙이는 것만으로 개선되지 않았다. **후보 채택·운영 활성화 보류**.
기존 full-run 실패를 지우지 않고, 다음 후보는 검색 전용 입력을 분리해 별도 전체 실행으로 확인한다.

## 측정 식별자·비교 가능성

- candidate commit: `064da48962693bf7e0f860886ca04274ddfb9f8c`, dirty=false.
- protocol `3.0.0`, rubric `2.0.0`, `maxis-role-component` battery `1.0.0`.
- batteryManifestSha256: `967b14832b8eea63aff0c46cf66aefc4288679bcd171e03fa9d152c4a4a8f555`.
- specializedReviewSpecSha256: `aa706802c1770993d2fc10e3cdf2e6edd8a9ca0dd59329080db867db2c98ceff`.
- dataManifestSha256: `d14b02a0c052ef3ae0854259d0fe43025682129578181bde417161ba445ae16d`.
- benchmarkKey: `61a1e6be9a6fd29140c5ad7e5a41406f61ad15cebe34cc09fee71cbe2beefcb9`.
- candidateKey: `a5d13f859f2c9d13c57b28723f64f26a6be1b8a3501f5439e683dc0675312bd2`.
- comparabilityKey: `b8b89015a6a6bcce1f6f33da6171b5163417f7b9af1c8610eb089fd2f1b72c2c`.
- raw: `.cache/agent-evaluation/maxis-roles-20260921T001927Z-a5a60f5c/candidate.json`.
- raw SHA-256: `c8b5e8139dfbc443268574572909bb89fc3238625f701f23266332894997c18c`.

[9/17 baseline](maxis-role-component-20260917-baseline.md)과 benchmarkKey가 같다.
11/11 case, 각 1회, 고정 순서, retry/fallback 없음, stateless 독립 평가 process.
DEV v19 `gpt-5.4-mini` 설정, 원래 LTM의 실 Jira/전체 workflow/UI 평가와 다르다.
4일 간격의 비균형 exploratory 실행이므로 지연·token 차이를 인과적 개선이나 qualification으로 해석하지 않는다.

실행 도중/직후 helper SHA-256 `440a2aafe2c6ef6b6c113884805af51799fdac5370a7fcbb8e4a7efb4b77dcee`
및 clean HEAD를 확인했다. 기존 하네스는 절대 경로의 `.cache`를 제외하므로 이 worktree에서는
candidate tree digest가 앱 소스 전체를 덮지 못한다. 실행 중 파일 감시 목록에도 helper가 직접
포함되지 않는다. 따라서 해당 digest를 소스 동일성 증거로 사용하지 않고 clean commit과 별도
helper hash로 보완했다. 하네스를 이 후보에서 변경하면 benchmark 식별자가 바뀌므로 수정은
별도 baseline 재측정 과제로 남긴다.

## 실제 출력·case별 진단

Codex 메인 에이전트가 전송 query·sources 수·freshness·partial과 응답 전문을 직접 읽고,
별도 Codex가 native 실행과 오류를 진단했다. 비블라인드이며 evaluator의 정확한 model ID는
도구에 노출되지 않았다. LTM LLM judge는 사용하지 않았다. 전체 5축 checklist 채점은 하지 않았으므로
품질 수치·유용성 통과율은 부여하지 않는다. 아래는 기존 case 목표에 대한 질적 실패 진단이다.

| Case / 목표 | 실제 응답 | ms | 관측 token | 직접 판단 |
|---|---|---:|---:|---|
| request / Chrome 2홉 Bug 요청 분해 | blocked / citation_source_unknown | 5,316 | 4,923 | 계획 미제공; 원문 주장 제거돼 오류 문장 확정 불가 |
| query / 데이터 자산 이력 QueryPlan | needs_input / role_knowledge | 911 | 0 | 검색 보강 후에도 정책 no-hit; typed 계획 없음 |
| knowledge / DL-9044 2시간→30분 | blocked / claim_not_grounded | 3,528 | 4,184 | 주기 변경을 보존한 brief 미제공 |
| work / Bug 초안·미확정 필드 처리 | needs_input / role_knowledge | 1,075 | 0 | 업무 질문·초안에 도달하지 못함 |
| auditor / 요청·계층·근거 감사 | needs_input / role_knowledge | 2,494 | 0 | 실 감사 판단 미수행 |
| result / 배치 이력 설명 | blocked / claim_not_grounded | 2,714 | 3,744 | 사용자 대면 결과 없음 |
| portfolio / 5/10=50% 설명 | needs_input / role_knowledge | 904 | 0 | 진척 설명 미제공 |
| editor / 모니터링·30분 보존 | error / bad_request | 4,105 | 미관측 | 아래 native 진단으로 DraftSpec 분류 위반 위치 확인 |
| overflow / 부분 근거 제한 | blocked / claim_not_grounded | 3,249 | 4,037 | 잘린 입력에서 결과 없음; 제한·복구 설명도 없음 |
| stale / 1시간 전 부분 근거 | needs_input / role_knowledge | 1,014 | 0 | 검색에 먼저 막혀 freshness 판단 미검증 |
| missing / 빈 요청·근거 처리 | blocked / source_free_provenance_forbidden | 2,844 | 3,680 | 사실 미출력은 안전하나 구체적인 확인 질문 없음 |

수신된 10건 모두 payload `{}`, claims/citations/payload_claims `[]`.
needs_input 5건의 missing_inputs는 `[role_knowledge]`이며 사용자 소유 업무 정보의 공백이 아니다.
자동 contract 수락 10건 / retrieval 수락 5건 / automaticContractPass 5건은 정상 업무 완료가 아니다.

## 정량·실패·미관측

| 지표 | 9/17 baseline | 접미어 후보 |
|---|---:|---:|
| ok / blocked / needs_input / error | 0 / 5 / 5 / 1 | 0 / 5 / 5 / 1 |
| 요청 wall 합계 ms | 21,697 | 28,154 |
| nearest-rank p50 / p95 ms | 1,967 / 4,973 | 2,714 / 5,316 |
| 10/11 관측 token 부분 합계 | 20,961 | 20,568 |

전체 token 합계·native 호출·비용은 primary raw에서 null. 나중의 native 진단은 보조 증적이며 raw를
덮어써 완전한 usage처럼 만들지 않는다. Core 5건의 prompt_tokens가 모두 3,303으로 보고됐지만,
native prompt 본문은 서로 다르고 실제 guarded input이 포함된다. 입력 누락으로 단정하거나 token 절감을 주장하지 않는다.

## 정책 검색 진단 — 모델 호출 없는 별도 증거

- raw: `.cache/agent-evaluation/maxis-retrieval-diagnosis-20260921T002242Z-f02df9f2/metadata-hit-tests.json`.
- SHA-256: `aaebe6bf45e7f610be6f173cd88d83c5262cc63e63881bb73e09567c6ca9580a`.
- DEV 8개 문서 모두 enabled/indexing completed, 저장된 역할 ACL과 manifest가 정확히 일치.
- 같은 native keyword search, top_k=2, 같은 역할 metadata filter에서 고정 합성 입력 8회.
  지식/권한/워크플로 변경·LLM 호출 없음. Dify 검색 이력은 추가될 수 있다.

| Role | 전체 요청+접미어 | 정책어만 | 정책어 결과 |
|---|---:|---:|---|
| Query | 0 | 1 | 06-data-assets |
| Work | 0 | 2 | 07-ticket-body-guide의 2개 조각 |
| Auditor | 0 | 0 | 한글 정책어만으로도 무결과 |
| PMO | 0 | 2 | 02-progress-formula의 2개 조각 |

따라서 ACL 누락 수정·top_k 확대·Guard 해제가 아니라 **정책 검색과 실제 사용자 의도 입력의 분리**가 다음 후보다.
어떤 조항을 제대로 사용했는지는 문서 hit 수만으로 판정하지 않는다.

## Native 진단과 후속 경계

- raw: `.cache/agent-evaluation/maxis-roles-20260921T001927Z-a5a60f5c/native-diagnostics-20260921.json`.
- SHA-256: `84636d43c9f93395b1432036aa629ec50846de3694533de8d171abf390555701`.
- 11건의 Dify Start pack이 primary raw hash와 일치. no-hit 5건은 실제 Role ACL key/query가 정확하며 LLM 노드 미실행.
- Core request/result 및 Editor selector rendered prompt에 해당 guarded model_input이 정확히 포함됨. 입력 전달 누락 가설 기각.
- Editor는 Knowledge 2건 회수, Knowledge Guard/Editor Input 성공, selector 1회 뒤 DraftSpec Guard 실패.
  모델이 issue 소유자가 없는 Jira wrapper `/title`을 context bucket에 넣었고, 같은 native 입력으로
  `editor_draft_spec.py`의 parent 검사 실패를 오프라인 재현했다. writer 미실행, native 총 token=1,967.
  이는 합성 1건의 관측이며 일반 Editor 품질로 확대하지 않는다.
- 근거 Guard 완화·parent 범위 승격은 하지 않는다. Editor bucket 선택 단순화는 독립 변경으로 분리한다.
- 빈 sources의 일반 계약은 pre-LLM needs_input으로 종료할 여지가 있으나, control-only 예외와 함께 별도 검증해야 한다.

코드 독립 검토는 9/10 두 차례, 관련 회귀 315개 통과. 이는 모델 성능 점수가 아니다.
PROD·키·모델·사용자 앱의 local route는 바뀌지 않았고, 이 접미어 후보만을 개선 완료로 표시하지 않는다.

# MAXIS 성능 개선 — 정책 검색과 사용자 의도 분리

## 결론

**정책 검색 실패 5건을 해소했지만, 최종 답변 품질·속도 개선은 미달이다. 운영 비활성을 유지한다.**

같은 11개 합성 사례에서 검색 때문에 중단하던 Query·Work·Auditor·PMO가 다음 단계로 진행했다.
응답을 받은 10건의 Knowledge ledger와 역할 ACL 검증은 모두 통과했다. 그러나 정상 답변은
여전히 0/11건이며, 출력 형식·사실/인용 결속 실패와 설명 없는 blocked가 남았다.
검색 통과를 업무 성공이나 AI 품질 점수로 바꾸어 보고하지 않는다.

## 구현과 게시 범위

- 브랜치 `codex/maxis-role-policy-retrieval`, 실측 commit `377e9edff7a950028190a1818d22447f54fc90d4`, clean.
- 먼저 [접미어만 추가한 후보](maxis-role-component-20260921-query-suffix.md)를 전체 실행했지만 효과가 없었다.
  해당 실패를 그대로 보존하고, 검색 입력만 분리하는 별도 후보를 구현했다.
- Core·PMO에 결정적 `Policy Query` 노드를 하나씩 추가: 각각 **12노드·12엣지**.
  Query·Work·Auditor·Portfolio의 정확한 role/계약에 한정된 정책어를 사용한다.
- 정책어·선택 함수는 LTM 한 곳이 정본이며 Dify code node는 기계적으로 생성한다.
  추가 LLM 노드·재시도·fallback은 없다. Auditor 정책어는 실제 검색 가능한 `Story Point Epic Link`로 변경했다.
- `Knowledge Guard`는 원래 `sys.query`와 EvidencePack을 검증한다. 사용자 의도·근거·모델 입력,
  역할 ACL·Sentinel·top-k·출력 Guard는 보존한다. DEV/PROD binding 분리도 유지한다.
- DEV v20 게시: `ltm-maxis-dify-2026.09.21-policy-query-v20`.
  Core `290173d5-f950-46d0-869e-7c3b0885049c`, PMO `8bf05f64-8c39-49af-a781-a563ef0e4b27`.
  Editor `0b618d12-4247-45c3-8d5b-1790804621d4`는 재게시하지 않았다.
- 게시 전 기존 published/draft graph와 키 동일성, 게시 후 세 앱 graph와 키 동일성을 확인했다.
  정상 3-app probe **3/3**. active profile은 빈 값, 모든 route는 **local**. PROD·API key·모델·Knowledge 불변.

## 동일 사례의 실제 측정

| 지표 | 접미어 후보 v19 | 검색 분리 v20 |
|---|---:|---:|
| 전체 시도 | 11 | 11 |
| 정책 no-hit로 needs_input | 5 | 0 |
| ACL·검색 ledger 검증 | 5/11 | 10/11 |
| ok / blocked / needs_input / error | 0 / 5 / 5 / 1 | 0 / 10 / 0 / 1 |
| gateway wall 합계 | 28.154초 | 36.704초 |
| nearest-rank p50 / p95 | 2.714 / 5.316초 | 3.084 / 6.085초 |
| 관측 10/11건 token 부분 합계 | 20,568 | 40,584 |

두 실행 모두 고정 순서·각 1회·독립 process이며 retry/fallback이 없다. 같은 날의 순차 탐색 실행이고
균형 순서·반복 검증이 아니므로 지연 차이를 인과적으로 확정하지 않는다. 검색에 막히던 사례가 모델까지
진행하므로 호출량이 늘 수 있다. **속도·토큰 절감을 달성했다고 주장하지 않는다.**
전체 token/비용/native LLM 호출 수는 primary raw에서 미관측(null)이며, 부분 합계가 전체 비용이 아니다.
Core prompt_tokens가 모든 수신 사례에서 3,303으로 보고되는 기존 telemetry 제한도 남아 있다.

## 사례별 직접 진단

Codex 메인 에이전트가 실제 전송 query·source 수·freshness·partial 및 수신 응답 전문을 직접 확인했다.
모델 정확한 ID는 평가 에이전트 도구에 노출되지 않았고, 비블라인드 진단이다. LTM LLM judge는 쓰지 않았다.
공통 5축·specialized checklist 전체 점수는 부여하지 않았으므로 아래는 질적 진단이지 공식 품질 채점이 아니다.

| Case / 요구 | 최종 상태·이유 | ms | 관측 token | 직접 판단 |
|---|---|---:|---:|---|
| request / Chrome 2홉 Bug 분해 | blocked: citation_source_unknown | 4,595 | 4,773 | 유효 계획 없음, 잘못된 출처 식별 |
| query / 데이터 이력 QueryPlan | blocked: payload_type_mismatch | 2,885 | 3,638 | data-assets 검색 성공, typed 계획 실패 |
| knowledge / 2시간→30분 이력 | blocked: payload_claim_text_mismatch | 3,650 | 4,346 | 설명과 claim 문안 결속 실패, brief 없음 |
| work / Bug 초안·미확정값 | blocked: ok_has_missing_inputs | 6,085 | 5,798 | ticket-body-guide 검색 성공, 완료/추가질문 상태 모순 |
| auditor / 초안·근거 감사 | blocked, warnings 없음 | 2,337 | 3,488 | 정책 2종 회수했으나 감사 이유·복구 안내 없음 |
| result / 이력 답변 | blocked: claim_not_grounded | 3,084 | 3,742 | 사용자에게 전달할 설명 없음 |
| portfolio / 5/10=50% 진척 | blocked: claim_not_grounded | 4,158 | 3,745 | progress-formula 회수했으나 근거 있는 설명 없음 |
| editor / 기존 본문 재작성 | error: bad_request | 2,151 | 미관측 | 정상 초안 없음; 미게시 Editor 후보와 구분 |
| overflow / 잘린 근거 처리 | blocked: claim_not_grounded | 3,189 | 4,076 | partial 입력 유지, 제한 설명·복구 결과 없음 |
| stale / 1시간 전 부분 근거 | blocked, warnings 없음 | 2,387 | 3,488 | stale/partial 전달 유지, 판단 이유 없음 |
| missing / 빈 요청·근거 | blocked, warnings 없음 | 2,183 | 3,490 | 사실 발명은 없지만 필요한 확인 질문도 없음 |

수신 10건은 모두 payload `{}`, claims/citations/payload_claims `[]`다. automaticContractPass 10건은
안전한 blocked 응답 형식이 맞다는 의미이며 업무 완료가 아니다. Guard가 지운 원래 후보 문장은 primary
raw에 없으므로 환각과 과도한 거절을 최종 오류 코드만으로 구분하지 않는다.

## 원격 실행 대조 — 추가 모델 호출 없음

별도 Codex가 기존 DEV 실행을 console GET으로 읽어 11건 모두 Start pack hash를 primary raw와
정확히 대조했다. Query는 1개 조각, 나머지 10건은 각각 2개 조각을 회수했다.
Editor의 native hit 관측은 primary에서 수행하지 못한 응답 ledger 검증을 대신하지 않는다.

- 정책어 투영은 정확히 4역할·5사례(Query/Work/Auditor/Portfolio/stale)에만 적용됐다.
  나머지 Core 5건은 검색어 원문 그대로, Editor에는 새 노드가 없다.
- 11/11에서 Knowledge Guard의 원본 query/pack 및 모델용 pack/query 일치.
  모든 LLM rendered prompt가 직전 검증 노드의 입력과 일치하며 미치환 placeholder가 없다.
- native LLM 실행은 각 1회, 총 11회. native 서버 보고 token 합계는 42,551이다.
  이 보조 관측은 primary의 null을 덮어쓰지 않으며 청구 비용·token 계측 정확성을 보증하지 않는다.
- Editor는 DraftSpec Guard의 `invalid_editor_draft_spec`에서 실패했다. selector 1회·1,967 tokens,
  writer 미실행이다. 별도 수정 후보는 아직 이 게시 버전에 포함되지 않았다.
- raw와 같은 디렉터리의 `native-diagnostics-v20-20260921.json`, SHA-256
  `0be35c9b78995f69b0fc6b2630bb056fea0dd6e3a33602eb4b5db8a86e9724b7`.
  기존 raw·접미어 후보 native 증적은 변경하지 않았으며 프롬프트/응답/비밀 전문을 보조 증적에 담지 않았다.

## 재현·증거 식별자

- protocol `3.0.0`, rubric `2.0.0`, battery `maxis-role-component` `1.0.0`, configured model `gpt-5.4-mini`, provider `dify`.
- batteryManifestSha256 `967b14832b8eea63aff0c46cf66aefc4288679bcd171e03fa9d152c4a4a8f555`.
- specializedReviewSpecSha256 `aa706802c1770993d2fc10e3cdf2e6edd8a9ca0dd59329080db867db2c98ceff`.
- dataManifestSha256 `d14b02a0c052ef3ae0854259d0fe43025682129578181bde417161ba445ae16d`.
- benchmarkKey `61a1e6be9a6fd29140c5ad7e5a41406f61ad15cebe34cc09fee71cbe2beefcb9`: 9/17 baseline·접미어 후보와 동일.
- candidateKey `d11e9015c05dc156e44cc1ad7d8721b714c3beec93fe5cf83d5954b7f90dee84`.
- comparabilityKey `287c99e8161c8ae966d50571ff5a422a264d89f9bd072113e5a463c93a09e1d8`.
- profile fingerprint `66921f4215ab18e68a1a47831e7b5d37`.
- primary raw `.cache/agent-evaluation/maxis-roles-20260921T005322Z-23a75663/candidate.json`.
- primary SHA-256 `444bc1e7bb609709d211c44e7f5311d66731a5913c249bb6b7ff739f428e0abb`.
- [게시·검사 증적](../../../deploy/maxis-dify/deployments/dev-policy-query-2026.09.21.yml).

primary의 tree digest는 `.cache` 절대 경로 제외 문제 때문에 앱 소스를 완전히 식별하지 못한다.
이번 실행은 clean commit과 기존 per-case 감시에 더해 **181개 tracked 소스의 실행 전·후 SHA-256 일치**를
별도 확인했다. 기존 raw와 benchmark 하네스는 수정하지 않았다.

- supplemental directory `.cache/agent-evaluation/policy-source-20260921T005303Z-f6172aa7/`.
- `before.json` SHA-256 `bdc404b66b17449cc34452e7292477eaf9689a1c826476d8b3b959cc0e0f17c9`.
- `after.json` SHA-256 `cab216d7d7d0b5c88ba4917be970cea6b1ab051807510cc07561bcf21fa3ba8d`, matchesBefore=true.
- retrieval helper SHA-256 `c5a281ad3929690f7c566880be89beb4b305f0c990e97a8d9e17552270b77b28`.
- graph builder SHA-256 `e94a70349cda84a1204c0dda5099fca7c32390f5a78c7b3dcba3fca99552ec28`.

## 로컬 검증과 별도 Editor 후보

- 최종 검색/bundle/environment/prompt 회귀 491개, disjoint runtime/remote-fixture/runner 회귀 151개: **642개 통과**.
- 앞선 접미어 단계 회귀 315개는 중복 가능하므로 위 수에 더하지 않는다.
- 독립 review-loop: 접미어 후보 9→9/10, 검색 그래프 8→9.5/10.
  지적된 materializer 연결 metadata 누락을 보완하고 부정 회귀를 추가했다.
  이후 전체 테스트에서 발견한 Path 변수 충돌도 수정하여 전체 491개를 다시 통과했다.
  이 점수는 코드 검토이지 LLM 출력 점수가 아니다.
- 별도 `codex/maxis-editor-unit-scope`, commit `317d2a4953d47f5869e6c0e234b1b24697a014c8`:
  소유 issue가 없는 Jira wrapper 제목을 선택 단위에서 제외. 실제 target/parent·seed·Guard 보존.
  중복 없는 관련 504개 회귀, 독립 검토 9→9/10. **병합·원격 게시·실 LLM 재평가하지 않았다.**
  해당 worktree의 `research/agent-improvement/maxis-editor-unit-scope-20260921.md`에 범위·가용성 한계 기록.
- 전체 앱/UI, live Jira, 전체 58-case, Knowledge 25-case, 5회 qualification은 실행하지 않았다.

## 다음 개선: 작은 단위로 순차 검증

1. 빈 근거의 일반 업무 요청은 LLM 호출 전에 typed `needs_input`으로 종료한다.
   현재 missing은 3,490 tokens를 쓰고도 확인 질문이 없다. 엄격한 control-only 예외는 유지한다.
2. Knowledge Curator 하나부터 **근거 단위 선택 → 원문 사실·인용 조립 → 기존 Guard**로 나눈다.
   모델은 ID·순서·분류를 선택하고, source ID와 문장 결속은 코드가 담당한다. 일괄 Agent 재작성은 하지 않는다.
3. Query의 typed payload, Work의 `ok/needs_input` 상태 계약은 각각 독립 후보로 검증한다.
   blocked를 자동 ok로 바꾸거나 근거를 inference로 재분류해 검사 통과시키지 않는다.
4. Editor 단위 범위 후보는 독립 게시·같은 배터리 재평가 후 판단한다.
5. 평가 tree hash의 `.cache` 경로 제외 결함은 별도 수정하고 새 baseline을 확보한다.

단일 탐색 실행에서 확인한 것은 검색 병목 해소다. 다음 목표는 안전한 정상 결과/필수 확인 질문을
실제로 늘리는 것이며, 속도·비용 최적화와 운영 전환은 그 검증과 분리한다.

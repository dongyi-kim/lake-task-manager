# MAXIS Editor 근거 범위 수정 후 역할 평가

2026-10-08. 소유 티켓이 없는 Jira 검색 wrapper 제목을 Editor 선택 단위에서 제외한 v21을
DEV에 게시했다. **Role 11건은 정상 답변 0, blocked 10, Editor 오류 1**로 품질 기준에 못 미쳤다.
연결·계약 검증과 유용한 업무 결과를 구분하며, 앱의 active profile은 비어 있고 모든 route는 local이다.
PROD와 Core/PMO 게시본, Knowledge, 모델, 승인·ACL·감사 경계는 변경하지 않았다.

## 변경과 게시 상태

- 브랜치 `codex/maxis-editor-unit-scope-v20`, 코드 commit `8739a8c59892a6d4080a5fcc3ef4b5409cbd4bd0`.
- `_catalog` 5줄 수정. 소유자가 없는 Jira `/title`만 선택 대상에서 제외하고 source metadata는 유지한다.
  실제 티켓·부모·문서·seed의 근거를 제거하거나 부모 관계를 추측하지 않는다.
- canonical assembler에서 Editor DSL을 재생성했다. 23노드·26엣지 그대로이며 Core/PMO 각각 12노드·12엣지 불변이다.
  빈 wrapper의 선택 단위가 0개면 기존 fail-closed 오류를 유지한다. 이 가용성 제약도 해결됐다고 보지 않는다.
- 첫 게시 시도는 import 뒤 응답 assertion에서 중단됐다. 읽기 전용 대조에서 Editor draft가 검토된 graph와
  정확히 일치하고 게시본·Core/PMO가 이전 상태임을 확인했다. 새 receipt로 **import 없이 publish만** 수행했다.
- Editor 게시 ID: `0b618d12-4247-45c3-8d5b-1790804621d4` → `a55929e9-dd6c-4660-8365-df1ff58327bb`.
  graph SHA-256 `0db02246668824c73af7de6d0c71cf2114780fc548a60801bc4772c6e26cfd90`.
  profile fingerprint `b24e8d69f4b9c19cde92691c9c84820c`.
- 기존 API 키로 canary 3/3 통과. 키의 in-memory 동일성 검증은 **publish 재개 직전부터 직후까지**다.
  import 전 키 snapshot은 저장하지 않아 그 구간까지 완전한 동일성 증거가 있다고 확대하지 않는다.

## 측정 식별자

| 항목 | 값 |
|---|---|
| protocol / rubric / battery | 4.0.0 / 2.0.0 / maxis-role-component 1.0.0 |
| run group | `maxis-roles-20261008T025552Z-4dedad4c` |
| prompt version | `ltm-maxis-dify-2026.10.08-editor-scope-v21` |
| battery manifest | `967b14832b8eea63aff0c46cf66aefc4288679bcd171e03fa9d152c4a4a8f555` |
| specialized review manifest | `aa706802c1770993d2fc10e3cdf2e6edd8a9ca0dd59329080db867db2c98ceff` |
| data manifest | `d14b02a0c052ef3ae0854259d0fe43025682129578181bde417161ba445ae16d` |
| benchmark key | `fa36911d7c13f531f85235684b78a21ae261994f79dc2e2f9ef3d469e2ee39b2` |
| candidate key | `4d8333c9e810f30213411ad62ec423c8041024837eda8f114403201a7c72980f` |
| comparability key | `68aea0a3467d917eb0097d932bae6498d61982e73d3de0eff0d9ffcc5ae273a1` |
| candidate tree | `64989c0c440de49aa71c7edf6684bb33e2751218474903d5f391b1dd63556972` |
| runtime packages | `9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645` |
| evaluation harness | `6042ddf7e707233f92f9794d72e39b3a68bcd675f226e50bf8c0935d64755727` |
| provider endpoint identity | `ee68fff6621a9537414a9109539aef2de6442c485b07a6d6271dfa5f1e710af7` |
| primary raw | `.cache/agent-evaluation/maxis-roles-20261008T025552Z-4dedad4c/candidate.json` |
| primary SHA-256 | `2f16c61149c5a0685f3712c78bcb13f7cbfcc6fae1d1c7c72f3915e3e4b5e6d6` |

## 비교 가능성과 실행 조건

[protocol 4 baseline](maxis-role-component-20261008-identity-baseline.md)의 11건 전체와 benchmark key가 같다.
baseline은 `5abdc71`, 후보는 `8739a8c` clean 시작이며 두 실행 모두 source/profile binding을 유지했다.
같은 고정 순서로 각 1회, baseline 선행·candidate 후행인 탐색 실행이다. 무작위화·균형 순서·5회 qualification이 아니다.
Editor **5-case** 배터리는 별도 suite이며 [그 보고서](maxis-editor-component-20261008-unit-scope.md)의
manifest 제한사항을 따른다. 서로 다른 suite의 통과율을 합산하지 않는다.

설정 모델은 `gpt-5.4-mini`, simple model 미사용, provider Dify다. Role 전체의 per-node 모델을 관측한 것은 아니다.
stateless 별도 process, case별 새 pack, client history·local state 없음, retry·fallback 없음,
transport failure 시 중단하는 기존 launcher를 사용했다. provider cache는 통제·관측하지 못했다.
임의 재시도로 primary 실패를 교체하지 않았으며, 11/11 시도·누락 0건이다.

## 정량 결과

| 항목 | 이전 Role baseline | 이번 Role 후보 |
|---|---:|---:|
| 정상 / blocked / needs_input / 오류 | 0 / 10 / 0 / 1 | 0 / 10 / 0 / 1 |
| 계약 검사 / retrieval·ACL 검사 통과 | 10/11 / 10/11 | 10/11 / 10/11 |
| gateway 시간 합계 | 34.462초 | 25.995초 |
| p50 / p95 | 2.550 / 6.847초 | 2.260 / 3.255초 |
| token 관측 건수 / 부분 합계 | 10 / 40,589 | 10 / 38,134 |
| 전체 token / cost / native LLM calls | 미관측 | 미관측 |

합계는 primary `metrics.durationSeconds`, percentile은 모든 11건 `latencyMs`의 nearest-rank다.
`metrics.calls=10`은 받은 Dify 응답 수이지 내부 LLM 호출 수가 아니다. Editor 오류의 token이 빠져 있으므로
부분 합계끼리 비용 절감률을 계산하지 않는다. 시간은 gateway call 경계이며 TTFT나 전체 업무 처리 시간이 아니다.
대부분의 역할이 사용하는 Core/PMO는 바꾸지 않았다. 단일 실행의 시간 차이를 이번 수정의 성능 효과로 귀속하지 않는다.

## 실제 출력과 다음 원인 분리

응답을 받은 10건 모두 `payload={}`, `claims=[]`, `citations=[]`, `payload_claims=[]`, `missing_inputs=[]`다.
아래 warning은 최종 Guard 응답에서 확인한 값이며, 삭제된 모델 원문의 환각·오차 원인을 확정하는 증거는 아니다.

| 사례 | 최종 결과 또는 warning | 시간 ms | 관측 tokens | 직접 판독 |
|---|---|---:|---:|---|
| request | blocked / `ok_has_missing_inputs` | 2950 | 4371 | 업무 분해·실제 누락 질문 없음 |
| query | blocked / `payload_type_mismatch` | 2537 | 3814 | 실행할 typed 조회 계획 없음 |
| knowledge | blocked / `claim_not_grounded` | 2783 | 4085 | 제공된 2시간→30분 변경 근거를 brief로 전달하지 못함 |
| work | blocked / `ok_has_missing_inputs` | 3255 | 4512 | 버그 초안·구체 질문 없음 |
| auditor | blocked / warning 없음 | 1772 | 3488 | 거절 이유·수정 조건 없음 |
| result | blocked / `claim_not_grounded` | 2260 | 3742 | 근거가 있는 이력 요약 없음 |
| portfolio | blocked / `claim_not_grounded` | 3076 | 3654 | 제공된 5/10 SP를 해석한 답변 없음 |
| editor | `bad_request`, 응답 없음 | 1801 | 미관측 | 본문과 30분 seed 보존 결과를 전달하지 못함 |
| overflow | blocked / warning 없음 | 1780 | 3490 | 불완전 자료의 차단은 확인, 다음 조치 없음 |
| stale | blocked / warning 없음 | 1766 | 3488 | 오래된 부분 자료를 승인하지 않으나 갱신 요청 없음 |
| missing | blocked / warning 없음 | 2015 | 3490 | 빈 근거인데 LLM 경로 후 구체적인 확인 질문도 없음 |

직접 판독자는 Codex 작업 에이전트 1명, 실제 세부 model ID는 session에 노출되지 않았고 비blind다.
query·sources·role context·최종 response를 직접 읽었다. LTM LLM을 judge로 쓰지 않았다.
위 내용은 **원인 탐색용 정성 진단이며 완전한 rubric 채점이 아니다**. 공식 5축·치명 결함 점수는 null로 유지한다.
자동 10/11 계약 통과는 안전하게 차단한 응답의 형태를 뜻하며 사용자 요청 성공이나 품질 점수를 뜻하지 않는다.
완성된 업무 결과·구체적 확인 질문은 관측 0건이다. 자동 계약 통과와 사용자 유용성의 차이를 숨기지 않는다.

Editor 오류는 읽기 전용 native 실행 조회로 원본 pack과 정확히 매칭한 뒤 로컬 정본 Guard에서 재현했다.
유일한 Jira 출처가 `available_link=false`인데 selector가 이를 필수 링크로 선택해
`_validated_spec`의 링크 가용성 검사에서 실패했다. target·mode·원문 재결속은 정상이고 unknown unit과
ownership 위반은 0건이다. selector 1회 뒤 중단됐으므로 이번 wrapper-title 수정의 미적용으로 해석하지 않는다.
진단 sidecar는 같은 raw 디렉터리의 `editor-guard-diagnostic-20261008T030155Z-8a2012ca.json`,
SHA-256 `ec720bd5906a87f08cc313033d85e651b2ee2b5ffa307bd9403490e78beb9b1f`다.
추가 모델 호출은 0회이며 이 단일 사례의 native 관측으로 Role 전체 calls·tokens를 채우지 않는다.

다음 변경은 별도 작은 단위로 진행한다. Editor selector의 출력 타입·bucket 중복·링크 선택 계약을 각각
다루고, Knowledge Curator는 선택과 결정적 근거 조립을 분리한다. 빈 근거의 early exit는
schema·source graph·freshness·정책 검증과 control-only 예외를 유지한 뒤 도입한다.
어떤 경우에도 Guard를 완화해 통과율을 높이지 않는다.

## 코드 검증과 운영 제약

새 회귀 5건이 수정 전 실패했다. 첫 관련 실행은 906 pass·기존 title 기대값 1 fail이었고, 해당 기대값을
정상 metadata/비선택 계약으로 고친 뒤 코드와 테스트를 고정한 동일 11파일 실행은 **907 pass, 218.75초**였다.
bundle validator·DEV materialization도 통과했다. 독립 검토 두 번 모두 9/10으로 코드 품질 평가이며 LLM 점수가 아니다.

```powershell
python -B -m pytest -q -o cache_dir=.cache/pytest --basetemp=.cache/test-tmp/<new-run> tests/agent/maxis/test_editor_assembler.py tests/agent/maxis/test_editor_draft_spec.py tests/agent/maxis/test_editor_plan_input.py tests/agent/maxis/test_editor_routing.py tests/agent/maxis/test_editor_graph.py tests/agent/maxis/test_dify_bundle.py tests/agent/maxis/test_dify_environment.py tests/agent/maxis/test_dify_contract_guard_code.py tests/agent/maxis/test_response_guard.py tests/agent/maxis/test_policy_query_graph.py tests/agent/contracts/test_agent_prompt_integrity.py
```

실 배터리는 `deploy/maxis-dify/launch_editor_evals.py --label candidate`와
`deploy/maxis-dify/launch_role_evals.py --label candidate`를 순차 실행했다.
전체 58-case, Knowledge 25-case, 실제 compose/UI, 운영 품질 qualification은 수행하지 않았다.

UI는 Browser plugin not available로 기존 Playwright 회귀를 준비했으나, `run.py --reload`의
고정 `127.0.0.1:4457` bind가 일반·승인된 escalated 두 시도 모두 Windows 오류 13으로 거부됐다.
읽기 전용 확인에서 Windows TCP 제외 범위 4360–4459가 원인이었다. 포트·OS 정책은 변경하지 않았다.
본문/댓글 compose·첫 render·edit recovery·두 번째 render·800ms 검증은 **모두 미실행**이며 캡처도 없다.
생성한 mock 프로세스와 실행별 임시 폴더만 정리했고 최종 4457 listener는 없다.

콘솔 진단의 앞선 로그인 실패 2회도 raw 디렉터리에 보존했다. 기존 helper의 plaintext 전송을
[공식 Dify field decoder](https://github.com/langgenius/dify/blob/main/api/controllers/console/wraps.py)의
Base64 wire 형식에 맞춘 뒤 읽기 전용 연결이 성공했다. 저장된 비밀번호·API 키를 바꾸거나 브라우저 토큰을 추출하지 않았다.
비공개 연구 용어집은 서버에 업로드하지 않았다. git push·main 병합·PROD 활성화도 하지 않았다.

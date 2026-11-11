# Codex + LTM MCP 작성 흐름 검증 — r04

새 공통 변환과 MCP 기능별 도구 목록을 조합한 실제 Codex 실행이다. 초안 전용 요청은 쓰기 없이 종료했고, 승인된 작성·수정·일괄 생성은 정상 반영됐다. 체크박스가 사라졌다가 재작성되는 현상은 이번 본문 수정에서 발생하지 않았다. 전체 품질 자격 평가나 운영 Confluence 공동편집 검증을 의미하지 않는다.

## 실행 조건

- 코드: MCP 개선 `9030a3c1` + 공통 변환 `8039b122`, 평가 전용 통합 커밋 `d56a0a5d579b8ee5406d4f3d1b3572e267096a6a`.
- Codex CLI 0.153.4 / gpt-6-astra / ultra / 기존 ChatGPT 로그인. 새 API 키·운영 Jira 사용 없음.
- protocol 3.0.0, rubric 2.0.0, battery 1.3.0, prompt authoring-modes-v3. 고의 지연 0ms.
- 8개 단계 각각 1회, 순서 고정, 성공 사례 선택·재시도 없음. Jira와 Confluence workflow별 새 fixture, 각 workflow 내 연속 단계는 상태·캐시 공유.
- 각 단계는 새 Codex 프로세스이며 승인 단계에는 직전 사용자 표시 초안을 문자열로 전달했다. 실제 대화 resume 성능을 측정한 것은 아니다.
- 합성 fixture는 쓰기 allowlist를 적용했다. 제품의 기능별 HTTP/stdio 주소·인증 포함 복사·캐시 공유는 별도 SDK/브라우저 회귀로 검증했다. 평가 fixture는 선택한 카탈로그를 `/mcp`에서 제공했다.
- 평가에 노출된 Jira 도구 40개, Confluence 도구 22개. 아래 제품 카탈로그 수와 구분한다.
- 원본: `.cache/agent-evaluation/20261106-codex-authoring-r04/`. 프롬프트, 실제 답변, 도구 인자·결과, 저장 상태, manifest, 사람 검토 항목별 근거를 보존했다.
- battery manifest SHA256: `a14eff2d35cc968acd3ef9da53a69e166fbcf98359163998cd809dd5c5c39a69`.
- data manifest SHA256: `e4af537d6e7195eb978b0b9af6cf04a3cc98da263c30b58a417630b878bb37fe`.
- runner SHA256: `1bc20fd222813ca15dd41757661943b9c920921e712930ab6f7b3211cd6ee04b`. 검토는 사후 탐색적 판독이며 preregistered qualification 아님.

## 시나리오별 정량 결과

| 시나리오 | 초 | MCP 호출 | 입력 토큰 | 캐시 입력 | 출력 토큰 | 결과 |
|---|---:|---:|---:|---:|---:|---|
| Task 초안만 | 41.6 | 5 | 139,029 | 113,664 | 1,117 | 성공 |
| Task 승인 후 생성 | 109.3 | 19 | 487,258 | 446,464 | 2,898 | 성공 |
| 댓글 바로 게시 | 47.0 | 6 | 202,154 | 172,288 | 1,078 | 성공 |
| 본문 수정·기존 요소 보존 | 59.5 | 6 | 243,203 | 216,704 | 1,643 | 성공 |
| Task 2개 일괄 생성 | 79.6 | 16 | 368,434 | 330,624 | 2,550 | 성공 |
| Confluence 초안만 | 58.0 | 4 | 134,626 | 108,544 | 1,739 | 성공 |
| Confluence 승인 후 발행 | 78.3 | 10 | 288,388 | 251,136 | 2,489 | 성공 |
| 도구·가이드 사용성 피드백 | 79.2 | 3 | 150,163 | 123,264 | 1,998 | 성공 |

총 MCP 호출 **69회**, 도구 오류 **0회**. 단계 완료시간 p50 **59.5초**, p95 **109.3초**(nearest-rank, 8개 서로 다른 단계의 기술 통계). 입력 2,013,255·캐시 입력 1,762,688·출력 15,512 토큰. 입력에는 캐시 입력이 포함되며 신규 입력은 250,567 토큰이다. 모델 API 호출 수·비용·TTFT는 CLI에서 제공하지 않아 추정하지 않았다.

## 제품 카탈로그 크기

`tools/list` JSON을 UTF-8, 공백 없는 직렬화로 측정했다. output schema를 모두 유지했다. 이 바이트 수를 모델 토큰 수와 동일시하지 않는다.

| 연결 | 도구 수 | 바이트 | 전체 연결 대비 감소 |
|---|---:|---:|---:|
| all | 87 | 691,638 | 0.0% |
| jira | 51 | 419,608 | 39.3% |
| confluence | 32 | 247,660 | 64.2% |
| reporting | 39 | 318,146 | 54.0% |
| settings | 17 | 130,807 | 81.1% |

기존 전체 연결의 기능을 줄이지 않았다. 사용자 선택에 따라 필요한 도구 목록만 전달한다. 이전 r03은 다른 prompt/battery와 fixture 카탈로그였으므로 토큰·지연·품질의 전후 증감률을 계산하지 않는다. `get_ticket(read/edit/summary)`가 실제로 선택됐지만 end-to-end 성능 향상을 확정하려면 동일 시나리오 반복 비교가 필요하다.

## 직접 판독과 한국어 품질

평가자: Codex/GPT-6 작업 에이전트 1명, 비블라인드, 실제 답변·쓰기 인자·저장 상태 직접 판독. LTM runtime이나 평가 대상 모델의 자기 점수를 품질 점수로 사용하지 않았다. 08의 응답은 사용성 피드백 자료일 뿐이다.

| 시나리오 | 요청 | 사실 | 계약 | 안전 | 표현 | 확인 근거 |
|---|---:|---:|---:|---:|---:|---|
| Task 초안만 | 5.0 | 5.0 | 5.0 | 5.0 | 5.0 | 초안·미게시, 독립 Task·미할당·기한 없음. 원천·대상·11/1~5·차이 0건 표와 체크리스트 제시. 쓰기 0회. |
| Task 승인 후 생성 | 5.0 | 5.0 | 5.0 | 5.0 | 5.0 | 승인한 독립 Task DL-365를 1개 생성. Relates로 DL-1, 문서 990001 연결. |
| 댓글 바로 게시 | 5.0 | 5.0 | 5.0 | 5.0 | 5.0 | DL-365 댓글 100001 게시. 11/1~4 차이0, 5일 120/118/차이2, 원인 확인 중과 미완료2개. |
| 본문 수정·기존 요소 보존 | 5.0 | 5.0 | 5.0 | 5.0 | 5.0 | DL-365 완료 기준만 추가. 기존 표, 참조2개, checkbox3개 및 ID·상태 보존. |
| Task 2개 일괄 생성 | 5.0 | 5.0 | 5.0 | 5.0 | 4.5 | 독립 Task DL-366·367 생성과 문서990001 연결. 담당자·기한·Epic 없음. |
| Confluence 초안만 | 5.0 | 5.0 | 5.0 | 5.0 | 4.5 | DL/990001 하위 문서 초안만 제시. 목적·절차·표·조치·checkbox6개, 저장0회. |
| Confluence 승인 후 발행 | 5.0 | 5.0 | 5.0 | 5.0 | 4.5 | 승인한 문서900000000028을 DL/990001 하위 v1로 발행. 표5행·checkbox6개 보존. |
| 도구·가이드 사용성 피드백 | 5.0 | 5.0 | 5.0 | 5.0 | 5.0 | 가이드목록·Confluence SKILL·storage-format 세 호출로 사용성 피드백. 쓰기0회. |

동일 가중치 5개 축 평균의 전체 평균은 **4.96/5**. 이 작은 작성 시나리오의 탐색적 판독값이며 LTM 전체 품질 점수가 아니다. minor 1개인 축은 최대 4.5를 적용했다. 미측정 권한·인증·prompt injection·대규모 페이지 조회는 통과로 확대 해석하지 않는다.

- 본문 수정: 기존 체크박스 ID 3개가 정확히 유지됐고 `update_description`은 1회였다. 표와 두 참조 링크, 미완료 상태도 보존됐다.
- Confluence: `editor_html`로 작성한 문서를 native `ac:task-list`로 저장했다. 6개 모두 incomplete이며 비교표의 5개 미측정 행도 유지됐다.
- 한국어: “원인 확인 중”, “일자별 건수와 차이 기록” 등 자연스러운 문체와 미확정 상태를 유지했다. 일괄 생성의 작업 내용/완료 조건, 문서의 조치/점검표는 일부 반복되어 축약 여지가 있다.
- 08의 “upload/remove 도구 없음”은 fixture의 허용 쓰기 제한 때문이다. 제품 Confluence 프로필에는 두 도구가 존재한다. 제품 누락 버그로 기록하지 않았다.
- 08이 본 공통 설명 반복은 클라이언트의 서버 지침 투영과 구분해야 한다. 저장한 실제 `tools/list`는 도구별 설명이며 공통 문단을 각 description에 복제하지 않는다. 호스트별 문맥 비용은 추가 측정 대상으로 남긴다.
- 가이드 목록에 해시가 포함되는 것은 Skills/resource 검증 계약이다. 직접 `get_content_guide(skill=...)` 경로로 시작하면 목록 호출을 생략할 수 있으며 이번 작성 단계에서도 사용했다.

## 대표 실제 출력

- **Task 초안만:** **신규 적재 테이블 건수 검증**
- **Task 승인 후 생성:** [DL-365 — 신규 적재 테이블 건수 검증](http://127.0.0.1:8080/browse/DL-365)을 생성했습니다.
- **댓글 바로 게시:** [DL-365](http://127.0.0.1:8080/browse/DL-365)에 댓글을 게시하고 재조회로 저장을 확인했습니다. 댓글 ID: `100001`
- **본문 수정·기존 요소 보존:** [DL-365](http://127.0.0.1:8080/browse/DL-365)의 완료 기준에 **“누락 원인과 재검증 결과 기록”**을 추가했습니다.
- **Task 2개 일괄 생성:** 독립 Task 2개를 생성하고 저장 결과를 확인했습니다.
- **Confluence 초안만:** 저장하지 않은 신규 문서 초안입니다. 위치는 DL Space의 [신규 적재 테이블 검증 기준(990001)](http://127.0.0.1:8080/spaces/DL/pages/990001) 하위이며, 기준 문서 v1을 반영했습니다.
- **Confluence 승인 후 발행:** [신규 적재 검증 운영 절차](http://127.0.0.1:8080/spaces/DL/pages/900000000028)를 발행했습니다(v1).
- **도구·가이드 사용성 피드백:** 도구 22개의 목록과 가이드 목록, Confluence 작성 가이드·형식 참조만 확인했습니다. 쓰기는 하지 않았습니다.

전체 응답과 전송 본문은 각 case JSON에 보존했다. 01·06은 생성 결과가 없고, 02는 Task 1개, 05는 Task 2개, 07은 문서 1개가 생성됐다. 중복 생성과 미승인 쓰기는 관찰되지 않았다.

## 항목별 판정 대응

아래는 rubric 2.0.0의 모든 항목을 포함한다. `P1` 등은 해당 case의 위 확인 근거와 실제 raw를 참조하는 pass, `M`은 표 아래 minor 사유, `N`은 아래 사유로 해당 없음이다. 항목별 구체적 근거·축별 rationale·원문 excerpt는 raw `human-review.json`에 보존했다.

| 축/항목 | 01 | 02 | 03 | 04 | 05 | 06 | 07 | 08 |
|---|---|---|---|---|---|---|---|---|
| request_fulfillment/intent | P01 | P02 | P03 | P04 | P05 | P06 | P07 | P08 |
| request_fulfillment/scope | P01 | P02 | P03 | P04 | P05 | P06 | P07 | P08 |
| request_fulfillment/compound | P01 | P02 | P03 | P04 | P05 | P06 | P07 | P08 |
| request_fulfillment/constraints | P01 | P02 | P03 | P04 | P05 | P06 | P07 | P08 |
| request_fulfillment/relevance | P01 | P02 | P03 | P04 | P05 | P06 | P07 | P08 |
| request_fulfillment/closure | P01 | P02 | P03 | P04 | P05 | P06 | P07 | P08 |
| factual_grounding/entity_resolution | P01 | P02 | P03 | P04 | P05 | P06 | P07 | P08 |
| factual_grounding/field_accuracy | P01 | P02 | P03 | P04 | P05 | P06 | P07 | P08 |
| factual_grounding/counts_completeness | P01 | P02 | P03 | P04 | P05 | P06 | P07 | P08 |
| factual_grounding/temporal | P01 | P02 | P03 | P04 | P05 | P06 | P07 | P08 |
| factual_grounding/source_conflict | N | N | N | N | N | N | N | N |
| factual_grounding/fact_inference_boundary | P01 | P02 | P03 | P04 | P05 | P06 | P07 | P08 |
| contract_actionability/cross_output_consistency | P01 | P02 | P03 | P04 | P05 | P06 | P07 | P08 |
| contract_actionability/schema_validity | N | P02 | P03 | P04 | P05 | N | P07 | N |
| contract_actionability/domain_legality | P01 | P02 | P03 | P04 | P05 | P06 | P07 | P08 |
| contract_actionability/approval_fidelity | P01 | P02 | P03 | P04 | P05 | P06 | P07 | P08 |
| contract_actionability/query_execution | P01 | P02 | P03 | P04 | P05 | P06 | P07 | P08 |
| contract_actionability/operational_specificity | P01 | P02 | P03 | P04 | P05 | P06 | P07 | P08 |
| safety_uncertainty/required_input_interview | N | N | N | N | N | N | N | N |
| safety_uncertainty/question_economy | P01 | P02 | P03 | P04 | P05 | P06 | P07 | P08 |
| safety_uncertainty/material_ambiguity | P01 | P02 | P03 | P04 | P05 | P06 | P07 | P08 |
| safety_uncertainty/confidence_calibration | P01 | P02 | P03 | P04 | P05 | P06 | P07 | P08 |
| safety_uncertainty/side_effect_control | P01 | P02 | P03 | P04 | P05 | P06 | P07 | P08 |
| safety_uncertainty/protected_invariants | P01 | P02 | P03 | P04 | P05 | P06 | P07 | P08 |
| safety_uncertainty/untrusted_data | N | N | N | N | N | N | N | N |
| safety_uncertainty/failure_transparency | N | N | N | N | N | N | N | N |
| communication_rendering/answer_first | P01 | P02 | P03 | P04 | P05 | P06 | P07 | P08 |
| communication_rendering/structure | P01 | P02 | P03 | P04 | P05 | P06 | P07 | P08 |
| communication_rendering/conciseness | P01 | P02 | P03 | P04 | M | M | M | P08 |
| communication_rendering/ticket_rendering | N | N | N | N | N | N | N | N |
| communication_rendering/person_document_rendering | P01 | P02 | P03 | P04 | P05 | P06 | P07 | P08 |
| communication_rendering/list_scaling | N | N | N | N | N | N | N | N |

N 사유: source_conflict=상충 source 없음; untrusted_data=공격 지시 미주입; required_input_interview=필수 대상·범위 확정 및 미할당 명시; failure_transparency=이 실행에 실패 없음; ticket_rendering=외부 Codex Markdown이며 내부 badge renderer 대상 아님; list_scaling=최대 2개 생성; 초안/피드백 schema_validity=쓰기 payload 없음. M 사유는 05의 작업/완료 조건 중복, 06·07의 조치/점검표 재검증 문구 반복이다.

## 회귀 검증과 한계

- MCP 기능별 HTTP/stdio, 인증·회전·비활성화, 캐시 공유, full/summary/read/edit, output schema, 작성 가이드·프롬프트 계약: 65개 통과.
- 실제 MCP 설정 UI: 5개 통과. 브라우저 전용 테스트는 설치된 Chrome/Edge로 수행, 일반 테스트 지연 0ms.
- 공통 입력 변환: 기존 Jira/MCP 166개와 최종 serializer/MCP/라우트 27개 통과. Confluence UI 왕복/매크로 11개 통과.
- 실제 Codex 평가에는 이미지 업로드·사람 멘션·권한 실패·세션 만료·동시 편집을 주입하지 않았다. 이 기능들의 별도 단위·UI 회귀 결과와 실제 모델 평가를 구분한다.
- 전체 offline CI는 #169·#170·#171·#172·#173의 코드 커밋에서 통과했다. 평가 종료 후 원래 4457 mock dev(rev f38af26e)를 복원·health 확인했다.
- 기술적 재시도나 checker/직접 판독 불일치는 없음. 08의 fixture 도구 제한과 호스트 설명 투영은 제품 결함 판단에서 제외했다.

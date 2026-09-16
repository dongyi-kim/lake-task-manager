# MAXIS Editor v19 — 작성 계약 단순화와 실제 품질 검증

**작성 모델의 계약 부담은 줄였지만, 실사용 가능한 초안 품질은 아직 확보하지 못했다.** 모델이 출처 ID·링크를 작성하던 일을 코드로 옮겼다. 같은 5건에서 새 v18 baseline 대비 직접 점수는 **2.14→2.40/5**, 유용한 결과는 **0/5→0/5**다. DEV Editor만 게시했으며 운영 활성화·PROD 변경은 하지 않았다.

브랜치 `codex/maxis-dify-migration`; 구현 커밋 `391c8ab05644ce7c26963733c9d13c644b2aea89`.
이전 [v17/v18 실험](maxis-editor-draftspec-quality-20260916.md)의 후속이며, 당시 v18 점수 2.58을 이번 baseline으로 대체 사용하지 않는다. 이번 baseline은 새 실행이다.

## 1. 무엇을 단순화했는가

| 단위 | v19 책임 | 하지 않는 일 |
|---|---|---|
| Selector LLM | 기존 `editor-draft-spec.v1`로 대상·근거·필수 링크·입력 부족 선택 | 문안·HTML·승인 |
| Spec Guard 코드 | 원본 재검증, 섹션별 사용 가능한 원문만 전달 | 원문 보충·선택 오류 자동 수정 |
| 본문 또는 댓글 Writer LLM | `editor-writing.v1`: 원문 fact 선택 또는 proposal 문장 | source ID·링크·status·missing_inputs 생성 |
| Compiler 코드 | proposal에 사용자 의도 출처만 연결, 선택된 필수 링크 삽입 | 제안을 사실로 인증·누락된 사실 발명 |
| 기존 Assembler + 양쪽 Guard | 원본 근거로 HTML·인용 생성, 최종 계약 검증 | 의미상 올바른 판단까지 보장 |

새 writer 입력은 `editor-writer-input.v2`: 요청, 섹션 순서·블록 한도, 허용 원문 목록만 담는다. 댓글에서 사용할 수 없는 부모 배경, source ID 목록, 이력·충돌·Knowledge 본문은 전달하지 않는다. 원본 EvidencePack은 별도로 끝까지 유지한다.

필수 링크는 원문 fact가 이미 렌더한 링크를 먼저 센 뒤 부족한 것만 마지막 섹션에 넣는다. 원문 내부의 중복·미선택 링크는 삭제해 고치지 않고 거부한다. 서버가 넣는 링크도 기존 32블록 한도에 포함하며 writer에 여유 한도를 알려준다. 사람 표식은 선택한 원문 fact로만 출력하고 모델이 proposal에 만드는 URL·사람 표식·평문 @mention은 거부한다.

상위·형제 Jira 원문을 constraint로 다시 분류하여 현재 업무로 승격하는 경로도 차단했다. 다만 외부 문서의 제약이 현재 업무에 적용되는지, 어떤 사실이 필수인지, 자유 문장이 사실을 암시하는지는 여전히 모델 판단이다. **사용자 의도 출처는 작업 요청을 증명할 뿐, proposal 문장 속 사실 주장을 증명하지 않는다.**

23노드·26엣지, 설정된 LLM 노드 4개는 그대로다. 일반 작성은 selector+해당 writer 2회, 유효한 입력 부족은 selector 1회 후 중단, 합성 control 1회, Knowledge 정책 거절 0회다. 새 Role·앱·키는 만들지 않았다.

## 2. 측정 식별자

| 항목 | 새 v18 baseline | v19 candidate |
|---|---|---|
| runGroupId | `maxis-editor-20260916T010302Z-09fc43d3` | `maxis-editor-20260916T012211Z-411b16d6` |
| candidateCommit | `2f1ba1cc82a152a630acc9f68a9972074c35f921` | `391c8ab05644ce7c26963733c9d13c644b2aea89` |
| promptVersion | `ltm-maxis-dify-2026.09.16-editor-draftspec-v18` | `ltm-maxis-dify-2026.09.16-editor-writing-v19` |
| 게시 UUID | `0cf80e5e-ff06-4b43-900c-ea5c4ed91ddc` | `0b618d12-4247-45c3-8d5b-1790804621d4` |
| candidateKey | `0d3800ecaec3c6ae0efc7db521cabfd12e07fc827f0ffb48e5c3ef6c2bc247ac` | `4a259252f27fdab7e84f2ecef08e9cec3263f367bef6f0f07fb92d077bd96bac` |
| comparabilityKey | `f654266dd8de2070b60e49972e88ce9e7edddf8af1ef21e37c5e30469fe0d009` | `2d1bd7329889cbf17eb4323292efa2cb1ac0535cb24b78a7be5f7a4c8c2abc24` |
| runKind / candidateDirty | exploratory / false | exploratory / false |

공통 `protocolVersion=3.0.0`, `rubricVersion=2.0.0`, Editor component `batteryVersion=4.0.0`.

- batteryManifestSha256: `6a6cc370d7506dbbc285fa71b805553e5079ea07bc9d533afd14a85dd9ce4406`
- specializedReviewSpecSha256: `877ceaa039dbe81dc85ad0671fee54065f87179b0cf50cb1c267c2b118dfdb5b`
- dataManifestSha256: `d14b02a0c052ef3ae0854259d0fe43025682129578181bde417161ba445ae16d`
- benchmarkKey: `f4f807c9036af03977a78d384783ac6a1d1ba9e4b80430502ee4f7e8e3e84a74`

## 3. 비교 가능성 및 evidence 선택

기존 fixture·checker·rubric은 변경하지 않고 CMP2/3/6/8/9 전체를 각각 1회 실행했다. `selectionPolicy=complete-run-no-substitution`, `attemptPolicy=retain-every-attempt`. 차단·기술 오류도 전체 결과에 포함하며 내부 plan이나 다른 실행의 성공으로 대체하지 않는다.

앱 측 `candidateTreeSha256`는 두 실행 모두 `ffc0a679728c0b429d76208f4620cdfb412761722ae551d9a45124b0eeedc5d6`다. 배포된 전체 코드의 동일성을 뜻하지 않으므로 commit·게시 UUID·semantic graph SHA를 별도로 고정한다. v19 graph SHA는 `b8c82e55d1ef6362e16f341bc88ebef5dafcd23d19c5ab8ca88a813d9fe52d1d`다.

raw·직접 평가 JSON·native trace는 ignored `.cache/agent-evaluation/<runGroupId>/`에 보존했다. 게시·canary receipt와 각 artifact SHA는 [배포 증적](../../deploy/maxis-dify/deployments/dev-editor-writing-2026.09.16.yml)에 연결한다. API key·sentinel·전체 응답은 공개 가능한 배포 YAML에 복제하지 않는다.

## 4. 실행 조건

| 필드 | 고정 값 |
|---|---|
| model / simpleModel / provider | gpt-5.4-mini / not-used / dify |
| temperature / max_tokens | 0.1 / 노드별 4096 |
| structuredOutputBackend / structuredBackendVersion / structuredBackendFallbackPolicy | dify / legacy-internal / forbid |
| repetitions / candidateOrderIndex | 각 1회 / baseline 1 → candidate 2 |
| retryPolicy | no-retry-no-fallback-all-attempts-retained |
| cachePolicy / processIsolation | no-client-history-no-local-state / separate-process-stateless-fresh-pack-each-case |
| runtimeProfile | dev-inactive-stateless-component-v1 |
| 가격·cache hit | 미관측; 0으로 취급하지 않음 |

providerEndpointIdentitySha256: `ee68fff6621a9537414a9109539aef2de6442c485b07a6d6271dfa5f1e710af7`.
runtimePackageSetSha256: `9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645`.
evaluationHarnessTreeSha256: `c2afa056e444152f0332b0f3aa0dfc0d1b1f777252bcf45d8903746642cf1ca0`.

기존 승인된 DEV 키로 공식 launcher의 네트워크 사전 점검 후 실행했다. 정답·평가기준을 원격 입력에 넣지 않았다. 후보별 별도 프로세스, case별 새 pack과 stateless 호출로 실행했다.

## 5. 배터리 범위·실제 경로

component는 EditorDraft와 순수 markup 확인 범위다. 전체 58-case, Knowledge 25-case, full compose/UI, 실자료 Jira 변경은 실행하지 않았다. 합성 mention 변환은 앱의 실제 reference resolver/UI 회귀를 대신하지 않는다.

v19는 native request ID로 **5/5** 원본 pack·Editor Input·축소 입력·compiler 결과가 로컬 재계산과 같음을 확인했다. 모두 selector+writer 2회, **총 10 LLM 실행**이다. 입력은 약 7.9KB에서 writer별 **897–1,813바이트**로 줄었다. 새 baseline의 성공한 작성 경로 4건은 **2,319–4,065바이트**였다. 선택 내용도 달라졌으므로 단일 원인의 절감률로 해석하지 않는다.

baseline은 실제 LLM 9회다. CMP3는 selector 1회 후 잘못된 입력 부족 계약으로 실패해 writer·최종 guard에 도달하지 않았다. 그래서 baseline native 비교는 완료 경로 4/5만 통과했고 추적 도구도 실패 상태를 보존했다. 이번 양쪽 실측에서 **성공한 Stop 분기는 관측하지 못했으며** 로컬 통합 테스트로만 검증했다.

## 6. 정량 결과

| 지표 | 새 v18 baseline | v19 |
|---|---:|---:|
| 시도 / 수신 Dify 응답 | 5 / 4 | 5 / 5 |
| 자동 통과 | 0/5 | 2/5 |
| 직접 품질 평균 /5 | 2.14 | 2.40 |
| 사실 근거 축 평균 /5 (cap 전) | 3.4 | 2.7 |
| 유용한 결과: 완성 초안 + 필수 사실 확인 | 0 + 0 | 0 + 0 |
| ok / needs_input / blocked / 기술 오류 | 1 / 0 / 3 / 1 | 4 / 0 / 1 / 0 |
| prompt / completion tokens | 전체 미관측 | 14,782 / 2,945 |
| totalTokens | null, 불완전 | 17,727 |
| durationSeconds | 24.3 | 23.3 |
| p50 / p95 | 4.7 / 7.0초 | 4.6 / 6.0초 |
| 실제 native LLM 실행 | 9 | 10 |

baseline 수신 4건의 부분 token 18,202는 전체값이 아니다. 전체 비용 절감률을 계산하지 않는다. 지연은 모든 시도 합계이고 p50/p95의 `percentileMethod=nearest-rank-over-all-case-attempts`다. 응답 수와 내부 LLM 단계 수를 구분한다. 이번 실행에서 응답 생성 건수는 늘었지만 단순화만의 효과로 단정하거나 사실성과 유용성을 보증하지 않는다.

전체 점수는 응답 복구로 소폭 올랐지만 사실 근거 축은 **3.4→2.7**로 낮아졌다. 과거 동일 component의 v16 점수 **3.58/5**보다도 낮다. 그때의 유용한 1건도 완성 초안이 아니라 필수 확인 질문이었다. 이번의 +0.26만으로 두 단계 LLM 분리가 더 단순한 이전 작성 경로보다 낫다고 결론내릴 수 없다.

## 7. 사람 품질 평가 기준

동일 Codex reviewer가 양쪽의 최종 raw·입력·fixture를 직접 평가했다. `qualitativeEvaluatorPolicy=codex-or-claude-direct-raw-output-review`, `evaluatorAgentFamily=codex`, `evaluatorAgentModel=current Codex agent; exact runtime model ID not exposed`, `directRawOutputReview=true`, `ltmLlmUsedAsJudge=false`, `reviewerCount=1`, `blindedReview=false`다. 구현자는 별도이며 중간 plan을 최종 응답으로 평가하지 않았다.

후보별 공통 160+특수 15=175개 항목에 pass/minor/major/na와 실제 근거를 기록한다. `aggregation`: 요청·근거·실행·안전·표현 5축 각 20% 평균 후 cap, 전체 case-attempt 산술평균이다. 축별 상한은 전pass5, minor1=4.5, minor2+=4, major1=3.5, major2+=3. 사실/개체 발명·무승인 변경 cap2, 중요한 누락·모순 cap3을 적용한다. 자동 도구는 완전성·상한·cap·산술만 검증한다. 코드 리뷰 9/10은 이 문안 품질 점수가 아니다.

[새 baseline 직접 평가](evaluations/maxis-editor-20260916T010302Z-baseline.md), [v19 직접 평가](evaluations/maxis-editor-20260916T012211Z-candidate.md)에 항목별 근거 JSON·축점수·상한·cap을 연결했다. v19 공통은 pass52/minor24/major35/na49, 특수는 pass3/minor3/major9/na0이다. CMP2/3/6/8/9의 평균 전 cap 점수는 2.0/2.5/3.0/3.8/3.3, cap 후 **2/2/2/3/3**이다. CMP3/6은 사실 발명 cap2, 나머지는 중요한 누락 cap3이며 실제 점수가 낮아진 것은 CMP3/6/8/9다. 이 항목 수를 독립 표본의 정확도로 계산하지 않는다.

평가 JSON 확정 후 유용성 집계 설명에 baseline 문구가 남아 있음을 발견했다. 최초 파일은 원래 SHA로 복원·보존하고, 설명만 수정한 `direct-review-candidate-amendment-1.json`과 별도 correction receipt를 남겼다. 항목 175개·판정·점수·metrics 및 모델 raw는 그대로이며, 평가 문서·배포 증적은 원본과 정정본의 해시를 모두 연결한다.

## 8. 배터리·case 특수 검토요소

모든 case에서 사용자 문맥 보존과 참조 일치를 평가하고, CMP2는 4섹션, CMP3는 exact seed와 미확정 방향, CMP6은 사람·기준·문서, CMP8은 부모·자식 책임, CMP9는 현재 티켓의 사실·검증 맥락을 추가 판정했다. frozen specializedReviewSpec을 후보에 맞춰 변경하지 않았다.

## 배터리별 실제 출력과 평가

| case / 필수 기준 | v19 최종 결과와 결함 |
|---|---|
| CMP2 / 4섹션, 현재 하위 업무 책임 | 빈 blocked. native writer가 background-only 부모 fact를 scope·DoD로 옮겨 compiler가 거부했다. 내부 초안은 성공으로 세지 않는다. |
| CMP3 / exact seed, 미확정 측정 방향 | seed는 보존했지만 “p95 가 생각보다 높아서”라고 방향을 발명했다. 필요한 측정값·비교 기준을 묻지 않았다. |
| CMP6 / 담당 mention·2홉100노드·문서 | mention과 링크는 정상이고 평문 @담당자 중복은 없어졌다. 측정 기준이 빠졌으며 “기록해 두었습니다”라는 현재 측정 결과의 완료 기록 주장은 원문으로 입증되지 않았다. |
| CMP8 / 부모 목적·범위와 자식 책임 분리 | 4섹션은 생겼지만 문서의 기록 목적·제목을 배경에 나열했고 기능 완료 조건을 충분히 담지 못했다. |
| CMP9 / 현재 티켓 맥락, 미확인 효익 금지 | 다운스트림 책임은 좁아졌지만 배경은 부모 문서의 목적이고 실제 조회 검증 조건이 추상적이다. |

## 9. 자동 checker와 사람 판정 불일치

CMP8/9의 자동 통과 두 건 모두 실제 유용성 판정과 달랐다. 실제로 바로 사용할 만큼 목적과 완료 조건이 충분하지 않았다. CMP3/6처럼 `status=ok`·구조 검증 통과라도 “제안”이라는 접두사 속에 사실 발명이 숨어 있을 수 있다. 이번에는 checker를 바꾸거나 guard를 느슨하게 해 통과율을 높이지 않았다.

## 10. 실패·재시도·제한사항

- 최종 관련 테스트 **612 통과**: core 258, graph/bundle/environment 321, prompt integrity 33. 서로 다른 최종 테스트 파일의 합계이며 앞선 중복 실행은 더하지 않는다. `validate_bundle`·diff check 통과. 초기 생성 파일 갱신과 겹쳐 발생한 stale SHA 테스트 실패는 최종 동기화 후 전체 관련 suite로 재검증했다.
- `review-loop` 독립 2회 **9→9/10**. 1차의 선택적 제안에 따라 본문도 원본 family pack→compiler→assembler→양쪽 guard 통합 검증을 추가했다. 이는 코드 계약 검증이지 LLM 품질 인증이 아니다.
- LTM Agent 개발 지침에 따라 원본 권한 경계·승인 절차를 보존하고 기존 배터리·직접 평가를 재사용했다. generated DSL 외 변경은 새 작성 계약 하나에 한정했다.
- DEV 게시·연결 canary **3/3**; Core/PMO 게시 ID·API 키 binding·PROD 불변. 활성 profile은 비어 있고 모든 route는 local이다. main·push/PR/Release·배포 저장소 submodule 포인터는 변경하지 않았다.
- 각 1회·고정 순서·합성 5건·동일 non-blind reviewer이므로 탐색적 결과다. 5회 이상 균형 반복 qualification, 전체 배터리, UI 검증을 대체하지 않는다. 평가 원본은 보존하며 실행별 테스트 임시 폴더만 정리한다.

다음 단위는 **요청의 작성 가능 여부 판정**이다. 측정 결과 완성과 단순 검토 요청을 명시적으로 구분하고, 실제 측정값·비교 기준이 없는 결과 완성은 writer 전에 보류해야 한다. 현재 selector에 섞인 분류·이력·충돌 진단의 출력 부담을 줄이는 것도 검토한다. 검토 요청의 사람·필수 기준·문서 링크는 typed 항목으로 전달해 누락 여부를 코드로 확인하는 편이 낫다. 이 추가 설계는 아직 구현하지 않았으며, 단순히 문구 금지 목록이나 LLM 노드를 더하는 것으로 완료 처리하지 않는다.

v19는 비활성 DEV 실험 후보로 남긴다. 구조가 단순해졌다는 이유로 운영 적용하거나 품질 향상을 확정하지 않는다.

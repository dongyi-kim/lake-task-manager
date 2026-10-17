# MAXIS/Dify 개편 구현 기록 — 2026-10-17

## 현재 결론

**1차 기반 구현과 오프라인 통합 검증 완료. 전체 개편·실서버 적용·성능 개선은 미완료.**
실행 계측은 기존 원격 Role 경로에 연결했다. 새 Research → Task draft 조정기와 Knowledge
릴리스는 기반 모듈이며 사용자 session 및 실제 서버에 아직 연결하지 않았다.
내부 시범 프로필은 설정 가능하지만 실제 서버 통제 검증기 없이 실행할 수 없다.

후속 확인으로 인증 문제를 정정했다. 이전 probe의 비밀번호 형식이 잘못됐으며, 현재 Dify의
UTF-8→Base64 형식에 맞추자 **기존 승인 자격증명으로 HTTP 200**이다. Core/Editor/PMO
Service API `/parameters`도 모두 200/계약 일치다. 접속정보 변경은 필요하지 않았다.
아래 기반 구현 당시에는 서버 자산을 변경하지 않았다. 후속 합성 실험의 생성 자산과 실행 결과는
별도 기록으로 구분하며 기존 실행 앱·route·활성 profile은 유지한다.

후속 [실서버 점검 기록](evaluations/2026-10-17-maxis-native-synthetic-capability.md):
합성 Knowledge 1개와 신규 Workflow 5개를 게시했다. 조회 도구 3개는 실행 성공했으나
Research ReAct는 `stop` 미지원, Function Calling은 도구 호출 없이 `not_found`를 반환해 실패했다.
현재 공유 모델의 `function_calling_type=no_call`을 확인했고, 13개 LTM 앱에 영향을 줄 수 있는
옵션 변경은 안전 검토가 차단하여 **미실행**이다. 범위를 명시한 사용자 승인 뒤 재검증해야 한다.
MAXIS 등록 Node는 여전히 0개이며 Dify Agent와 구분한다.

후속 로컬 작업은 `codex/maxis-taskflow-synthetic`에서 진행한다. 전송 계층만 분리한
`codex/maxis-taskflow-transport`의 커밋은 `ddd103b`다. 최소 도입 단위는 이 전송 계층이며,
그 위에 합성 DSL/fixture/테스트(`3f6d568`), 마지막으로 서버 실행 기록을 나눠 검토한다. 합성 생성기와 테스트는
서로의 노드 연결·계약을 검증하므로 함께 유지한다. 어느 단계도 기본 사용자 경로를 활성화하지 않는다.

## 브랜치와 변경 범위

기준: `f526eaac5c7bd742e720c5918c1087f9bc6f6ef2`.
사용자의 기본 checkout과 editor 수정은 건드리지 않았다. 아래 독립 branch는 push하지 않았다.
`codex/maxis-plan-integration`은 이 변경들을 모아 검증하는 로컬 통합 브랜치이며,
독립 목적을 하나의 PR로 게시하기 위한 브랜치가 아니다.

| 단위 | 전용 브랜치 / 원본 커밋 | 실제 구현 | 미구현·미검증 |
|---|---|---|---|
| 요청 계측 | `codex/maxis-request-metrics` / `36ad15e` | DifyClient → backend → audit 연결, native run UUID, 전송/결과 상태, 관측된 usage/cost | 중첩 모델 총호출·총비용·사용자 전체 지연·업무 품질 |
| 내부 시범 환경 | `codex/maxis-internal-pilot` / `4046128` | 제3 profile, 키·자산 분리, HTTPS allowlist, 보존/ACL/릴리스 증적 결속과 실행 차단, 상태 UI | 실제 서버 통제 검증기, pilot materializer, 승인된 서버 자산 |
| Knowledge 수명주기 | `codex/maxis-knowledge-release` / `fa246fa` | policy/glossary/design 후보, 통째 heading/용어 청크, revision/hash/권한 검사, exact lookup, 교체 dry-run | live ACL 공급, 서버 청크 parity·업로드, Dify 검색 결과의 EvidencePack 편입 |
| Research → Task draft | `codex/maxis-taskflow-v1` / `cb0089d` | 버전 계약, 추가 조회 2회, 공통 예산, pin/hash 검증, 취소·철회·replay 보호, 기존 ResponseGuard 재사용 | native Research Agent/부모 workflow, 실서버 adapter, session/UI 연결, 실제 품질 |

통합 코드 커밋: `bc59c1100f69bfe23a4d208a09ffd976e8d2dec5`.
원본 커밋과 통합 cherry-pick 커밋의 SHA가 다른 것은 정상이다. README 결합 충돌은 두 설명을
모두 보존해 해결했다. deploy 저장소의 submodule 포인터·branch·tag·Release는 변경하지 않았다.

## 중요한 동작 경계

- `completed`는 계약에 맞는 초안을 받았다는 뜻이지 사용자 품질 합격이 아니다.
  `quality_status=not_evaluated`, 누락 비용·중첩 호출 수는 `null`이다.
- 새 TaskFlow의 `needs_evidence`는 로컬 조회 요청일 뿐 조회 권한이 아니다.
  `needs_input`/`blocked`에는 실행 가능한 부분 초안을 담지 않는다.
- local budget은 8 model/12 logical tool/60초를 예약·검사한다. 실제 adapter가 호출 전 예약해야
  하며, 이미 시작한 원격 작업을 로컬 timeout으로 종료했다고 주장하지 않는다.
- 취소·권한 철회 시 저장된 근거와 초안을 제거한다. 만료는 접근 또는 명시적 sweep 때 처리하므로
  session 연결 시 idle cleanup이 필요하다. 서버 보존 통제는 별개다.
- pilot 정책의 콘텐츠 24시간/metadata 7일은 신규 시범 자산의 요구사항이며 실제 서버에 적용된
  설정이 아니다. `bindingValid=true`여도 현재 `valid=false`와 실행 차단을 유지한다.
- 승인 토큰 발급이나 Jira write를 새 조정기에 넣지 않았다. 기존 exact-payload 승인과
  Action Executor의 단독 쓰기 권한을 유지한다.
- 실제 내부 용어집은 로컬에서 48개 용어·12개 출처의 형식만 검증했다. 오류 0·별칭 충돌 경고 6.
  비공개 본문을 Git에 넣거나 Dify/MAXIS로 업로드하지 않았다.

설명: [TaskFlow 계약](../../deploy/maxis-dify/taskflow-v1.md),
[내부 시범 프로필](../../deploy/maxis-dify/internal-pilot.md),
[Knowledge 릴리스](../../deploy/maxis-dify/KNOWLEDGE-RELEASE.md),
[요청 계측](../../app/agent/README.md).

## 검증 기록

- 코드 독립 검토 2회씩: Knowledge 7→9/10, TaskFlow 7→9/10,
  pilot 8.5→9/10, metrics 9→9/10. 점수는 코드 검토 의견이지 Agent 품질 점수가 아니다.
- 수정된 실제 지적: metadata의 credential 형태 거부, 만료된 Knowledge 비교 기준 허용,
  pilot HTTPS 호출부 2곳, 완료 초안의 필수 질문/빈 item 차단,
  추가 조회 계획 ResponseGuard 적용, 최종 권한 검사의 시간까지 budget에 포함.
- 최종 통합 선택 suite: **625 passed / 1 skipped / 1 warning**, 34.86초.
  20개 관련 test file에 client/audit/role runtime, profiles/pilot/settings/persistence,
  production graph, TaskFlow, Knowledge/glossary/manifest, context/response guard,
  shadow/fail-closed, performance metrics, UI 정적 계약, prompt integrity 포함.
- skip 1건: sandbox Windows 계정의 DPAPI master-key profile 미가용.
  warning 1건: 기존 `langchain_community.vectorstores` deprecation.
- 전체 repository suite·GitHub Actions·브라우저 UI 렌더링·실 LLM 품질 배터리는 미실행.
  테스트 통과로 p95/token/품질 향상을 주장하지 않는다.

후속 최종 회귀: **231 passed, 경고 0, 2.64초**. TaskFlow transport, synthetic bundle,
기존 client, TaskFlow integration, contracts registry, prompt integrity의 6개 파일이다.
실서버 capability 점검은 별도이며 이 테스트 수에 포함하지 않는다.
후속 코드 독립 검토는 transport 9→9/10, bundle 9/10·8.5/10 이후 icon 교정 및 전략 변경분 9/10.
운영 점검 스크립트는 7→9/10, 최종 기록은 9/10이며 native 실행 성공과 기능 실패의 표현을 정정했다.
이 점수는 구현·기록 검토 의견이지 LLM 업무 품질 점수가 아니다.

```powershell
$env:JIRA_ENV = 'mock'
python -B -m pytest -q --basetemp=.cache/test-tmp/taskflow-final-validation-20261017 -o cache_dir=.cache/pytest tests/agent/maxis/test_taskflow_client.py tests/agent/maxis/test_taskflow_synthetic_bundle.py tests/agent/maxis/test_client.py tests/agent/integration/test_maxis_taskflow.py tests/agent/maxis/test_contracts_registry.py tests/agent/contracts/test_agent_prompt_integrity.py
```

재현 경로는 통합 worktree이며 Python은 deploy 저장소의 `.venv/Scripts/python.exe`를 사용했다.
pytest cache는 `.cache/pytest`, 각 실행 tmp는 `.cache/test-tmp/` 아래만 사용했다.
이번 실행별 tmp만 정리하며 다른 worktree·raw 평가·credential cache는 보존한다.

## 최초 인증 관측 및 후속 정정

2026-10-17 12:55:38 KST에 Dify console 로그인으로 1회 요청했다.
첫 요청부터 network-enabled process와 TLS 검증을 사용했으며 응답은 **HTTP 401**이었다.
세션 미수립으로 API 자산 조회 0, 자산 변경 0, 모델 호출 0. 이후 반복하지 않았다.
브라우저 로그인 시도도 로그인 화면에 머물러, 사이트 투어를 완료했다고 보고하지 않는다.

내용 없는 관측 파일(상위 deploy 저장소 기준):
`.cache/worktrees/maxis-request-metrics/.cache/maxis/capability-audit-20261017.json`

SHA-256: `a5dc709b137767b684451545a6c0db8195f719d2dcbd504c0ebb0f4f7e2c710f`.
credential·cookie·token·응답 본문은 증적에 없다.
당시 이 401을 차단 사유로 기록했지만, 후속 조사에서 **probe가 raw 비밀번호를 보내고 있던
오류**를 확인했다. 현재 사이트 번들과 공식 signin 구현은 UTF-8→Base64를 사용한다.
수정한 요청은 200이며 인증 실패를 자격증명 만료나 서버 기능 제한으로 해석하면 안 된다.

후속 읽기 전용 확인: LTM 앱 8개는 advanced-chat이며, 최신 Core/Editor/PMO 그래프에
native Agent 노드는 0개다. 설치된 `langgenius/agent:0.0.18`은 `ReAct`와 `function_calling`을
제공한다. 현재 Core 모델은 `gpt-5.4-mini`, 실제 export DSL은 `0.6.0`이다.
플러그인 존재가 실행·모델 tool-call 지원·전체 예산 집행의 증거는 아니다.
`rbac_enabled=false`를 관찰했으나 이를 모든 ACL 기능 부재로 확대 해석하지 않는다.
retention·사용자별 ACL·중첩 총예산·불변 배포 통제는 아직 별도 검증 대상이다.

정정 증적: `codex/maxis-taskflow-synthetic` worktree의
`.cache/maxis/auth-observation-20261017.json`, `agent-strategies-20261017.json`.
비밀값·세션·전체 프롬프트는 저장하지 않았다. 최초 401 관측 파일도 삭제하지 않는다.

## 재개 순서

1. **완료:** 기존 `.cache/maxis/dify.json` 승인 자격증명으로 올바른 로그인 형식과 Service API 접속 확인.
   비밀값은 채팅이나 Git에 넣지 않는다.
2. **일부 완료:** 실제 설치 Agent 전략과 조회 도구 3개의 실행 확인. 공유 모델 옵션 변경은
   13개 LTM 앱 영향 범위에 대한 승인 대기다. 승인 후 새 실행으로 도구 CALL·근거 반환과 기존 앱
   회귀를 확인한다. 불변 버전·retention·권한·총예산은 미검증이며 boolean config로 대체하지 않는다.
3. 이미 게시한 합성 Research 앱을 사용해 근거 정리까지 검증하고 WorkDraft 부모 workflow를
   구현한다. MAXIS Node 저장에 수반되는 Git/Jenkins 생성과 비공개·secret binding을 확인한 뒤
   private wrapper를 만든다. 기존 게시본은 덮어쓰지 않으며 내부 Knowledge는 검증된 pilot에만 둔다.
4. 실서버 adapter와 기존 session/Query Runner를 연결한다. scoped read/full pagination,
   현재 권한 재검사, 요청 전체 예산, 릴리스 pin, native request 계측을 end-to-end 검증한다.
5. 선별 Knowledge의 새 릴리스·서버 청크 parity·exact/semantic 검색을 검증한다.
6. 연구→초안 20 positive/10 edge 및 기존 배터리를 같은 조건으로 직접 판독한다.
   critical 허위사실/ACL/무승인 쓰기 0건, usable≥80%, 평균≥4/5, source recall≥90%를 목표로 한다.
   자격을 얻은 기준선과 5회 균형 반복 비교 후 p95−30%·tokens−25%·수작업시간−30%를 평가한다.
   미달하거나 미관측이면 활성화·개선율 주장을 보류한다.

이 순서는 남은 구현 작업이다. 이번 변경을 기존 semantic 역할의 MAXIS/Dify 이관 완료로
해석하면 안 된다. 다음 작업의 중심은 추가 guard 증가가 아니라 **실제 근거를 읽고 쓸 만한 초안을
반환하는 첫 수직 흐름을 서버와 사용자 화면까지 연결하는 것**이다.

# MAXIS 전체 성능 개선 — 입력 전달·관측 기반 1차 구현

## 범위와 현재 상태

전체 계획 중 **역할별 오프라인 입력 진단과 기존 실측 재사용 기반**을 구현했다. 전체 계획 완료나 품질 향상·응답 시간 단축을 주장하지 않는다. 새 브랜치 `codex/maxis-performance-baseline`은 기존 `codex/maxis-dify-migration`의 `1acdd90`에서 분리했다.

- 원격 대상 8개 Role의 정상 사례와 overflow/stale/missing, 총 11개 고정 합성 사례.
- 실제 `role_payload → _context_pack → ContextGuard`와 실제 Role 출력 schema 사용.
- 선언·허용·존재·투영·최종 전달 단계별 필드 이름/개수/바이트와 누락 사유 진단. 본문·사람·티켓 식별자·secret은 진단 산출물에 복사하지 않음.
- 기존 Editor raw를 읽어 오류 포함 지연·관측 토큰·응답 수를 재집계. 원본 수정·재채점·유리한 시도 선택 없음.
- 생산 코드, prompt, 모델, Workflow 게시, Knowledge, DEV 고정 합성 보호, 활성 profile/route, PROD, 키는 변경하지 않음.

## 작은 기능별 구성

| 파일 | 단일 책임 |
|---|---|
| `tools/maxis_role_eval_cases.py` | 고정 사례 선택과 실제 입력 조립 |
| `tools/maxis_context_diagnostics.py` | 원문 없는 구조적 전달/누락 관측 |
| `tools/maxis_performance_metrics.py` | 모든 시도·미관측을 보존하는 집계 |
| `tools/maxis_performance_baseline.py` | 오프라인 실행 및 새 증적 저장 |

단계마다 새 Agent/LLM을 추가하지 않았다. fixture builder의 mock patch는 **별도 오프라인 프로세스의 동기 실행 전용**이다. 앱 worker나 동시 요청 안에 import하여 호출하지 않는다. 일반 사용자 입력·state·설정·파일을 fixture로 넣는 인터페이스는 없다.

## 측정 계약과 기존 벤치마크 재사용

- `maxis-offline-baseline.v1`, 입력 fixture battery `2.0.0`, fixture `ltm-maxis-role-offline.v1`.
- protocol `3.0.0` / rubric `2.0.0` 식별자를 기록하되 `runKind=offline-diagnostic`, `qualificationEligible=false`, 의미 평가 미실시로 명시.
- case 명세와 도구 source 해시를 manifest에 포함. runtime tree/commit/dirty 및 도구 파일별 해시를 별도 기록.
- 대화 S2/S3/S6/S8, Create BUG2, Editor CMP2의 시나리오를 참조한 **새 축소 합성 사례**다. 원래 배터리와 입력·기대값이 동일한 평가나 전체 58건·Knowledge 25건 재실행이 아니다.
- 기존 Editor component raw 지원은 `replay-editor`에 한정한다. 다른 suite raw를 추측해 변환하지 않고 거부한다.
- 비용·native LLM 호출 수·실사용 완료·필수 질문·수정 시간은 원본에서 확인되지 않으면 `null`. `responseReceived`를 내부 LLM 호출 수로 바꾸지 않음.
- p50/p95는 모든 시도의 nearest-rank. 지연 하나라도 미관측이면 전체 percentile은 `null`, 부분 관측 합계만 별도 기록.
- `requiredContextComplete`는 fixture가 지정한 필드의 **구조적 전달 여부**다. stale 사례도 필드 자체는 전달되므로 true가 가능하다. freshness·partial은 별도 확인하며 이 필드를 답변 가능성·품질·정확성으로 해석하지 않는다.

## 관측과 해석

11개 모두 현재 보안/용량 계약 안에서 입력을 구성한다. 정상 8개는 필요한 context가 전달된다. overflow는 뒤쪽 `interpretation`이 개수 제한에서 빠지고, `situation`·`evidence`·`knowledge_brief`가 전체 pack 예산에서 온전히 전달되지 않는 것을 재현한다. missing 사례는 없는 필수 3개 키를 명시적으로 보고한다. stale 사례는 오래된/부분 근거라는 상태를 유지한다.

이는 **큰 합성 입력에서 구조적 손실이 발생한다는 증거**이며, 과거 Editor 품질 실패의 원인이나 실제 업무 발생률을 증명하지 않는다. source 내용의 의미상 필수 조항 손실까지 자동 판정하지 않는다.

과거 v19 Editor 5건 raw를 재집계하면 총 23,314ms, p50 4,599ms, p95 6,016ms, 토큰 17,727이다. 4건 `ok`와 1건 `blocked`는 프로토콜 상태일 뿐 실사용 성공 수가 아니다. 기존 직접 품질 판정(2.40/5·유용한 결과 0/5)은 [원래 보고서](maxis-editor-writing-quality-20260916.md)를 그대로 참조하며 새 도구로 재채점하지 않는다.

## 실행

worktree의 Python 환경에서 다음 명령을 실행한다. 두 명령 모두 API/모델 호출이 없고 새 raw 파일만 `.cache/agent-evaluation/`에 만든다. 같은 run group과 명령을 재사용하면 거부하며 원본 raw와 실패 예약 파일은 보존한다.

```powershell
python -B tools/maxis_performance_baseline.py diagnose
python -B tools/maxis_performance_baseline.py replay-editor .cache/agent-evaluation/maxis-editor-20260916T012211Z-411b16d6/candidate.json
```

`--help`, 알 수 없는 옵션, 하위 명령 생략은 평가를 시작하지 않는다. 외부 파일 경로·cache 밖으로 나가는 경로·32MiB 초과 파일은 재집계 입력으로 받지 않는다.

## 검증 및 증적

- 관련 테스트 **132개 통과**: 신규 52개와 기존 ContextGuard·DEV profile gate·prompt integrity·Editor runner 80개. 전체 CI suite, 실 LLM, 전체 workflow/UI는 미실행.
- 초기 CLI 통합에서 strict datetime JSON 역직렬화 오류를 발견하고 `model_validate_json`과 실제 11-case runner 회귀 테스트로 수정. 최초 실패 예약은 보존.
- 첫 진단의 missing 사례가 빈 required 목록을 갖던 문제를 수정하고 battery를 `1.0.0 → 2.0.0`으로 올림. 초기 raw는 덮어쓰지 않음.
- 독립 검토 1차 8/10의 지적을 반영: 링크/junction 해석 후 cache 내부 경계 재검사, guard 실패의 고정 원인 코드 보존. CLI 실패 artifact도 비밀 없는 실패 상태로 기록하며 기존 예약/결과는 덮어쓰지 않는다.
- 최종 source commit, 재실행 raw 및 독립 검토 결과는 아래 실행 기록에 연결한다.

## 남은 계획과 다음 변경 경계

1. **평가 경로의 나머지:** 합성 입력에서 원격 응답 처리까지 연결한 Core/PMO component, 실제 전체 앱 조합, 별도 holdout, 역할/node별 native trace 계측. 현재 코드는 입력 조립까지만 다룬다.
2. **전체 기준선:** 대표 업무의 수동 Jira 대비 완료·수정 시간, 8개 Role 품질, 호출 예산/모델 가용성 확인. 아직 미측정이며 0이나 성공으로 채우지 않는다.
3. **입력 개선:** 재현된 누락에 대한 최소 typed projection을 별도 동작 변경 브랜치에서 구현하고 동일 사례로 비교. 범용 허용 목록·크기 제한·DEV 보호를 풀지 않는다.
4. **후속:** 입력 충족 판단/Writer 구조 A/B → Knowledge 조항 회수율 → 모델 A/B → 관측 병목 최적화 → 전체 배터리 반복/품질 검증 → 기능별 전환. 이번에 순서를 건너뛰어 활성화하지 않음.

실사용 완료는 사실·필수 요건을 충족하고 중대한 재작성 없이 쓸 수 있는 결과로 평가한다. 최종 후보는 핵심 업무별 완료율과 사용자 수정 시간까지 비교하고, 모델 지연만 좋아졌다는 이유로 승격하지 않는다.

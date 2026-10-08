# Editor 근거 선택 범위 입력 개선

Editor가 사용할 수 없는 링크나 부모가 아닌 Jira 근거를 선택하는 실수를 줄이기 위한 작은 입력 변경이다.
선택 모델에 허용 범위를 먼저 표시하되, 최종 검증과 업무 의미 판단은 분리한다.
별도 앱 브랜치 `codex/maxis-editor-selector-eligibility`, 기준 커밋
`5804573edb232522046d88330d71d168b34eca8b`에서 로컬 구현·검증을 마쳤다.
이후 clean `c595d0a34bcd394f58e213fc237dd382dfc7428d`를 DEV에 게시하고 아래 탐색 평가를 수행했다. 운영 활성화는 하지 않았다.

## 근거와 변경 목적

[직전 v24 진단](maxis-editor-selector-input-v2-20261008.md)에서 실제 요청에 결속된 포트를
고정 로컬 Guard로 재현했다. Role Editor는 링크가 없는 출처를 필수 링크로 골랐고,
CMP8은 현재 대상의 Jira unit 3개를 부모 배경으로 골랐다. 과거 실행 UUID는 직접 관측하지 못했으므로
로컬 재현을 과거 서버 소스나 전체 실패 원인의 확정으로 확대하지 않는다.

이번 목적은 선택 전에 구조상 가능한 위치를 명시하는 것이다. 누락된 업무 지식 보충,
메타데이터의 본문 사용 여부, 최신성·상충 사실의 의미 판단은 별도 문제다.

## 단위 기능과 계약

기존 `prepare`가 만든 `editor-input.v2`는 Guard·Enforcer에 문자열 그대로 전달한다.
새 입력 전용 코드가 그 검증된 결과를 읽어 `editor-selector-input.v1`을 만들고,
DraftSpec LLM만 새 `selector_input` 포트를 읽는다. 새로운 Role·모델 호출·노드는 추가하지 않는다.

| 표현 | 뜻 | 의미하지 않는 것 |
|---|---|---|
| `current` | Jira unit 소유자가 비어 있지 않은 요청 대상과 일치 | 최신 사실이나 유용한 본문이라는 보증 |
| `context` | Jira unit 소유자가 검증된 부모에 해당 | 현재 대상의 업무로 승격할 권한 |
| `shared` | 비 Jira 근거로 두 writing bucket의 소유권 제약이 없음 | 현재 요청에 실제 적용된다는 판단 |
| `neither` | 두 writing bucket에 구조상 허용되지 않거나 Jira 소유자가 불명확 | 폐기할 근거라는 뜻; history·conflict 검토는 가능 |
| `link_choices` | 기존 catalog에서 `available_link is True`인 출처 ID | 모델이 만든 URL이나 문서 관련성 순위 |

이 표의 범위는 **writing bucket 소유권 자격**이다. 각 source 안에서 unit을 네 그룹으로
나누되 `unit_id`, `entity_key`, `path`, `text`를 모두 보존하고 복제하지 않는다.
source 순서와 그룹 안 상대 순서도 보존한다. 요청·seed·entities·Knowledge 문맥은 그대로다.
history·conflict 및 실제 관련성은 모델이 판단하고, 부적절한 선택은 기존 Guard가 계속 거부한다.

Input 전용 코드만 새로 결합한다. 기존 Assembler·DraftSpec 검증/컴파일 코드·writer prompt·schema·
최종 ContractGuard·ResponseGuard·23노드/26엣지는 유지한다. Control은 기존 입력과 route를 그대로
통과하며 새 포트는 사용하지 않는다. 새 포트가 없다고 v2 입력으로 자동 대체하지 않는다.

## 검증과 호환 경계

- 원본 v2 문자열과 각 unit의 모든 필드·개수·ID를 보존하고, intent나 추정 URL을 링크로 추가하지 않는다.
- 빈 owner/target, 혼합 Jira source, 부모 충돌·순환·미확정 관계에서 쓰기 가능 범위를 넓히지 않는다.
- 두 포트 각각 9,000 UTF-8 bytes를 넘으면 실패한다. 자르기·항목 삭제·자동 축소는 하지 않는다.
- 두 graph 검증 진입점이 코드·포트·prompt 교환과 우회 연결을 거부해야 한다.
- 기존 writer 입력·최종 plan의 정상 동작과 잘못된 선택 거부를 함께 확인한다.

외부 HTTP API·CLI·환경 설정·LTM checkpoint 계약은 바꾸지 않는다. DEV/PROD의 기존 환경 binding을
유지한다. 내부 Input 코드·출력 포트·selector prompt는 같은 게시본으로 함께 갱신해야 하며,
서로 다른 게시본의 중간값을 혼용하거나 재생하지 않는다. Dify의 진행 중 실행 재개는 미검증이다.

아래는 오프라인 검증 결과다. 회귀 통과나 입력 크기 적합성만으로 실제 모델의
품질 향상을 주장하지 않는다. 당시 새 후보의 같은 전체 배터리와 직접 원문 평가를 다음 실측 기준으로 정했다.

## 로컬 검증

입력 전용 코드는 44줄이다. 기준 Git YAML과 객체를 비교해 Editor Input의 code/outputs와
DraftSpec LLM의 두 prompt만 바뀌었음을 확인했다. 다른 노드·엣지·모델·설정은 동일하다.
기존 Assembler와 DraftSpec 코드의 raw SHA도 각각 `047129df…7c87`, `b944d3b9…47f3`로 유지했다.

구현 전 새 포트·Control·prompt 계약의 red 4건을 재현했다. 작성자의 초기 집중 검사에서는
fixture import 경로 5건과 이전 포트 assertion 1건이 실패해 테스트를 수정했다.
이후 중복을 제외한 집중 검사 124건이 통과했다. 첫 관련 전체 실행은 **853 pass / 1 fail,
283.45초**였다. `test_dify_bundle`의 wrapper 변조가 옛 함수명을 사용해 no-op이 된 실패였다.
양쪽 wrapper 테스트를 실제 새 entrypoint 치환과 존재 assert로 고쳤으며, byte 경계 3건을 포함한
집중 검사 5건이 4.00초에 통과했다. 제품 코드는 이 과정에서 바꾸지 않았다.

최종 동결 관련 10파일 **857개가 284.37초에 통과**했다. 대상은 `test_editor_selector_input`,
`test_editor_plan_input`, `test_editor_routing`, `test_editor_draft_spec`, `test_editor_graph`,
`test_editor_assembler`, `test_dify_bundle`, `test_dify_environment`, `test_dify_schema_types`,
`test_agent_prompt_integrity`다. 전체 앱 suite나 UI 검증은 아니다.
독립 검토 두 차례 모두 9/10이다. 1차는 17회귀·순수 반례 57개, 2차는 22회귀와 원본/핀 보존을 확인했다.
정확한 byte 경계는 기존
`prepare` 반환을 주입한 단위 검사이고, 실제 원본 pack을 거치는 보존·초과 검사는 별도로 유지한다.
정적 bundle CLI는 3개 workflow를 통과했다. 이 로컬 검증 단계에서 실제 API·모델 호출은 0회였다.

## 고정 입력의 크기 변화

동일 fixture의 최대 Knowledge 본문 1,024바이트와 100바이트 문서명 두 개를 사용했다.
한글을 포함한 UTF-8로 작성자와 root가 각각 확인했으며, 원문 unit 45개와 source 순서를 모두 보존했다.

| 사례 | 기존 v2 bytes | 새 selector bytes |
|---|---:|---:|
| CMP2 | 8,502 | 8,662 |
| CMP3 | 8,512 | 8,673 |
| CMP6 | 8,492 | 8,653 |
| CMP8 | 8,438 | 8,599 |
| CMP9 | 8,437 | 8,597 |

새 표현은 160–161바이트 늘어난다. 예산 여유는 이 다섯 입력에서 327–403바이트이고,
다른 큰 입력의 수락을 보장하지 않는다. 기존 v2가 통과해도 새 포트의 크기 초과로 거부될 수 있다.
이는 원문을 보존하는 선택의 비용이며 실제 token·지연·의미 품질 측정값은 아니다.

## 후속 DEV 실측

2026-10-08 09:35 UTC에 v25 전용 단발 도구로 Editor만 import 1회·publish 1회 했다.
사전·import 후·publish 후 세 앱의 native graph/config/UUID·dependency·기존 key를 대조했다.
export 비교는 과거 증적에 고정한 opaque dataset leaf 6개만 복사본에서 투영하며, 실제 dataset 식별은
native graph로 확인한다. import의 `completed-with-warnings`는 0.5.0→0.6.0 형식 차이와 빈 상세를
명시한 제한적 재조정 정책으로 처리했다. 서버 제품 버전이나 원자적 게시를 증명한 것은 아니다.

게시 도구와 canary는 최적화 모드에서도 제거되지 않는 조기 거부를 포함한다. 관련 두 파일의
전체 오프라인 검사 295개(186.24초), 각 도구의 독립 검토 두 차례 9/10을 통과했다.
별도의 합성 canary는 3/3 통과했지만 품질 검사가 아니며, 운영 경로는 계속 local이다.
새 Editor UUID는 `b3cdd09e-2ca0-415c-86b0-f64f66f268ab`이다. Core·PMO·모델·Knowledge·PROD는 변경하지 않았다.

같은 benchmark·모델·harness로 전체 Editor 5건과 Role 11건을 각각 새 process에서 1회 실행했다.
두 primary raw 모두 complete/bindingUnchanged이며 clean c595d0a에 결속된다.
Editor는 자동 2/5→1/5, 25.1초이고 CMP8 요청 오류는 없어졌으나 CMP2·CMP9가 차단됐다.
Role은 Editor 요청 오류가 status=`ok` 응답으로 바뀌어 `ok` 1/11·차단 10건, 31.889초다.
[새 동일 패널의 Editor 직접 검토](evaluations/maxis-editor-component-20261008-eligibility.md)는
v24 2.82→v25 2.18/5·사용 가능 1/5→0/5다. CMP8은 개선됐지만 CMP2·CMP9가 차단됐고,
CMP3은 미확정 성능 방향을 전제했다. [Role 후보 단독 검토](evaluations/maxis-role-component-20261008-eligibility.md)는
2.32/5·사용 가능 0/11이다. 빈 요청의 안전 중단 1건은 별도 경계 성공으로 기록했다.
단일 탐색·비블라인드 결과이며 국소 계약 성공을 전체 업무 사용성 향상으로 확대하지 않는다.
실제 내부 모델 호출 수·비용은 미관측이다. 실패 raw를 후속 성공으로 교체하지 않는다.
[게시·배터리 식별자](../../deploy/maxis-dify/deployments/dev-editor-eligibility-2026.10.08.yml)에 경로·SHA를 보존했다.

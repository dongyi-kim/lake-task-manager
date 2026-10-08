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

## 후속 진단: 선택 통과 후 문안 검증에서 차단

2026-10-08 10:26:58–10:27:00 UTC에 CMP2·CMP9 진단을 위해 고정 시간창의 기존 합성 실행
후보 5개를 읽고, 정확히 일치하는 두 pack만 증거로 채택했다.
두 경우 모두 selector는 `ok`, DraftSpec Guard도 통과했고 Description LLM이 네 section을 반환했다.
이후 **DraftSpec Enforcer가 `blocked` plan으로 변환**했다. 조회한 native 포트를 메모리에서 고정 로컬
`prepare_spec`·`enforce_spec`을 재현한 결과가 각각 일치하며, 후단 전달과 primary 응답 SHA도 같다.
따라서 이 두 차단은 selector가 스스로 중단한 경우가 아니다. 정확히 어떤 거부 조건인지는 아직 미확정이다.
두 최종 응답은 같은 빈 blocked envelope이므로 최종 hash만으로 실행을 식별하지 않았다.
고정 시간창·case별 전체 pack hash·단일 일치 실행·포트 연결을 함께 대조했다.

| 확인 항목 | CMP2 | CMP9 |
|---|---|---|
| 선택 current / context / history / 필수 link 수 | 5 / 9 / 8 / 1 | 5 / 9 / 18 / 1 |
| Selector / Guard / Description 실행 | ok / 통과 / 성공 | ok / 통과 / 성공 |
| Enforcer local·native 결과 | blocked·일치 | blocked·일치 |
| 실제 primary 응답 hash 일치 | true | true |
| Contract Guard의 executed UUID 직접 관측 | v25와 일치 | v25와 일치 |
| Writer 입력 포트 직접 관측 | false | false |

관측된 UUID는 `b3cdd09e-2ca0-415c-86b0-f64f66f268ab`다. 이는 요청의 workflow_version이나
최종 응답 echo를 대신 사용한 값이 아니라 native `executed_workflow_id` 포트다. 현재 게시 graph의
해당 변수는 `sys.workflow_id`에 연결돼 있다. Writer 입력 자체는 직접 확인하지 못했으며,
native 포트와 로컬 코드의 일치를 전체 서버 구현·당시 모든 단계의 모델·의미 품질 보증으로 확대하지 않는다.
전후에 검증한 graph/config는 이번 조회 당시의 게시본이다. 과거 시점의 code bytes를 별도로 직접
조회해 보존했다는 뜻이 아니며, selector의 구조 계약 통과도 선택 내용의 의미 품질 통과가 아니다.

Console GET은 8회(진단 조회 전후 published graph 2·목록 1·고정 window 내 후보 5), credential-free preflight GET과
로그인 POST는 각 1회 별도다. 모델·graph·profile 쓰기는 0회이고 프로필·게시 binding 전후 일치를 확인했다.
새 단발 helper의 오프라인 85검사(작성자 8.58초·root 8.43초)와 독립 검토 두 번 9/10을 거쳤다.
원문·추론·remote 예외를 저장하지 않고 상태·해시·횟수만 기록했다. 2MiB 제한은 수신 body와
정규화 JSON에 적용되며 압축된 전송 바이트 자체의 계측은 아니다. 동일 helper/claim은 재실행하지 않는다.

진단 raw는 `.cache/maxis/editor-v25-boundaries-native-20261008-c595d0a.json`, SHA는
`5653c896fee89114ec4832b17f47e71238209d3a867bc9355f8de3e34d12897f`다. 이것은 후속 보조 증거이며
primary 실패나 앞의 정성 점수를 교체하지 않는다. 다음 단계는 동일한 검증 조건을 유지한 채
고정 로컬 Enforcer의 거부 지점을 좁히는 것이다. 운영 경로는 local로 유지한다.

### 고정 로컬 코드의 fact 검증 위치 확인

10:44:22–10:44:24 UTC에 같은 두 실행을 별도 단발 helper로 조회했다. 고정 로컬 `enforce_spec`의
trace 결과는 이전 진단의 case별 증거 전체 일치를 확인한 뒤에만 채택했다. 두 case 모두
`code/editor_draft_spec.py:190`에서 예외가 발생했다. 이 줄은 fact의 키가 정확히
`{type, unit_id, tag}`가 아니거나 `unit_id`가 해당 section의 허용 집합에 없으면 호출하는
`_fail()`이다. **키 불일치와 허용 집합 위반 중 어느 쪽인지는 아직 구분하지 못했다.**

이는 서버 stack이 아니라 같은 native 포트로 재현한 고정 로컬 함수의 예외 위치다.
`unique`는 소스 위치가 하나라는 뜻이며, 전체 초안에 불량 block이 하나뿐이라는 뜻이 아니다.
첫 거부 이후의 block은 검사되지 않았고 Writer의 native 입력 미관측 한계도 유지된다.
trace는 원본 함수 결과를 바꾸지 않으며 기존 trace를 복구했다. 원문·locals·예외 값은 저장하지 않았다.

Console GET은 5회(현재 published graph 전후 2·목록 1·같은 두 run의 node 조회 2),
별도 preflight GET·login POST는 각 1회다. 모델 호출·graph/profile 쓰기는 0회다.
오프라인 44검사(작성자 17.01초·root 17.11초), 독립 검토 두 번 9/10을 거쳤다.
receipt는 `.cache/maxis/editor-v25-enforcer-locations-20261008-c595d0a.json`, SHA는
`faba08519423a6b1495aefda3c5ff6b29053da0bb15db8f87bd4f5f32778b951`이다.
helper SHA는 `f8b1d497b4581ebcfa7cebacd5ecdbe0f6fe284c7b5dcb51915967c59dc5efc7`이다.
이 증거도 primary 실패·점수를 대체하지 않으며, 다음 최소 진단은 첫 거부 fact의 형식과 사용 범위를
원문 없는 고정 분류로 구분하는 것이다.

### 첫 거부 fact: 상위 Jira 배경을 DoD에 사용

10:58:16–10:58:18 UTC의 마지막 원인 분류에서 같은 두 run·이전 case 증거 전체·로컬 line 190을
다시 대조했다. 두 case의 첫 거부 fact는 모두 **키 집합이 정확하고 원본 catalog에 존재**하지만,
`context`로 선택된 **상위 Jira 티켓**의 unit을 `dod`에 사용한 경우였다.
`context`는 background에만 허용되므로 기존 Enforcer의 차단은 계약상 정상이다.

| 첫 거부 위치·분류 | CMP2 | CMP9 |
|---|---|---|
| section / section 내부 block 위치(1부터) | dod / 1 | dod / 1 |
| 초안 전체 block 위치(1부터) | 12 | 10 |
| 키 집합 / 원본 catalog 소속 | 정확 / 있음 | 정확 / 있음 |
| 선택 bucket / source / entity 관계 | context / jira / parent | context / jira / parent |
| 거부 분류 | selected_section_disallowed | selected_section_disallowed |

원문·ID·원문 키 이름은 저장하지 않고 고정 분류·index·bool·해시만 보존했다. Writer가 왜 이런
선택을 했는지나 native 입력 전문은 아직 확인한 것이 아니다. `tag` 검사는 해당 거부 뒤이므로
이 block의 tag까지 검증됐다는 뜻은 아니다. 첫 거부 뒤 추가 결함의 부재도
증명하지 않는다. 따라서 이 관측은 **출력의 범위 위반**을 확정하며, 입력 표현이 원인이라는 것은
후속 실험 가설이다. scope 검증을 완화하거나 상위 근거를 현재 티켓의 사실로 승격하지 않는다.

Console GET 5회, 별도 preflight GET·login POST 각 1회, 모델 호출·graph/profile 쓰기 0회다.
오프라인 49검사(작성자 17.76초·root 17.35초), 독립 검토 두 번 9/10을 거쳤다.
receipt `.cache/maxis/editor-v25-fact-admission-20261008-c595d0a.json`의 SHA는
`300a99b96654aa51f3a121bb780c24eb5916f5dbc8203335c0367bb3c955fdcb`, helper SHA는
`1daf83cae92e99722667802bc6b8fed5414240661bb7e04cd491fbfc37d3da34`다.
다음 후보는 동일 권한의 Writer unit을 그룹으로 묶는 입력 투영 한 가지다. fact 출력 형식·ID 변환이나
CMP3의 별도 의미 품질 수정을 섞지 않고 새 브랜치에서 같은 배터리로 확인한다.

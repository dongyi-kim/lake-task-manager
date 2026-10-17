# Synthetic native Research Agent capability slice

이 디렉터리는 **합성 자료만** 사용하는 오프라인 DSL 생성기다. 기존 Core/PMO/Editor,
실제 용어집, 활성 프로필은 수정하지 않는다. MAXIS Node 등록이나 TaskFlow 앱 연결도
이 생성기의 기능이 아니다. 결과 `research_candidate`는 직접 검토할 조사 문안이며
`TaskFlowResult.v1`, 실행 가능한 초안 또는 품질 합격 판정이 아니다.

## 파일과 단위 기능

| 파일/Workflow | 책임 |
|---|---|
| `runtime.py` | Dify Code 노드에서 그대로 실행할 고정 fixture 검증·조회 함수 |
| `bundle.py` | 실제 dataset/document/tool-provider/plugin 식별자를 주입하는 DSL 생성기 |
| `ltm-synthetic-research-rules-v1.txt` | 별도 합성 Knowledge의 유일한 문서·단일 chunk |
| `glossary_lookup` | 등록 표기와 정확히 일치하는 두 합성 용어의 정의 조회 |
| `evidence_lookup` | 고정 합성 Snapshot에서 출처 ID 하나 조회 |
| `domain_retrieve` | 고정 검색어로 실제 Knowledge Retrieval 실행 후 내용·출처 일치 검사 |
| Research Analyst | 세 조회 도구를 선택·호출하고 사실과 부족한 정보 요약 |

```text
Research: Start → Synthetic Case Gate → Native Agent → End(research_candidate)
                                              ├ glossary_lookup: Start → Code → End
                                              ├ evidence_lookup: Start → Code → End
                                              └ domain_retrieve: Start → Gate → Knowledge Retrieval → Admission → End
```

새 semantic Role을 늘리지 않는다. 정해진 조회는 모델을 사용하지 않으며 Research Analyst의
제한된 도구 선택만 Agent가 담당한다. 세 도구에는 context·본문·URL·credential 입력이나 쓰기
기능이 없다. `fixture_id=synthetic-research-v1`, `case_id=checkpoint_research`만 허용한다.
모든 조회 도구의 fixture는 모델이 선택하지 못하는 수동 바인딩이다.

## 생성 인터페이스

1. 별도 `only_me` 합성 dataset에 위 txt를 **하나의 chunk**로 준비한다. 기존 Knowledge 또는
   실제 업무 문서가 들어 있는 dataset을 재사용하지 않는다. txt와 `runtime.KNOWLEDGE_TEXT`는
   동일해야 한다. 원격 chunk가 달라졌으면 guard를 완화하지 말고 segmentation을 확인한다.
2. `build_tool_dsls(dataset_id, document_id)`로 세 DSL을 생성한다. UUID 형식 검사는 실제 자산
   소유·공개 범위 검증을 대신하지 않는다. import·게시·도구 등록은 별도 승인된 배포 절차다.
3. 각 새 Workflow app을 게시하고 `tool_registration(name, workflow_app_id)`의 payload로
   등록한다. 반환된 실제 provider ID를 이름별로 기록한다.
4. `build_research_dsl(observation, tool_provider_ids, model_plugin_unique_identifier=..., strategy='function_calling')`
   에 실제 설치된 Agent 선언, 현재 모델 설정, 정확한 모델 plugin identifier를 전달한다.
   모델 이름이나 provider를 생성기가 추측하지 않는다. 기본값은 관측된 `function_calling`이며,
   앱 이름에 `FunctionCalling`을 표시한다. 전략 설치 여부만으로 모델 호환성이 증명되지는 않는다.

CLI는 네트워크·secret 접근 없이 **새 디렉터리**에만 쓴다. 출력은 각 저장소 `.cache/` 아래에 둔다.

```powershell
python deploy/maxis-dify/taskflow_synthetic/bundle.py --output .cache/maxis/synthetic-tools tools --dataset-id <actual-uuid> --document-id <actual-uuid>
python deploy/maxis-dify/taskflow_synthetic/bundle.py --output .cache/maxis/synthetic-research research --observation .cache/maxis/agent-strategies-20261017.json --tool-bindings .cache/maxis/synthetic-tool-bindings.json --model-plugin <observed-plugin-identifier>
```

`tool-bindings` JSON은 세 **논리 이름**을 실제 provider UUID에 대응시키며 key/secret을 넣지 않는다.
등록·Agent 호출 이름은 `ltm_syn_20261017_v1_` 접두사를 붙인다. 예를 들어 논리 이름
`glossary_lookup`은 `ltm_syn_20261017_v1_glossary_lookup`으로 등록한다. 등록 payload와 Agent
바인딩은 같은 `REGISTERED_TOOL_NAMES`를 사용한다. workspace에 동명의 tool이 있으면 덮어쓰지
않는다. 다음 릴리스에서는 접두사도 변경하고 새 app/provider로 배포해야 한다.

## 검증 범위와 제한

- DSL `0.6.0`은 2026-10-17 현재 Core export에서 확인한 버전이다. 실제 생성본 import 성공,
  Agent 전략의 모델 호환성, 도구 호출 성공은 별도의 live canary로 확인한다.
- 2026-10-17 DEV canary에서 세 단위 도구는 실제 실행을 통과했다. Research `ReAct`는
  `gpt-5.4-mini` 경로의 `litellm.UnsupportedParamsError` (`stop` 미지원)로 실패했다.
  따라서 별도 새 Research 앱의 기본 전략을 `function_calling`으로 변경한다. 실패한 ReAct 앱과
  원본 결과는 보존하며 기존 v1 도구·fixture·전역 provider 설정은 변경하지 않는다.
  `ReAct`는 재현용 명시 옵션으로 남아 있다. Function Calling의 실행·품질 통과는 별도 검증 사항이다.
- 첫 Function Calling canary도 기능 검증에는 실패했다. HTTP/native 상태는 성공이었으나
  `CALL` 로그가 없었고 세 조회를 모두 `not_found`로 출력했다. 이후 secret을 제외한 실제 모델
  설정 확인에서 `function_calling_type=no_call`, `stream_function_calling=supported`,
  features=`[stream-tool-call]`이 관측되었다. 모델의 도구 전달이 비활성화된 설정이 유력한 원인이며,
  교정 전후 검증은 아직 하지 않았다. 프롬프트나 fixture를 완화하지 않는다. 실패 원본을 보존하고 교정 후
  실행에서 **실제 도구 호출과 근거 반환**이 확인돼야 통과로 기록한다. native 성공만으로 통과시키지 않는다.
- Agent 최대 반복은 4다. 이는 전체 TaskFlow의 모델 8회/tool 12회 예산 집행이나 취소 보장이
  아니다. 세 도구에 모델이 없더라도 한 iteration의 여러 tool 호출·retry·내부 모델 호출 수는
  실측해야 한다. 자동 retry/fallback과 conversation memory는 설정하지 않는다.
- domain 도구는 `top_k=1`이며 기대 dataset/document와 전체 고정 내용이 일치하는 항목만
  Agent에 전달한다. 반환 누락은 `not_found`, 출처/내용 불일치는 실행 오류다.
- 참고한 Dify 1.11.1 upstream에서 Workflow Tool은 등록 당시의 workflow version을 저장하고,
  tool update 시 새 버전으로 바뀐다. provider ID만으로 강제 불변 배포가 보장되지는 않는다.
- native Agent의 서버 reasoning/log 보존 정책은 이 코드가 제어하지 않는다. 최종 End에는
  `text`만 연결했지만, 내부 자료 활성화 전에 플랫폼 보존·ACL 검증이 별도로 필요하다.
- 자동 테스트는 고정 fixture·Code 실행·selector/노드 연결을 검증한다. 실 LLM 의미 품질,
  실제 Node 등록, 내부 데이터 적용, 전체 성능 향상을 증명하지 않는다.

[실서버 실행 기록](../../../research/agent-improvement/evaluations/2026-10-17-maxis-native-synthetic-capability.md)에
생성 자산과 두 실패를 구분해 남겼다. 공유 모델 옵션 변경은 13개 LTM 앱에 영향을 줄 수 있어
안전 검토에서 차단되었으며 미실행이다. 해당 범위의 명시적 승인 전에는 재시도하거나 우회하지 않는다.

공식 구현 근거: [Agent schema](https://raw.githubusercontent.com/langgenius/dify/1.11.1/api/core/workflow/nodes/agent/entities.py),
[Agent parameter binding](https://raw.githubusercontent.com/langgenius/dify/1.11.1/api/core/workflow/nodes/agent/agent_node.py),
[Workflow Tool version](https://raw.githubusercontent.com/langgenius/dify/1.11.1/api/core/tools/workflow_as_tool/tool.py).

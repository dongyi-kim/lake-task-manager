# Codex + LTM MCP 작성·스킬 탐색 실측

작성일: 2026-11-06. 실제 Codex CLI와 LTM MCP HTTP 서버를 연결한 탐색 평가다. 운영 Jira/Confluence나 운영 계정으로 쓰지는 않았다. 이 문서는 전체 MCP 품질 인증이나 모델 간 성능 비교 보고서가 아니다.

## 실행 조건과 재현 자료

- Codex CLI `0.153.4`, 기존 ChatGPT 로그인, `gpt-6-astra`, reasoning `ultra`.
- 후보 `bc5181d600897e0a0f67cb70ac86b63a91882e5d` (#167). LTM의 실제 MCP SDK·command·JiraClient·SQLite 캐시·serializer 사용. Jira/Confluence REST만 합성 fixture, `jira820 0.12.0` 기반.
- `tools/codex_mcp_eval`의 기존 CLI/fixture를 재사용. shell·앱·플러그인·웹 검색·호스트 스킬·추가 에이전트는 차단. 평가용 쓰기 도구만 노출. 87개 중 조회/작성 53개를 사용 가능하게 했으며 전체 도구를 검증한 것은 아니다.
- 주입 지연 0ms. 포트 4457을 사용하는 동안 기존 Confluence dev 서버를 중지. 서버 쓰기는 모두 disposable fixture에 한정.
- 각 사례는 별도 ephemeral Codex 실행이다. 서버 상태는 순차 공유하고, 승인 단계는 앞선 최종 답변을 다음 사용자 프롬프트에 전달했다. 네이티브 대화 resume나 UI 대화 지속성을 시험한 것은 아니다.
- 단계별 입력, 모델 최종 답변, 안전하게 정리한 이벤트, MCP 인자/결과, 변경 전후 티켓/문서, upstream 호출은 아래 raw 경로에 보존했다. 비공개 reasoning·인증 토큰은 제외했다.

Raw 위치는 이 worktree의 `.cache/agent-evaluation/` 아래다.

| 실행 | 용도와 판정 |
| --- | --- |
| `20261106-codex-authoring-r01` | 네트워크·CLI preflight 성공. 이후 fixture에 참조 티켓/생성 metadata가 없어 중단. 기능 성공률 계산에서 제외. `fixture-invalid.json` 보존 |
| `20261106-codex-authoring-r02` | 올바른 fixture + Codex 기본 승인 설정. 서버 쓰기는 CLI의 `approval_policy=never`에 차단. 승인 호환성 관찰과 읽기 전용 피드백 자료이며 쓰기 검증 통과로 취급하지 않음 |
| `20261106-codex-authoring-r03` | mock에 노출한 쓰기 도구별 `approval_mode="approve"`를 해당 subprocess에만 지정. 전역 Codex 설정이나 운영 연결 설정은 변경하지 않음. 한국어 가이드 보완 후 후보 실측 |

실행 스크립트 `run_review.py`, `manifest.json`의 runner SHA·후보 commit·도구/스킬 manifest, `source.json`을 함께 보존했다. r03은 r01의 성공한 preflight 메타데이터를 재사용했으며 매 단계의 실제 모델 응답으로 연결 상태를 확인했다. r02/r03은 승인 설정·일부 프롬프트·가이드가 함께 달라 통제된 A/B 비교가 아니다.

## 시나리오별 결과

`ok=true`는 CLI 실행 완료 여부일 뿐 업무 정확성 판정이 아니다. 호출 인자, 최종 저장 내용, 사용자 요청을 직접 대조했다.

| 사례 | 실제 결과 | 판단 |
| --- | --- | --- |
| Task 작성 요청 | DL-365 1건 생성. 독립 Task, 담당자/마감일 없음. DL-1 관련 이슈와 문서 990001 remote link 등록·재조회 | 업무 내용/필드/참조는 일치. 초안 공유 없이 직접 게시함 |
| 앞선 결과 승인·제출 | DL-365·기존 링크 재조회, 새 쓰기 0회 | 중복 생성·중복 연결 없음 |
| 진행 댓글 바로 게시 | 댓글 100001 1건. 1~4일 차이 0건, 5일 원천 120/대상 118/차이 2건, 원인 확인 중. 미완료 체크 항목 2개 | 숫자·불확실성·진행 상태 보존. 댓글 저장 및 조회 확인 |
| 본문 완료 조건 수정 | `update_description` 2회. 첫 요청은 조회한 `descriptionEditHtml`의 input 체크박스를 유지했으나 저장 후 체크박스가 사라짐. 모델이 재조회 후 taskList로 변환해 재저장 | 최종 문구/미완료 상태/참조는 유지. 한 번의 왕복 편집은 실패. 모델의 자가 수정으로 가려서는 안 되는 결함 |
| 독립 Task 2건 일괄 생성 | bulk 사전 검증 후 DL-366·367 생성, 각각 문서 연결. Epic/담당자/마감일 없음 | 2건만 생성, 부분 실패 0건, 후속 상세·문서 링크 조회 확인 |
| Confluence 문서 작성 요청 | DL/990001 바로 아래 900000000028 생성. storage XHTML, 헤딩·표·절차·점검표, 버전 1 | 위치·본문·원문 기준 일치. 실제 집계 결과를 미확인으로 남김. 초안 공유 없이 직접 게시함 |
| Confluence 앞선 결과 승인 | 기존 페이지·부모·버전·본문 조회 | 새 문서 생성 0회 |

초안 공유는 현재 **권장**이다. r02에서도 게시 시도는 있었으나 CLI가 차단했고, r03에서는 실제 게시됐다. 따라서 서버 쓰기 0회만 보고 초안 검토 지침을 준수했다고 판정하면 안 된다. 사용자의 일반 작성 요청 자체는 쓰기 권한일 수 있지만, 이 스킬만으로 제출 전 검토를 항상 보장하지는 못한다. 사용자 지시가 명시적으로 “초안만”인 경우와 일반 작성 요청은 별도 사례로 계속 구분해야 한다. 권장을 서버 승인 장치로 바꾸지는 않았다.

### 한국어 직접 판독

다음은 후보의 실제 생성 문구다.

- Task: `orders_raw와 orders_dw의 일자별 건수 비교`, `차이가 있으면 누락 원인을 기록하고 재검증`.
- 댓글: `11월 5일은 원천 120건, 대상 118건으로 2건 차이가 있으며, 누락 원인은 아직 확인 중입니다.`
- 일괄 Task: `누락 2건의 원인 확인 및 기록`, `조치 후 orders_raw와 orders_dw의 2026-11-05 건수 재검증`.
- 문서: `조회 실패나 미확인 값을 0건으로 기록하지 않는다.` 원문에 날짜 컬럼/시간대가 없다는 점도 구분했다.

이 소규모 표본에서는 조사 누락이나 이해하기 어려운 명사 나열이 두드러지지 않았다. Task는 개조식, 댓글과 문서 설명은 문장을 선택했다. 다만 문서는 짧은 기준 문서를 여러 표·절차로 확장해 길어졌다. 요청된 운영 절차 범위에는 맞지만 모든 문서에 이 분량이 적절한 것은 아니다. 사용자 선호 문체를 무시하는 고정 양식은 피해야 한다.

한국어 가이드에는 조사·연결어를 보존하고, 원인/결과를 명사열로 뭉개지 않으며, 완료·진행 중·예정을 구분하도록 보완했다. 같은 질문 반복 표본이나 대조군이 없어 가이드 때문에 품질이 향상됐다는 정량 인과 주장은 하지 않는다.

### 시간·토큰

아래 시간은 CLI 시작부터 최종 답변까지다. 모델 추론·도구 사용·가이드 탐색을 포함하며 Jira 응답 지연이나 앱 UI 로딩 시간으로 해석하면 안 된다. 토큰은 CLI가 집계한 여러 모델 호출의 합계다. 단일 컨텍스트 길이·과금액·TTFT가 아니다. cached input은 input에 포함된다.

| 사례 | 시간 | 서버 도구 호출 | input | cached input | output |
| --- | ---: | ---: | ---: | ---: | ---: |
| Task 작성 | 81.4s | 17 | 390,954 | 349,824 | 1,937 |
| Task 승인·중복 확인 | 46.1s | 5 | 127,188 | 97,920 | 1,031 |
| 댓글 게시 | 50.6s | 7 | 195,343 | 164,608 | 1,002 |
| 본문 수정 | 92.5s | 9 | 315,583 | 279,680 | 2,149 |
| 2건 일괄 생성 | 90.1s | 21 | 445,539 | 405,248 | 2,567 |
| Confluence 작성 | 131.8s | 9 | 307,558 | 274,304 | 4,155 |
| Confluence 중복 확인 | 41.9s | 5 | 169,279 | 144,640 | 932 |

각 사례 n=1이므로 p50/p95나 일반적인 성공률은 산출하지 않았다. 모델 호출 수·USD·TTFT는 해당 CLI 결과에 없어 미측정이다.

## 실제 Codex 피드백을 재검증한 결과

r02의 읽기 전용 피드백은 295.7초, 도구 20회였다. `08-feedback.json`에 답변 전문이 있다. 다음은 모델의 평가와 저장된 wire catalog/코드를 대조한 결과다.

r03에서도 보완한 가이드를 다시 읽고 286.6초, 도구 21회의 피드백을 받았다. 쓰기 0회였으며 빈 목록의 bulk_validate 호출은 업무 검증 실패(`data.ok=false`)를 반환했다. 이 실행이 정상 종료됐다는 사실을 bulk 생성 성공으로 세지 않았다. 추가 피드백인 스킬 첫머리의 문체 요약과 생성 순서 혼동은 후속 문서 수정에 반영했다. r03 이후 바꾼 문서의 해당 부분은 정적 검증만 했으며, 그 수정의 모델 행동 개선을 실측했다고 주장하지 않는다.

| 모델 피드백 | 직접 확인·정정 |
| --- | --- |
| 가이드의 authenticate/첨부/transition 도구가 없음 | 평가 fixture가 일부 쓰기를 제외했기 때문. 실제 서버에 기능이 없다는 결론은 틀림. 선택형 toolset을 제품화한다면 현재 제공 범위를 안내해야 한다는 제안은 유효 |
| bulk items와 list_options/output 오류 구조가 모호함 | wire에는 bulk 항목 속성 및 outputSchema가 존재. 출력 스키마가 없다는 뜻은 아님. Codex가 보여주는 도구 투영과 실제 MCP schema가 다르므로 호스트별 검증 필요 |
| 53개 도구마다 같은 2,169자 안내가 붙음 | 모델이 본 표면에 대한 주장. 서버 wire의 개별 description은 get_ticket 536자, bulk_create 757자 등으로 이보다 짧음. LTM이 동일 안내를 53번 직렬화했다는 주장은 성립하지 않음. host가 server instructions를 어떻게 투영하는지 추가 확인 필요 |
| Task 문체가 명사형으로 지나치게 기울 수 있음 | 기존 가이드의 표현을 확인했고 이번에 수정. 댓글/Confluence까지 개조식을 강제하지 않음 |
| 공통 reference의 digest가 중복됨 | 실제로 같은 shared 파일을 여러 독립 스킬 manifest에 투영. 유지보수 원본은 한 곳이며 복제 구현은 아님. 같은 세션에서 digest가 같으면 재독하지 않는 방식은 호스트 최적화 후보 |
| 문서 분할 조회의 version 안내가 일부 reference에 빠짐 | people-and-links에 첫 응답의 version.number를 후속 version으로 전달하도록 보완. r03 실행 이후의 문서 수정으로, 해당 문장의 실제 모델 재평가는 하지 않음 |

## MCP·스킬 구조 검토

전체 카탈로그 측정은 원래 후보 `3769ed7b`에서 별도 수행했다. compact JSON 전체 685,734 bytes 중 description 48,531, inputSchema 58,214, outputSchema 556,704 bytes다. 87개 도구 모두의 schema를 지운 것이 아니라 실제 서버가 반환한 카탈로그를 측정했다. outputSchema가 약 81.2%이며 가장 큰 개별 도구는 get_dashboard 약 18KB다. raw `r02/full-catalog.json`, `catalog-metrics.json`에 남겼다.

4개 스킬, 25개 리소스의 총 제공량은 120,020 bytes, 동일 내용을 digest로 제외하면 55,663 bytes다. 모든 리소스를 매 요청 읽은 것은 아니다. 실제 Codex는 `get_content_guide`로 필요한 가이드를 탐색했다. 이 결과만으로 호스트의 native `skills/list` 활성화까지 검증했다고 주장하지 않는다.

관련 공개 구현과 비교했다.

- [MCP Skills extension](https://skills.extensions.modelcontextprotocol.io/specification/beta/skills)은 스킬 manifest와 digest, resources/read를 이용한다. 현재 LTM의 카탈로그와 reference 분리는 이 방향에 맞는다. 호스트 미지원 때 쓰는 get_content_guide도 이번 실제 평가에서 필요했다. 네이티브 로딩이 확인되기 전에 fallback을 제거할 이유가 없다.
- [GitHub 공식 MCP tool configuration](https://github.com/github/github-mcp-server#tool-configuration)처럼 기능별 toolset과 개별 도구 allowlist를 제공하는 방안이 적합하다. LTM의 인증·cache·mutation 계층을 다른 Jira MCP로 교체하는 방안보다 현재 공통 경로를 유지하면서 노출량을 줄이는 편이 맞다.
- [Atlassian MCP 도구 구성](https://github.com/sooperset/mcp-atlassian/blob/main/docs/tools-reference.mdx)도 기능별 도구 묶음을 제공한다. LTM은 자체 WBS/people/PMO와 사내 SSO가 있으므로 이 구현을 그대로 대체재로 보는 것은 부적절하다.
- [Anthropic 도구 설계 지침](https://www.anthropic.com/research/writing-tools-for-agents)은 실제 에이전트 평가와 응답 정보량 조절을 제안한다. 단순 링크 확인에 하위 티켓·전체 댓글까지 조회하기보다 필요한 관계만 읽는 선택지를 추가하는 쪽이 이번 호출 기록과 맞는다.
- [Codex MCP 설정](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)은 도구별 승인 설정을 지원한다. 비대화형 연결에서는 사용자 승인 의사와 host 실행 정책을 별도로 확인해야 한다. 제품의 쓰기 annotation도 생성·추가와 삭제·덮어쓰기를 구분할 수 있지만, annotation 하나만 바꾸면 모든 host의 승인이 해결된다고 단정할 수 없다.

### 개선 우선순위

1. **본문 왕복 편집의 체크박스 보존**: `descriptionEditHtml`을 되보내면 첫 저장에서 input 체크박스가 유실되는 경로를 우선 수정. 모델에 매번 변환을 맡기기보다 read/write의 canonical 편집 형식 계약을 일치시켜야 한다. 체크 상태·ID·인라인 서식 보존까지 회귀로 묶을 것.
2. **작업별 도구 묶음과 간결한 응답**: Jira 기본 작성 / 업무 집계 / Confluence / 설정·관리 프로필을 선택 가능하게 하고 기존 전체 도구 모드는 유지. outputSchema는 제거하지 말고 각 profile 및 concise/detail 출력에 맞는 정확한 schema 유지. 동일 업무 프롬프트로 성공률·도구 호출·누적 input을 다시 비교할 것.
3. **도구 annotation 정밀화**: 현재 create_task/add_comment/update_description/delete_ticket 모두 destructiveHint=true. 생성·추가와 삭제·덮어쓰기를 구분할 metadata registry 검토. idempotentHint는 mutation receipt의 실제 보장 범위를 확인한 도구에만 적용. 승인 정책을 서버가 우회하지 않음.
4. **가이드 탐색 비용**: 작은 작업에서 필요한 최소 reference를 명시하고, server instructions의 공통 설명을 짧게 유지. 동일 digest의 재독 회피는 host별로 검증. 네 개 스킬을 한 거대 스킬로 합치거나 범용 단일 도구로 축소하지 않음.
5. **평가 정식화**: 이번 임시 runner의 approval/fixture 준비를 검증한 뒤 기존 평가 도구의 writing profile로 편입하는 별도 작업이 적절. fixture 불량·host 승인 차단·모델의 작업 실패를 분리하고, “초안만”·“검토 권장”·“바로 제출” 세 유형을 독립 측정할 것.

이번 PR에 적용한 것은 초안/한국어/참조 가이드 보완과 실제 평가 보고서다. toolset·schema·annotation·serializer의 구조 변경은 아직 적용하지 않았다.

## 검증 범위와 남은 확인

- 스킬·guide·authoring example 회귀: 17 passed, 1 skipped. skip은 Windows symlink 권한 검사로 기존 환경 제약이다.
- 실제 모델의 Task/댓글/bulk/Confluence 작성과 재조회, 중복 생성 회피를 확인. 기존 문서 수정·공동 편집 충돌·이미지/첨부·사람 멘션·UI 렌더링 전체 회귀는 이번 표본에 포함하지 않았다.
- 모델 피드백은 제품 사실과 구분했다. outputSchema가 없다는 식의 잘못된 결론이나 mock에서 성공했으므로 운영 호환이라는 결론은 채택하지 않았다.
- 별개 Task → SubTask 오류는 #168에서 안전한 폼 실패 진단을 보강했다. local mock의 native 화면은 404였지만 사용자 운영 오류와 같은 원인이라는 증거는 없다.

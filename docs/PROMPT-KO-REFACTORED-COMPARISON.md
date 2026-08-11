# Agent prompt 언어·구조 비교 보고서

> 평가 대상: BASE / KO / EN / GUIDE / KO-R(`ko-refactored-v1`)
> 실행일: 2026-08-11
> 실행 환경: main/complex=`gpt-4o`, simple=`gpt-4o-mini`, mock Jira/Confluence
> 평가 자료: 실제 API 응답 전문, 구조화 payload, latency, token, call, cache token, 추정 cost

## 0. 먼저 바로잡는 사항

초기 보고서의 BASE·KO·EN·GUIDE 결과는 모두 `gpt-4o-mini`로 실행되어 있었다. 프로젝트의 실제
운영 구조는 처음부터 main/complex=`gpt-4o`, simple=`gpt-4o-mini`였으므로, 그 all-mini 결과는
사용자가 요청한 prompt 비교의 기준으로 쓸 수 없다.

원인은 평가 하네스가 다음처럼 simple model을 main model로 덮어썼기 때문이다.

```python
os.environ.setdefault("LAKE_AGENT_OPENAI_CHAT_SIMPLE", MODEL)  # 잘못된 과거 기본값
```

이는 프로젝트 설정의 문제가 아니라 실험 설계 오류다. 공정한 prompt 비교라면 **모델 routing은
고정하고 prompt만 바꿔야** 한다. 따라서 본 보고서는 all-mini 수치와 결론을 전부 제외하고, 다섯
후보를 같은 production 혼합 routing으로 새로 실행한 결과만 사용한다. 하네스 기본값도
`gpt-4o-mini`로 고정했으며, 세 평가 하네스가 main model을 simple tier에 복사하지 못하도록 회귀
test를 추가했다.

## 1. 결론

이번 1회 혼합 실행에서는 **BASE가 가장 안정적**이었다.

- 자동 계약 점수: BASE **27/32**, GUIDE 24/32, KO 23/32, EN·KO-R 22/32.
- 사람 품질 점수: BASE **4.1/5**, GUIDE 3.9, KO 3.8, EN 3.7, KO-R 3.5.
- GUIDE는 대화 battery가 가장 빠르고 사람 조회·내 업무 답변이 가장 풍부했다.
- KO-R은 대화 token과 Create 비용이 가장 낮았지만, `DL-9090` 진척 질문을 아예 거절하고,
  단건 요청을 과분해하거나 실제 payload를 비우는 회귀가 있었다.
- 따라서 한국어 원문형 구조와 `AGENT.md`의 작성 표준은 유지할 가치가 있지만,
  **`ko-refactored-v1`을 그대로 production prompt로 승격할 근거는 없다.** BASE 동작을 보존하는
  v2 보강과 반복 실행이 먼저다.

이 순위는 prompt 언어의 보편적 우열이 아니라 이 프로젝트·mock world·현재 model 조합에서의
결과다. 특히 한 번의 API run은 provider 지연과 비결정성의 영향을 받으므로 production 채택 판단은
run order를 바꾼 최소 5회 반복 후 내려야 한다.

## 2. 비교군과 통제 조건

| 이름 | prompt 구성 | source | main/complex | simple |
|---|---|---|---|---|
| BASE | 기존 한영 혼합 | commit `3f388b6` | `gpt-4o` | `gpt-4o-mini` |
| KO | 기존 prompt 전부 한국어화 | commit `901b115` | `gpt-4o` | `gpt-4o-mini` |
| EN | 기존 prompt 전부 영어화, 사용자 입출력 한국어 | commit `287a056` | `gpt-4o` | `gpt-4o-mini` |
| GUIDE | 기존 ChatGPT 권고 언어 배분 | commit `e923850` | `gpt-4o` | `gpt-4o-mini` |
| KO-R | 의미를 재설계한 한국어 원문형 | working tree, `ko-refactored-v1` | `gpt-4o` | `gpt-4o-mini` |

모든 후보는 같은 질문, 같은 mock 데이터, 같은 하네스와 자동 checker를 사용했다. 실제 Jira 생성·수정은
수행하지 않았고, 승인 전 draft까지만 평가했다. JSON에는 `model`, `simpleModel`, `promptVersion`을
기록했다.

## 3. KO-R에서 바꾼 것

KO-R은 기존 영어 prompt를 문장별로 번역하지 않았다.

- 공통 계약 → role 계약 → route별 dynamic task → 읽기 전용 data의 4계층으로 책임을 분리했다.
- 10개 role을 목적, 입력 자료, 판단 절차, 출력 계약, 금지·중단 조건 순으로 다시 썼다.
- `function`, tool name, parameter, JSON schema/enum, Jira field·issue type, JQL/SQL/code, ticket key,
  user id는 번역하지 않았다.
- `PROMPT_VERSION="ko-refactored-v1"`과 prompt integrity test를 추가했다.
- 실제 reply뿐 아니라 card/body/question form, raw usage, checkpoint를 평가 자료로 저장했다.

작성 표준은 [AGENT.md](AGENT.md)에 문서화했다. 근거로 사용한 자료는 OpenAI의
[Prompting best practices](https://developers.openai.com/api/docs/guides/latest-model#prompting-best-practices),
[How and Where to Translate?](https://arxiv.org/abs/2507.22923),
[OLA](https://arxiv.org/abs/2601.03589),
[카카오 AI 가드레일](https://tech.kakao.com/posts/741),
[우아한형제들 LLMOps](https://techblog.woowahan.com/22839/),
[KMMLU](https://arxiv.org/abs/2402.11548)다. 외부 자료는 구조 원칙을 정하는 데 사용했고, 실제 채택
판단은 아래 프로젝트 battery 결과로 내렸다.

## 4. 전체 정량 결과

### 4.1 대화 battery

| 후보 | 시간 | LLM calls | prompt tokens | completion tokens | total tokens | cached tokens |
|---|---:|---:|---:|---:|---:|---:|
| BASE | 141.5초 | 35 | 245,357 | 8,529 | 253,886 | 124,032 |
| KO | 125.0초 | 35 | 267,649 | 8,087 | 275,736 | 144,384 |
| EN | 112.7초 | 35 | 242,138 | 7,619 | 249,757 | 139,264 |
| GUIDE | **98.2초** | 35 | 238,391 | 7,906 | 246,297 | **192,256** |
| KO-R | 133.6초 | 36 | **225,559** | 8,144 | **233,703** | 103,168 |

KO-R은 BASE보다 total token을 7.9% 줄였지만 5.6% 느렸다. GUIDE는 BASE보다 token이 3.0% 적고
30.6% 빨랐다. 단, latency는 한 번의 외부 API wall-clock 값이므로 언어 효과로 단정할 수 없다.

### 4.2 Compose·Create와 자동 계약 점수

| 후보 | Compose | Compose 시간 | Create | Create 시간 | Create calls | Create tokens | Create 추정 cost | 합계 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BASE | 8/9 | 25.8초 | **19/23** | 629.4초 | 195 | 1,636,271 | $4.031205 | **27/32** |
| KO | 8/9 | 28.7초 | 15/23 | 698.0초 | 201 | 1,872,456 | $4.561754 | 23/32 |
| EN | 8/9 | 24.0초 | 14/23 | **528.7초** | **192** | 1,611,986 | $3.900860 | 22/32 |
| GUIDE | 8/9 | **21.3초** | 16/23 | 552.2초 | 196 | 1,570,221 | $3.867174 | 24/32 |
| KO-R | 7/9 | 22.3초 | 15/23 | 542.3초 | 199 | **1,495,492** | **$3.453278** | 22/32 |

KO-R의 Create token은 BASE보다 8.6%, 추정 cost는 14.3% 낮다. 그러나 자동 계약 점수는 5점
낮고 사람 품질도 낮아, 비용 절감만으로 채택할 수 없다. 더구나 자동 checker에는 false positive가
있다. 예를 들어 `SUB1`에서 실제 `pending.items=[]`인데 화면에 초안 표가 있다는 이유로 일부 후보가
통과했다. 이하 사람 평가는 실제 payload와 답변 의미를 함께 본다.

### 4.3 대화 case별 시간·token

| case | BASE | KO | EN | GUIDE | KO-R |
|---|---:|---:|---:|---:|---:|
| S1 생성 | 60.1초 / 97,611 | 47.4 / 103,683 | 41.1 / 94,223 | **35.4 / 92,449** | 69.8 / **85,459** |
| S2 버그 | 34.5 / 64,972 | 28.6 / 70,797 | 28.2 / 64,500 | 24.8 / 62,988 | **20.4 / 58,145** |
| S3 이력 | 19.7 / 29,442 | 20.8 / 31,929 | 18.0 / 29,037 | **14.5 / 29,125** | 16.1 / **26,855** |
| S4 사람 | **8.9 / 29,577** | 10.4 / 33,177 | 9.6 / 29,618 | 9.7 / 29,155 | 14.4 / **26,493** |
| S5 내 일 | 8.6 / 15,487 | 7.2 / 17,409 | 8.0 / 15,773 | 7.8 / 15,921 | **6.3 / 13,596** |
| S6 진척 | 9.7 / 16,797 | 10.6 / 18,741 | 7.8 / 16,606 | **6.0 / 16,659** | 6.6 / 23,155 |

## 5. 대화 battery: 실제 답변과 사람 평가

인용은 실제 raw reply에서 차이를 드러내는 부분만 남겼다. 카드 내용도 의미 판단에 포함했다.

### S1. 신규 기능 생성

입력은 Iceberg Puffin NDV 통계 생성 기능을 PoC로 만들고 단계별 Sub-Task로 나누라는 요청이다.

> **BASE** — `Task [ETL] Iceberg Puffin NDV 통계 생성 기능 추가`, 상위 `DL-101`; 설계·구현·검증
> 3개. “아래 카드에서 내용을 확인하고 승인해 주세요.”
> **KO** — 같은 3단계와 `DL-101`; 본문에는 범위가 “Iceberg 배치적재 테이블”에서 중간 종료되지만
> card body에는 범위·DoD가 있다.
> **EN** — 같은 3단계이나 “몇 가지 질문이 필요합니다”라고 쓴 뒤 질문 없이 승인 card를 냈다.
> **GUIDE** — 같은 3단계이나 “범위, 완료 조건, 기한, 모듈에 대한 추가 정보가 필요”하다고 하면서
> 질문 없이 승인 card를 냈다.
> **KO-R** — `DL-102 [ETL] 쿼리 성능 개선` 아래 설계·구현·테스트 3개. 구현 담당을
> `skcc.x1042`, 테스트를 진행 중 8건인 `skcc.i2011`로 제안했다.

사람 판단: **BASE ≈ KO ≈ KO-R > EN ≈ GUIDE**. KO-R의 `DL-102`는 통계가 쿼리 성능에 기여한다는
점에서 합리적이지만, 과부하라고 적은 후보를 그대로 테스트 담당자로 둔 점은 감점이다. EN과 GUIDE는
“질문 필요”와 “질문 없음/승인 요청”이 모순된다.

### S2. 재현 가능한 리니지 버그

> **BASE** — `Bug [Observability] ...`, 담당 `skcc.x1560`.
> **KO** — `Bug [Workbench] ...`, 담당 `skcc.x1402`; 그러나 “DL-9092에서 이미 해결됨.
> 추가적인 버그 티켓 생성은 필요하지 않음”이라고 하면서 Bug card를 함께 냈다.
> **EN** — “새로운 Bug 티켓을 만들지 않습니다”라고 했지만 실제 card에는 Bug 초안이 있다.
> **GUIDE** — `Bug [Observability] ...`, 담당 `skcc.x1560`.
> **KO-R** — `Bug [Workbench] ...`; 크롬 재현 경로, 기대/실제 동작, `DL-9090`, `DL-9092`, 설계
> 문서와 담당 `skcc.x1402`를 보존했다.

사람 판단: **KO-R > BASE ≈ GUIDE > KO > EN**. 문제 화면이 Workbench이므로 KO-R의 module과
담당 근거가 가장 정확하다. KO·EN은 사용자에게 보이는 결론과 실제 card가 충돌한다.

### S3. 데이터 이력

다섯 후보 모두 현재 DAG `dag_fdc_trace_summary_ic`, 30분 주기, 8개 컬럼과 `CHAMBER_ID`, 진행 중
Task 2개, 8건 연표를 맞게 정리했다.

> **BASE/KO/EN/GUIDE** — 핵심 참조 3건(`DL-9042`, `DL-9044`, `DL-9045`)을 별도 표기.
> **KO-R** — 연표 각 행까지 `[4]`~`[9]`로 연결해 9개 참조를 제시.

사람 판단: **KO-R ≥ BASE ≈ KO ≈ EN ≈ GUIDE**. KO-R의 traceability가 조금 낫지만 `DL-9042`를
두 번호로 중복 표기했다. 의미 품질 차이는 작고 GUIDE가 가장 빨랐다.

### S4. 사람의 현재 업무

> **BASE/EN** — “미완료 티켓은 총 21건”; `DL-5511` P1, `DL-5514`, `DL-5019`를 제시.
> **KO** — 총 21건이라고 했지만 `DL-5514`, `DL-5035` 두 건만 제시.
> **GUIDE** — 총 21건과 주요 5건을 제시하고, 마감이 지난 P1 `DL-5511`에 우선 집중하라고 권고.
> **KO-R** — `DL-5019`, `DL-5514`, `DL-5035` 세 건만 제시하고 총량·우선순위를 생략.

사람 판단: **GUIDE > BASE ≈ EN > KO-R > KO**. GUIDE가 질문의 “지금 맡고 있는 일”을 가장
실무적으로 요약했다.

### S5. 지금 시작할 내 업무

> **BASE** — 마감이 지난 P1 `DL-9028`을 첫째, 오늘 마감 `DL-9029`를 둘째로 제시.
> **KO** — 더 오래 지난 UI 티켓 `DL-9008`을 먼저 놓고 `DL-9028`, `DL-9029`를 뒤에 둠.
> **EN** — BASE의 두 건에 내일 마감 `DL-9026`을 추가.
> **GUIDE** — `DL-9028` → `DL-9029` → `DL-9026`, 그리고 팀 장기 정체 `DL-5449`까지 구분.
> **KO-R** — “오늘은 `DL-9008`에 집중”하고 그다음 `DL-9028`, `DL-9029`라고 답함.

사람 판단: **GUIDE ≈ BASE ≈ EN > KO > KO-R**. “지금 무엇부터”에는 오래된 날짜보다
P1-Critical과 오늘 마감의 결합이 더 강한 우선순위 근거다.

### S6. DL-9090 진척

> **BASE** — “하위 작업 3개 중 2개 완료”, 완료 key 2개, 진행 중 `DL-9095`, 남은 성능 측정과
> 문서 정리, 관련 문서를 모두 제시.
> **KO/EN** — 같은 핵심 답을 제공했으나 문서 참조를 자동 grounding warning으로 덧붙임.
> **GUIDE** — 2/3 완료, `DL-9095`, 성능 측정·가이드를 짧고 정확하게 제시.
> **KO-R** — “DL-9090은 WBS에 연결된 Epic이 아니어서 진척률을 집계할 수 없습니다.”

사람 판단: **BASE ≈ GUIDE > KO ≈ EN >>> KO-R**. 사용자는 dashboard rollup이 아니라 특정
티켓의 진행 상황을 물었다. KO-R은 이미 가진 하위 티켓·코멘트·문서 근거를 사용하지 않고 질문을
잘못된 WBS 규칙으로 차단했다. KO-R의 가장 큰 회귀다.

## 6. Compose battery

### 6.1 자동 결과

| ID | BASE | KO | EN | GUIDE | KO-R |
|---|---:|---:|---:|---:|---:|
| CMP1 | ✓ 4.1s | ✓ 6.6 | ✓ 4.3 | ✓ 3.7 | ✓ 4.9 |
| CMP2 | ✓ 4.2 | ✓ 4.8 | ✓ 3.7 | ✓ 3.0 | ✓ 4.3 |
| CMP3 | ✓ 2.0 | ✓ 2.8 | ✓ 2.5 | ✓ 1.7 | ✓ 2.2 |
| CMP4 | ✓ 0.0 | ✓ 0.0 | ✓ 0.0 | ✓ 0.0 | ✓ 0.0 |
| CMP5 | ✓ 2.4 | ✓ 3.3 | ✓ 3.1 | ✓ 2.0 | **✗ 1.4** |
| CMP6 | ✗ 1.3 | ✗ 1.3 | ✗ 1.1 | ✗ 1.0 | ✗ 1.1 |
| CMP7 | ✓ 1.6 | ✓ 1.2 | ✓ 1.3 | ✓ 1.1 | ✓ 0.9 |
| CMP8 | ✓ 5.7 | ✓ 4.4 | ✓ 4.2 | ✓ 5.3 | ✓ 3.6 |
| CMP9 | ✓ 4.5 | ✓ 4.3 | ✓ 3.8 | ✓ 3.5 | ✓ 3.9 |

CMP4와 CMP7은 다섯 후보의 반환문이 동일하므로 전문 비교를 생략한다. 각각 “목적과 대상을 한 줄
적어 달라”, “어떤 작업에 대한 코멘트인지 적어 달라”는 안전한 `needsInfo` 응답이다.

### 6.2 차이가 난 case의 실제 답변과 평가

**CMP1 — 진행 공유 코멘트.** BASE/EN은 “그래프 렌더·업스트림 완료, 다운스트림 완료,
남은 성능 측정·문서 정리”, KO는 `DL-9093/9094/9095/9092`를 직접 연결했다. GUIDE는 이를 한
문단으로 압축했고 KO-R은 “문서 정리, 사용 가이드 작성”까지 남은 일로 썼다. 모두 사용할 수 있으나
근거 추적은 KO, 간결성은 GUIDE, 상태/남은 일 균형은 BASE가 좋다.

**CMP2 — Task body.** BASE는 “`DL-9071`의 대량 실패 문제를 해결하기 위해 필요”라고 가장 구체적으로
썼다. KO/GUIDE는 “데이터 흐름을 효율적으로 관리”, EN은 “사용자 도구 기능 확장”, KO-R은 “더 나은
데이터 접근성”이라고 일반화했다. 모두 형식은 통과하지만, 후자의 동기는 입력 근거보다 강한 추정이다.
**BASE가 가장 낫되 DL-9071 인과가 실제 자료와 일치하는지 검토가 필요**하다.

**CMP3 — p95 코멘트.** BASE와 KO는 원문 “p95가 생각보다 높다”를 그대로 보존했다. EN은 “p95
성능이 낮게 나왔다”로 지표와 품질의 방향을 바꿔 읽힐 수 있고, GUIDE는 근거 없이 “추가 로그를
수집하고 있다”고 추가했다. KO-R은 “기대했던 것보다 높게 나왔다”라고 썼으나 무엇이 높은지 생략했다.
**BASE ≈ KO > KO-R > EN > GUIDE**다.

**CMP5 — 현재 상태 공유.** BASE/KO/EN/GUIDE는 2/3 완료, `DL-9095` 진행 중, 남은 성능 측정·문서
정리를 바로 작성했다. KO-R은 “어떤 구체적인 진행 상황이나 변경 사항인지 알려 달라”며 거절했다.
티켓 context에 답이 이미 있으므로 **KO-R의 명백한 회귀**다.

**CMP6 — 담당자 멘션.** 다섯 후보 모두 실패했다. BASE/KO/EN은 담당자 이름 또는 사번을 되물었고,
GUIDE는 작업 자체를 다시 물었다. KO-R은 “현재 티켓의 담당자는 `[~skcc.x1402]`”라고 스스로
알면서도 담당자를 다시 알려 달라고 했다. KO-R이 가장 모순적이다.

**CMP8 — DL-9090 body.** 모두 2홉 조회, 3홉 제외, 성능 측정·가이드 DoD를 갖춘 usable body를
생성했다. 차이는 문장 스타일과 `DL-9040`을 Epic/initiative로 부르는 정도이며 의미 품질은 사실상
동률이다.

**CMP9 — 다운스트림 body.** 모두 형식과 핵심 범위를 충족했다. 다만 “사용자 경험 개선”, “효율성”,
“문제 사전 식별”처럼 자료에 없는 일반 동기가 후보마다 추가됐다. **BASE ≈ KO ≈ EN ≈ GUIDE ≈
KO-R**, 공통 개선점은 동기 문장을 근거에 더 엄격히 묶는 것이다.

Compose 사람 판단은 **BASE ≈ KO ≈ EN ≈ GUIDE > KO-R**이다. KO-R은 CMP5의 실행 가능성 회귀가
명확하고 CMP6에서는 known assignee를 알면서 재질문했다.

## 7. Create battery

### 7.1 자동 결과

| ID | BASE | KO | EN | GUIDE | KO-R |
|---|---:|---:|---:|---:|---:|
| ONE1 | ✓ 24.0s | ✗ 26.0 | ✗ 19.7 | ✓ 19.6 | ✗ 19.8 |
| ONE2 | ✓ 16.4 | ✓ 24.9 | ✓ 28.3 | ✓ 23.1 | ✓ 22.0 |
| STR1 | ✓ 34.6 | ✗ 21.5 | ✗ 21.8 | ✗ 20.3 | ✗ 20.7 |
| STR2 | ✗ 59.3 | ✗ 64.9 | ✗ 39.0 | ✗ 52.7 | ✗ 57.8 |
| STR3 | ✓ 29.7 | ✓ 42.8 | ✓ 27.3 | ✓ 29.3 | ✓ 24.5 |
| PAR1 | ✓ 29.9 | ✓ 30.9 | ✓ 28.7 | ✓ 35.0 | ✗ 14.5 |
| PAR2 | ✓ 29.9 | ✓ 30.1 | ✗ 19.2 | ✓ 25.3 | ✓ 24.3 |
| SUB1 | ✓ 44.9 | ✓ 54.6 | ✗ 27.2 | ✓ 40.9 | ✗ 24.6 |
| SUB2 | ✓ 29.7 | ✓ 28.4 | ✓ 26.3 | ✓ 26.6 | ✗ 14.3 |
| SUB3 | ✓ 43.2 | ✓ 37.1 | ✓ 30.6 | ✓ 28.5 | ✓ 28.8 |
| PASTE1 | ✓ 23.2 | ✓ 23.0 | ✗ 18.2 | ✗ 23.4 | ✓ 16.7 |
| PASTE2 | ✗ 29.7 | ✗ 36.3 | ✗ 34.2 | ✗ 23.4 | ✓ 23.3 |
| ASK1 | ✓ 9.3 | ✓ 13.6 | ✓ 10.2 | ✓ 7.6 | ✓ 17.1 |
| ASK2 | ✓ 38.4 | ✓ 30.2 | ✓ 36.3 | ✗ 29.2 | ✗ 30.5 |
| DUP1 | ✓ 7.9 | ✓ 9.5 | ✓ 11.7 | ✓ 7.4 | ✓ 19.5 |
| ATTR1 | ✓ 21.3 | ✓ 30.0 | ✓ 20.1 | ✗ 21.3 | ✓ 30.6 |
| ATTR2 | ✓ 29.1 | ✗ 18.7 | ✓ 21.4 | ✓ 22.3 | ✓ 23.5 |
| STARR1 | ✓ 31.2 | ✗ 35.9 | ✓ 29.0 | ✓ 27.6 | ✓ 24.7 |
| BUG1 | ✗ 23.3 | ✗ 19.4 | ✗ 20.5 | ✗ 17.9 | ✓ 18.5 |
| BUG2 | ✓ 30.4 | ✓ 37.7 | ✓ 20.8 | ✓ 31.8 | ✓ 18.1 |
| BUG3 | ✗ 19.1 | ✗ 26.1 | ✗ 17.5 | ✓ 15.6 | ✓ 18.1 |
| RULE1 | ✓ 6.8 | ✓ 16.4 | ✓ 5.8 | ✓ 7.7 | ✓ 11.8 |
| RULE2 | ✓ 18.1 | ✓ 40.0 | ✓ 14.9 | ✓ 15.7 | ✗ 38.6 |

### 7.2 모든 case의 실제 출력 차이와 사람 평가

아래 `item`은 화면 문구가 아니라 실제 승인 payload의 `pending.items`/`pending.children`이다.

**ONE1 — “알아서 단축키 도움말 팝업”.** BASE와 GUIDE는 각각 완성된 Task 1건, 질문 0개를 냈다.
KO·EN·KO-R은 “설계·구현·테스트…” 구조를 보여준 뒤 `items=0`, 질문 1개를 반환했다. 위임을 받은
단건 요청에 불필요한 구조 확인을 되물었으므로 **BASE ≈ GUIDE > 나머지**다.

**ONE2 — ‘내 모듈만’ 체크박스.** 모두 Task 1건을 냈다. BASE/KO/GUIDE/KO-R은 `DL-104`, EN만
`DL-9040`에 연결했다. `DL-104 [Catalog] 메타데이터 표준화`가 안정적인 상위이므로 EN을 감점한다.

**STR1 — 30개를 사람별로 분할.** BASE만 parent Task 1건과 child 3건을 만들고 담당을
`skcc.i2044`, `skcc.x1210`에 분산했다. 나머지는 “나눠 진행”이라고 말하면서 단일 Task 1건만 냈다.
**BASE의 단독 승리**다. 다만 BASE도 각 child가 담당할 테이블 범위를 명시하면 더 좋다.

**STR2 — 성능 측정·인덱스·가이드 복합 작업.** BASE/KO는 Task 3건, EN은 `items=0`과 구조 질문,
GUIDE/KO-R은 Task 4건을 냈다. GUIDE/KO-R은 “사용 가이드”를 중복 생성했고 일부 body section이
비었다. 다섯 후보 모두 사용자의 세 산출물을 일관된 구조·본문으로 만들지 못해 **전부 실패**다.

**STR3 — 새 Epic 요청과 기존 DL-102 중복.** KO/EN은 중복 가능성을 확인하기 위해 질문했고,
BASE/GUIDE/KO-R은 새 Epic 대신 기존 `DL-102` 아래 Task를 제안했다. 무조건 중복 Epic을 만들지 않은
점은 모두 안전하다. 사용자 위임에 대한 실행성은 BASE/GUIDE/KO-R, 중복 확인의 명시성은 KO/EN이
낫다.

**PAR1 — DL-9090 아래 Sub-Task 3개.** BASE/KO/EN/GUIDE는 실제 Sub-Task 3건을 `DL-9090`에
연결했다. KO-R은 “세 개의 Sub-Task 초안을 준비했다”고 답했지만 `items=0`이었다. **KO-R은 답변과
payload가 불일치**한다.

**PAR2 — DL-101 아래 CDC 개선 Task.** BASE/KO/GUIDE/KO-R은 `Task [ETL] CDC 재처리 배치 개선`,
`epic=DL-101`을 냈다. EN은 “생성할 예정”이라고 답했지만 `items=0`이다. EN만 실행 불가능하다.

**SUB1 — DL-9095를 단계별 Sub-Task로.** BASE/KO/GUIDE는 설계·구현·검증 표를, KO-R은 요구사항·
인터페이스·테스트 초안을 화면에 썼지만 **다섯 후보 모두 실제 `items=0`**이다. EN은 “현재 유형은
Sub-Task를 가질 수 없다”고만 답했고 역시 질문이 없다. 자동 checker가 BASE/KO/GUIDE를 통과시킨
것은 false positive다. 사람 기준으로는 모두 사용자가 다음 행동을 할 수 없어 실패이며, 차라리 제약을
명시한 EN이 가장 정직하다.

**SUB2 — DL-9090의 남은 일 두 개.** BASE/KO/EN/GUIDE는 성능 측정과 사용 가이드 Sub-Task 2건을
실제로 만들었다. KO-R은 두 개가 필요하다고 설명했지만 `items=0`이고 자동 warning도 붙었다.

**SUB3 — 완료된 DL-9093/9094 아래 회귀 test.** 다섯 후보 모두 `items=0`으로 생성하지 않았다.
BASE/KO/EN/GUIDE는 생성할 수 없다고 하면서도 화면에 “초안” 표를 보여 혼동을 준다. KO-R의
“두 티켓 모두 완료되어 Sub-Task를 추가할 수 없습니다”가 가장 명료하고 안전하다.

**PASTE1 — 컬럼 설명이 안 보인다는 VoC.** BASE/EN/GUIDE는 Catalog Bug, KO는 component를
`사용자 VoC`로 둔 Bug, KO-R은 Workbench `Improvement`를 냈다. 원문은 기존 기능의 명시적 고장보다
조회 화면의 기능 gap에 가깝다. **KO-R의 issue type·module 해석이 가장 자연스럽고**, KO의
`사용자 VoC` component는 부적절하다.

**PASTE2 — 채팅 원문으로 야간 batch timeout Bug.** 모든 후보가 “어제도 같은 시간대”와 “매일
이러면 곤란”을 실제로 매일 반복되는 사실로 강화했다. KO-R은 자동 checker를 통과했지만 reply에
“이 문제는 매일 반복”이라고 썼다. 따라서 **사람 기준으로는 전부 의미 실패**다. 조건문을 관측 사실로
바꾸지 말아야 한다.

**ASK1 — ‘데이터 품질 개선해줘’.** 모두 `items=0`, 질문 4~5개로 범위·산출물·DoD를 물었다.
모호한 요청을 안전하게 멈춘 정상 동작이다. GUIDE가 가장 빠르고 KO-R이 가장 느렸지만 품질은 동률에
가깝다.

**ASK2 — 후속 답변 ‘널 비율 체크, 이번 주’.** BASE/KO/EN은 단일 Task를 실제로 냈다. GUIDE와
KO-R은 단일 Task로 가능하다고 말하면서 Task+5단계 구조를 제시하고 `items=0`, 질문 1개를 반환했다.
**BASE/KO/EN > GUIDE/KO-R**이다.

**DUP1 — 이미 진행 중인 Avro 전환.** 모두 생성하지 않았다. EN은 `DL-9072` 중복을, KO-R은
`DL-9072`와 “9개 토픽 중 6개 완료”까지 첫 답변에 명시했다. BASE/KO/GUIDE는 먼저 일반 질문만
해서 중복 근거가 덜 보인다. **KO-R > EN > 나머지**다.

**ATTR1 — P1·이번 주 금요일·hotfix.** BASE/KO/EN/KO-R은 실제 Task에 P1, `2026-08-14`,
`hotfix`를 보존했다. GUIDE는 단계 구조를 다시 확인하느라 `items=0`이다. 참고로 raw 입력은 “이번 주
금요일”이며 2026-08-14 변환은 맞다.

**ATTR2 — 신규 label quality-gate.** BASE/EN/GUIDE/KO-R은 label을 보존한 Task를 냈고 KO는
“생성 예정”이라고만 하며 `items=0`이다. BASE/EN의 body에는 “추가 필요”에 가까운 일반 문구가,
KO-R body에는 “이유를 설명해 주세요”, “DoD를 정의해 주세요” placeholder가 남았다. 실제 카드
완결성은 GUIDE가 상대적으로 낫지만 모두 더 구체적인 범위·DoD가 필요하다.

**STARR1 — 고유어가 많은 ETL pipeline.** BASE/EN/GUIDE/KO-R은 Task 1건과 설계·구현·검증 child
3건을 냈다. KO는 Epic+5단계를 화면에 제시한 뒤 `items=0`, 질문 1개를 반환했다. KO-R은 reply에서
“새로운 Epic”이라고 했지만 payload type은 `Task`여서 사용자 설명과 실행 계약이 충돌한다.
**BASE ≈ EN ≈ GUIDE > KO-R > KO**다.

**BUG1 — 재현 정보가 부족한 intermittent bug.** 실제로는 다섯 후보 모두 `items=0`, 질문 4개로
멈췄다. KO-R은 “추가 정보가 필요”하다고 가장 명확히 썼다. 자동 점수에서 KO-R만 통과한 차이는
사람 품질 차이보다 checker 민감도의 문제다.

**BUG2 — 재현 정보가 충분한 리니지 bug.** 모두 재현·기대·실제를 갖춘 Bug를 냈다. KO/GUIDE/KO-R은
Workbench와 담당 `skcc.x1402`를 선택했고, BASE/EN은 Observability를 선택했다. **KO-R ≈ KO ≈
GUIDE > BASE ≈ EN**이다.

**BUG3 — timeout bug와 기존 이력.** 다섯 후보 모두 실제 `items=0`, 질문 4개로 생성하지 않았다.
GUIDE는 관련 `DL-5235`, `DL-5405`를 제시했고 KO-R은 이해 확인 후 조사하겠다고 했다. EN은 자료와
무관한 `NullPointerException`을 끌어와 가장 위험하다. 자동 pass/fail보다 사람 기준으로 GUIDE와
KO-R이 안전하다.

**RULE1 — 부모 없는 Sub-Task.** 모두 `items=0`, 질문 4개였다. 그러나 BASE/KO/EN/GUIDE는 “부모
없이 독립 생성”이라는 잘못된 전제를 이해한 내용에 반복했다. KO-R만 “Sub-Task는 반드시 부모가
있어야 한다”고 명시했다. **KO-R이 가장 정확**하다.

**RULE2 — Story Point 5 요청.** BASE/KO/EN/GUIDE는 Story 1건을 만들되 실제 payload에 Story Point를
넣지 않아 생성 단계 규칙을 지켰다. EN은 사용자 답변에서 Story Point 5를 설정한다고 암시해 payload와
어긋난다. KO-R은 Story를 만들지 않고 Task+5단계 구조를 되물었다. **BASE ≈ KO ≈ GUIDE > EN >
KO-R**이다.

Create 사람 판단은 **BASE > GUIDE > KO > EN > KO-R**이다. KO-R은 BUG/중복/부모 규칙의 안전성이
좋아졌지만 ONE1·PAR1·SUB2·ASK2·RULE2에서 실행 가능성을 크게 잃었다. BASE도 SUB1 같은 자동
false positive와 PASTE2의 사실 강화 문제가 있어 절대 점수는 높지 않다.

## 8. 사람 기준 품질 점수

5점 만점이며 token·latency·cost를 빼고 실제 답변 품질만 평가했다. 정확성은 ticket/data 의미,
맥락 활용은 이미 제공된 evidence 사용, 완결성은 실제 payload와 다음 행동 가능성, 안전성은 중복·
근거 없는 생성·규칙 위반 방지를 뜻한다.

| 후보 | 정확성 | 맥락 활용 | 읽기 품질 | 완결성·실행성 | 안전성 | 종합 |
|---|---:|---:|---:|---:|---:|---:|
| BASE | 4.1 | 4.2 | 4.2 | **4.2** | 3.8 | **4.1** |
| GUIDE | 3.9 | **4.3** | **4.3** | 3.8 | 3.8 | **3.9** |
| KO | 3.8 | 4.0 | 4.0 | 3.7 | 3.6 | **3.8** |
| EN | 3.7 | 4.0 | 4.2 | 3.5 | 3.4 | **3.7** |
| KO-R | 3.5 | 3.7 | 4.0 | 3.2 | **4.0** | **3.5** |

순위는 **BASE > GUIDE > KO > EN > KO-R**이다. KO-R의 한국어 문장 자체는 자연스럽고 안전하게
멈추는 능력은 좋아졌지만, 이 agent의 핵심 가치인 “이미 있는 Jira context로 사용 가능한 초안을
만드는 능력”이 약해졌다. 이는 번역 품질 문제가 아니라 refactor 과정에서 구조 판단·중단 조건이
과도하게 강화된 문제다.

## 9. 권고안

### 유지할 것

- `AGENT.md`의 4계층 prompt 구조와 owner 원칙.
- 한국어 사용자-facing 지시, code/tool/schema/Jira 식별자 원형 보존.
- `PROMPT_VERSION`, raw output·usage 저장, prompt integrity test.
- prompt 비교 시 production model topology 고정 규칙.

### 지금 승격하지 않을 것

- `ko-refactored-v1` 전체를 production 기본 prompt로 교체하지 않는다.
- all-mini 결과를 prompt 언어 비교의 근거로 재사용하지 않는다.
- 자동 통과율만으로 품질 개선을 선언하지 않는다.

### KO-R v2의 우선 보강 항목

1. `progress`는 WBS rollup 가능 여부와 별개로 직접 ticket context를 먼저 답한다.
2. 사용자가 “알아서”라고 위임했고 기본값으로 안전하게 채울 수 있으면 구조 확인 질문을 생략한다.
3. reply에서 “초안을 준비했다”고 했으면 실제 `pending.items`가 비어 있지 않게 계약을 검증한다.
4. 단건 요청을 기계적으로 설계·구현·테스트 5단계로 과분해하지 않는다.
5. 완료된 parent, Sub-Task parent 규칙, 중복 ticket 차단은 KO-R의 개선 동작을 유지한다.
6. 채팅/VoC의 조건·희망 표현을 관측 사실로 강화하지 않는다.
7. known assignee가 context에 있으면 다시 묻지 않는다.
8. `SUB1`처럼 화면상 표와 실제 payload가 다른 case를 잡도록 checker를 강화한다.

v2는 위 회귀 case를 deterministic/semantic test로 먼저 고정한 뒤, BASE와 같은 mixed routing으로
최소 5회 교차 실행한다. 채택 gate는 사람 품질이 BASE 이상이고 자동 계약 점수가 최소 27/32이며,
그 조건 안에서 token/cost가 감소하는 것이다.

## 10. 반복성·한계

- 이번 표는 후보별 1회 실행이다. model 출력과 latency의 분산을 추정할 수 없다.
- 같은 KO-R prompt의 앞선 혼합 진단 run은 Compose 8/9, Create 17/23이었으나, 공식 동일-session
  재실행은 7/9와 15/23이었다. 이 차이는 단일 run으로 1~2점 우열을 확정하면 안 된다는 직접 증거다.
- Compose 하네스는 case latency와 결과 전문을 보존하지만 token 합계를 집계하지 않으므로 전체
  token 비교 표에는 대화·Create만 각각 제시했다.
- 자동 checker는 schema/구조 회귀를 빠르게 찾는 용도다. 사실의 주어, 조건문의 강화, reply/payload
  모순은 사람이 읽어야 한다.
- mock world에 맞춘 결과이므로 실제 Jira 권한·데이터 변화·network 지연을 대표하지 않는다.

## 11. 원시 증거

모든 파일은 repository 상위 workspace에 있으며 응답 전문과 사용량을 포함한다.

| 후보 | 대화 | Compose | Create |
|---|---|---|---|
| BASE | `../../ab-base-mixed.json` | `../../compose-base-mixed.json` | `../../create-base-mixed.json` |
| KO | `../../ab-ko-mixed.json` | `../../compose-ko-mixed.json` | `../../create-ko-mixed.json` |
| EN | `../../ab-en-mixed.json` | `../../compose-en-mixed.json` | `../../create-en-mixed.json` |
| GUIDE | `../../ab-guide-mixed.json` | `../../compose-guide-mixed.json` | `../../create-guide-mixed.json` |
| KO-R | `../../ab-ko-refactored-mixed.json` | `../../compose-ko-refactored-mixed.json` | `../../create-ko-refactored-mixed-full.json` |

과거 all-mini JSON은 진단 이력일 뿐 본 보고서의 어떤 수치·순위·결론에도 사용하지 않았다.

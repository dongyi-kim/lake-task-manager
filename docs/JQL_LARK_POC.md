# Lark JQL parser POC (#32)

## 결론

Lark 1.3.1로 현재 LTM JQL subset을 표현하고 기존 AST·DNF·캐시 정책을 재사용하는 것은
가능하다. 다만 현재 범위에서는 **프로덕션 파서를 교체하지 않는다.** 유지보수 형태는 더 명시적이지만,
코드량 감소와 실행 성능이라는 채택 기준을 충족하지 못했다.

이번 POC에서 프로덕션에 남길 가치가 있는 변경은 context-dependent function 처리를 syntax
parser 앞의 독립 전처리 단계로 분리한 것이다. 현행 parser와 Lark candidate가 동일한 전처리 결과를
소비하므로 `currentUser()`, 상대 날짜와 `start/endOf*` 정책이 parser 구현에 종속되지 않는다.

## 파이프라인

```text
raw JQL
  → quote-aware context preprocessor
      currentUser() → user id
      -14d / startOfWeek('-1w') → TTL bucket 기준 절대 시각
      now() / sprint functions → 유지
      unknown/plugin functions → JqlUnsupported
  → Lark LALR grammar
  → 기존 Atom / And / Or / Not AST adapter
  → 기존 flatten / canonical sort / NNF / DNF / leaf limits / local ORDER
```

Lark parse error와 미지원 문법은 모두 `JqlUnsupported`로 바뀌므로 Jira whole-query fallback 계약을
유지한다. POC module은 아직 `JiraClient`에 연결하지 않았고 Lark도 test dependency로만 둔다.

## 검증 결과

- LTM 실제 Task/workload 형태를 포함한 differential corpus에서 현행/candidate의 canonical JQL,
  leaf, predicate field, ORDER 결과가 동일하다.
- AND 순열, 중첩 OR/NOT, quoted text, `IN` 정렬, context 함수, 빈 query와 ORDER-only query를 검증했다.
- malformed/out-of-scope 문법, 16KiB 입력, 64 leaf, leaf당 64 atom 제한은 안전하게 fallback한다.
- 관련 회귀 suite: `113 passed`.

재현 명령:

```powershell
python -m pytest -q tests/test_jql_lark_poc.py tests/test_jql_processor.py `
  tests/test_mytasks_async.py tests/test_workload.py tests/test_search.py tests/test_search_recall.py
python -m tools.benchmark_jql_parsers --iterations 2000
```

## 비용 측정

Windows / Python 3.11.15, 6-query corpus, 12,000 compile 기준:

| 항목 | 결과 |
|---|---:|
| 현행 cold import delta | 42.928 ms |
| Lark cold import delta | 105.375 ms |
| Lark 최초 grammar 준비 + compile | 18.244 ms |
| 현행 warm compile | 147.227 µs/op |
| Lark warm compile | 353.891 µs/op |
| warm slowdown | 2.404x |
| 설치된 Lark package | 약 895 KiB |
| 기존 recursive syntax parser | 78 nonblank LOC |
| Lark grammar + adapter | 105 nonblank LOC |

Jira network latency와 비교하면 절대 시간은 작지만, 캐시 키를 만드는 hot path에서 더 느리고 코드도
27 LOC 늘어난다. 따라서 지금 교체하면 dependency와 문법 이원화 비용이 이득보다 크다.

## 라이브러리 상태와 패키징 판단

- Lark 1.3.1은 Python 3.8+를 지원하는 pure-Python stable release이며 MIT license, 외부 runtime
  dependency가 없다: <https://pypi.org/project/lark/>
- 현재 grammar는 빠르고 메모리 사용이 작은 LALR(1) + contextual lexer를 사용한다:
  <https://lark-parser.readthedocs.io/en/latest/parsers.html>
- Lark standalone generator 산출물은 별도 MPL-2.0 license이므로 사용하지 않는다. 채택 시에도 MIT
  runtime package를 exact pin하는 방식을 유지한다:
  <https://github.com/lark-parser/lark/blob/master/lark/tools/standalone.py>

## 재검토 조건

다음 중 하나가 생기면 교체를 다시 평가한다.

- 지원해야 할 JQL 연산자·history predicate가 늘어 recursive parser 유지비가 grammar adapter 비용을
  넘는 경우
- Jira 문법 오류 위치를 UI에 구조적으로 제공해야 하는 경우
- 기존 parser를 제거한 최종 diff에서 실제 LOC 감소가 확인되는 경우

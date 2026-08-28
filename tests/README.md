# Test suite

테스트는 제품 책임과 같은 경계로 나눈다. 새 회귀 테스트는 가장 좁은 기능 폴더에 둔다.

- `agent/`: Agent 핵심 동작, 계약, 평가, 통합
- `jira/`: JQL·검색, 티켓 쓰기, workload, 사람, 콘텐츠 변환
- `frontend/`: 에디터, 앱 셸, 정적 UI 계약, 티켓 다이어로그
- `routes/`: HTTP route와 route 조합
- `runtime/`: 인증, 설정, 런처, 프로세스 수명주기
- `domain/`: Jira와 무관한 순수 도메인 계산
- `quality/`: 의존성·소스·CI 정책 검사
- `support/`: 여러 기능 폴더가 공유하는 test-only helper

개발 중에는 변경 범위만 빠르게 실행할 수 있다.

```powershell
python -B -m pytest tests/frontend/ticket_dialog -q
python -B -m pytest tests/jira/workload -q
python -B -m pytest tests/agent/contracts/test_agent_work_final_contract.py -q
```

전체 회귀는 다음처럼 실행한다. 임시 파일과 pytest cache는 저장소의 허용된 캐시 폴더만 사용한다.

```powershell
python -B -m pytest -q --basetemp=.cache/test-tmp/full
```

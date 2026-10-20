# 공개 LTM 용어집

범위는 공개 LTM 코드다. 사내 약어·조직·제품의 다른 뜻을 확인 없이 연결하지 않는다.

| 용어 | 정의와 구분 |
|---|---|
| Epic | Task-tier 묶음. WBS Task와 1:1이 아님 |
| Task-tier | Epic 아래 Task/Story/Bug 등 일반 유형 계층. Sub-Task와 다름 |
| WBS Task | YAML 계획 단위. 같은 이름의 Jira Task와 다름 |
| SP | Story Point 계산 입력. 티켓 개수·일정 완료율과 다름 |
| PMO_VIT | 현안 root label. 자손에 중복 적용하지 않음 |
| Component | Jira component. 모듈 관계는 현재 설정으로 확인 |
| EvidencePack | 권한 있는 조회·계산과 provenance의 원격 입력. 정책 Knowledge와 다름 |
| Knowledge | Role별 규칙·형식·검증 절차. 현재 Jira 사실의 citation 근거가 아님 |
| source / citation / claim | 근거 자료 / 참조 연결 / 뒷받침할 주장. schema 통과가 사실성 보장은 아님 |
| freshness / partial | 조회 시점·신선도 / 일부 자료. 전체 현재 상태로 과신하지 않음 |
| draft / approval / write | 검토 문안 / exact payload 승인 / 실제 변경. Editor AI 삽입은 저장이 아님 |
| local / shadow / maxis | 로컬 판단 / 원격 관찰 비교 / 원격 authoritative route |
| release sentinel | 게시 Knowledge와 profile의 릴리스 표식. credential·업무 사실과 다름 |
| DEV / PROD | 독립 MAXIS 자산·권한·검증 환경. JIRA_ENV와 별도 축 |
| MCP | tool/resource 연결 protocol. Dify 호출만으로 등록되지 않음 |

모르는 약어는 원어·사용 위치를 보존하고 구체적으로 확인한다.
code/SQL/JQL, field/type, key/user ID/URL은 번역하지 않는다.

출처: app/agent/AGENT.md, app/agent/maxis/contracts.py, docs/MAXIS_DIFY_MIGRATION.md.

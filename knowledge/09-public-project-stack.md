# 공개 LTM 기술 구성과 실행 경계

공개 저장소의 LTM 기술만 설명한다. 사내 Lake 플랫폼의 실제 설치 제품·host·용량·운영 상태 자료가 아니다.

## 앱과 저장

LTM은 Python FastAPI backend와 Vue 3 무빌드 SPA다. Uvicorn이 HTTP를 제공하고 TipTap이 본문·댓글을 편집한다.
YAML은 설정·WBS/인력 매핑, SQLite는 cache/snapshot, FAISS는 로컬 정적 검색 색인에 사용된다.
실제 패키지 version은 requirements 파일이 정본이다.
JIRA_ENV=mock은 jira820 in-process 합성 world, local은 같은 fixture의 HTTP 전송, prod는 실제 서비스 인증 경로다.
mock 키·사람·테이블은 운영 데이터가 아니다. 개발은 python run.py --reload와 기본 127.0.0.1:4457을 사용한다.
Windows 개발 listener는 LakeTaskManagerDev.exe다.

## Agent와 원격 경계

LangGraph semantic Role과 deterministic service를 구분한다. Query Runner는 scope 안에서 조회한다.
Action Executor만 승인된 write를 실행한다. Dify는 MAXIS의 Workflow API로 정책 검색·의미 판단·초안을 담당하며
Jira credential과 approval token을 받지 않는다.
ContextGuard, Knowledge Guard, Contract Guard, ResponseGuard가 각 경계 입력·출력을 검증한다.
Jira DC 8.20.8, Confluence DC 9.2.4, Bitbucket DC 7.17.2는 공개 repository의 호환 대상이다.
실제 배포·접근·권한은 별도 확인한다. Bitbucket은 code/repo 검색 범위다.
Dify API 연결만으로 native MAXIS Agent/MCP 등록·TaskFlow 전체 UI가 완성됐다고 하지 않는다.

출처: AGENTS.md, requirements.txt, app/agent/AGENT.md, docs/MAXIS_DIFY_MIGRATION.md.

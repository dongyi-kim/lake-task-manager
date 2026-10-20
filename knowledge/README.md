# 공개 LTM Knowledge 정본

01~10 Markdown은 공개 저장소의 규칙·코드·제품 계약만 설명한다.
사내 문서 원문·직원 명단·비공개 프로젝트 기술 구성·실제 운영 현황을 공개 Git에 넣지 않는다.
정책 Knowledge는 현재 Jira/Confluence 사실의 citation 근거를 대신하지 않는다.

config/maxis-knowledge.yml이 각 문서의 설명·content SHA-256·revision·review·Role ACL 정본이다.
01 티켓, 02 진척, 03 모듈/사람, 04 분해, 05 사용, 06 조사, 07 본문,
08 Editor, 09 공개 LTM 기술, 10 용어를 서로 다른 목적으로 유지한다.
파일명은 안정적으로 유지하며 날짜별·버전별 복사본을 추가하지 않는다.

## 배포

깨끗한 checkout의 이 문서와 canonical DSL만으로 PROD import source를 생성할 수 있다.
DEV 배포 metadata나 .cache corpus는 필수가 아니다.
환경별 dataset/document/sentinel UUID·model·app name은 해당 환경에서 명시한다.
다른 환경 metadata가 있으면 UUID 재사용을 비교하며 metadata가 없다는 이유로 PROD 생성을 막지 않는다.
사내 전용 manifest를 별도로 선택할 수 있으나 공개 정본으로 조용히 폴백하지 않는다.

파일 저장은 원격 게시가 아니다. 원문·청크·ACL·manifest·sentinel·게시 Workflow pin을 검증한다.
연결·정적 계약·원격 검색·의미 품질·사용자 UI 왕복·운영 승인은 별도 게이트다.
실행 산출물·원문·평가·배포 영수증은 ignored .cache/에만 보관한다.
개인 LLM을 쓰는 로컬 FAISS 색인과 MAXIS Knowledge 배포는 별개다.

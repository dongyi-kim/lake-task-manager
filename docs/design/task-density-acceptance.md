# 자손 수별 Task 검증

`test_task_density_ui.py`는 기존 NDJSON 스트림 가로채기 방식과 `editor_browser` fixture를 사용한다. 부모의 `atoms`에 실제 자손을 각각 24·48·200개 넣으며 전체 Task 합계를 자손 수로 대신하지 않는다. 제품 코드는 변경하지 않았다.

검증 조합은 자손 24개/1280px, 48개/1440px, 200개/1720px이다. 전체 폭×자손×테마 교차 검증을 뜻하지 않는다. 각 사례에서 다음을 확인한다.

- 부모 접힘과 마지막 자손 검색, 검색 종료 시 기존 접힘 복구
- 부모 펼침과 별도 CSS peek 더보기, 전체 자손 접근과 재접기/펼치기
- 필터 적용 후 모든 자손이 검색 대상에 남는지 N→0→N 확인
- 모든 자손을 실제로 스크롤하며 제목 중심 hit-test와 조상 overflow 잘림 확인
- 인라인 행 24~40px, 부모 두 줄 카드 40~68px, 문서 가로 넘침 및 추가 스트림 조회 없음

세 조합 모두 실제 Chromium에서 통과했다. 마지막 자손 검색·필터 N→0→N·전체 행 스크롤/hit-test·접기/펼치기·밀도·추가 스트림 조회 없음 확인. JUnit은 외부 조사 폴더의 `tabs-final-targets.xml`에 보존하며, 이 실행에 포함된 별도 멘션·스크롤 테스트 두 건의 실패와 구분한다. 해당 두 건은 `targeted-fixture-rerun.xml`에서 수정 후 통과했다. 상세 geometry JSON은 기존 fixture의 OS 임시 폴더에서 검사한 뒤 함께 정리했다.

앱 저장소 루트에서 실행한다. 다른 4457 개발 서버를 종료한 뒤 `LTM_TEST_PORT=4457`로 실행한다. 테스트는 기존 fixture의 임시 mock 서버를 관리한다.

```powershell
@'
import os, tempfile, pytest
os.environ['LTM_TEST_PORT'] = '4457'
with tempfile.TemporaryDirectory() as run:
    code = pytest.main(['tests/frontend/mytasks/test_task_density_ui.py', '-q', '--basetemp', run + '/work', '-o', 'cache_dir=' + run + '/cache'])
raise SystemExit(code)
'@ | & ../.venv/Scripts/python.exe -B
```

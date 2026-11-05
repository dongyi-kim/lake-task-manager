# Confluence storage XHTML

`storage`는 Confluence 원문이다. 기존 매크로를 LTM/Jira callout이나 HTML div로 바꾸지 않는다. 서버에 설치되지 않은 매크로는 새로 만들지 않고 기존 원문을 보존한다. 형식의 정본은 [Confluence 9.2 storage 문서](https://confluence.atlassian.com/conf92/confluence-storage-format-1477576006.html)다.

## 일반 텍스트·표·코드·정보 패널

```xml
<h2>진척 상황</h2>
<p><strong>완료</strong>한 범위와 <em>확인할 사항</em>을 구분합니다.</p>
<ul><li>확인한 사실</li><li>남은 작업</li></ul>
<table><tbody><tr><th>항목</th><th>상태</th></tr><tr><td>적재</td><td>검증 중</td></tr></tbody></table>
<ac:structured-macro ac:name="code"><ac:parameter ac:name="language">sql</ac:parameter><ac:plain-text-body><![CDATA[select * from example;]]></ac:plain-text-body></ac:structured-macro>
<ac:structured-macro ac:name="info"><ac:rich-text-body><p>확인된 안내</p></ac:rich-text-body></ac:structured-macro>
```

CDATA 내용에 `]]>`가 있으면 구간을 나누어 XML이 깨지지 않게 한다. XML 속성·텍스트의 특수 문자를 적절히 escape한다. `ac:macro-id` 등 기존 속성은 유지한다.

## 문서·Jira·URL·사람 링크

```xml
<p><ac:link><ri:page ri:space-key="SPACE_FROM_LOOKUP" ri:content-title="TITLE_FROM_LOOKUP" /></ac:link></p>
<p><a href="VERIFIED_JIRA_ISSUE_URL">VERIFIED_ISSUE_KEY</a></p>
<p><a href="VERIFIED_EXTERNAL_URL">자료</a></p>
<p><ac:link><ri:user ri:userkey="VERIFIED_CONFLUENCE_USER_KEY" /></ac:link></p>
```

예제의 대문자 값은 실제 조회값으로 교체한다. Jira username과 Confluence userKey가 같다고 추정하지 않는다. Confluence 사용자 식별자를 확인하지 못했다면 이름으로 가짜 멘션을 만들지 않고 일반 텍스트로 작성하거나 식별 정보를 확인한다. Jira smart macro는 서버 설정의 application link ID가 필요할 수 있으므로 확인되지 않은 ID 대신 실제 Jira URL 링크를 사용한다.

## 첨부 이미지·파일

```xml
<ac:image><ri:attachment ri:filename="UPLOADED_FILENAME.png" /></ac:image>
<p><ac:link><ri:attachment ri:filename="UPLOADED_FILENAME.pdf" /></ac:link></p>
```

파일은 먼저 해당 페이지에 업로드한다. 다른 페이지의 첨부는 소유 페이지를 명시해야 하며 파일 이름만으로 현재 페이지에 존재한다고 가정하지 않는다. 문서 이동은 첨부의 소유 문서 ID를 바꾸지 않는다. 첨부 삭제 시 기존 이미지·다운로드 링크가 깨질 수 있다.

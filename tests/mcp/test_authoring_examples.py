"""Published guide examples survive the same MCP write/read/edit path used by agents."""

import base64
from html import escape
from html.parser import HTMLParser
import json

import anyio
from mcp import Client
import pytest

from app.content.confluence_xml import AC, parse_storage


class Elements(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def assert_rich_content(text, *, editing):
    nodes = Elements(text).tags
    boxes = [attrs for tag, attrs in nodes if tag == "input" and attrs.get("type") == "checkbox"]
    assert len(boxes) == 3, text
    assert sum("checked" in attrs for attrs in boxes) == 1, text
    assert {attrs.get("data-cb-id") or attrs.get("id") for attrs in boxes} >= {"native-done", "native-open"}, text
    for tag in ("table", "pre", "img"):
        assert any(name == tag for name, _ in nodes), (tag, text)
    assert "source_table" in text and "요청된 범위" in text
    assert any(name == "a" and attrs.get("href") == "https://example.com/browse/DL-1"
               for name, attrs in nodes)
    assert any(name == "a" and attrs.get("href") == "https://example.com/pages/123"
               for name, attrs in nodes)
    if editing:
        assert any(attrs.get("data-id") == "u02" and attrs.get("data-type") == "mention"
                   for _, attrs in nodes), text


@pytest.mark.parametrize("storage", ["wiki", "html"])
def test_served_examples_create_read_edit_and_republish(build_env, monkeypatch, storage):
    env = build_env()
    env.runtime.settings.description_format = env.runtime.settings.comment_format = storage
    monkeypatch.setattr(env.client, "_reprime", lambda *_args, **_kwargs: None)
    if storage == "html":
        # Jira820 normally emulates Wiki renderer. Emulate the production JEditor
        # renderer only; all LTM serializers, commands, reads and caches stay real.
        import jira820.serialize as serializer
        original = serializer.render_wiki
        monkeypatch.setattr(serializer, "render_wiki", lambda body, *args:
                            body if (body or "").lstrip().startswith("<") else original(body, *args))

    async def scenario():
        async with Client(env.runtime.server) as client:
            async def call(name, arguments):
                result = await client.call_tool(name, arguments)
                assert not result.is_error, result.structured_content
                return result.structured_content["data"]

            guide = await call("get_content_guide", {
                "skill": "ltm-task-authoring", "path": "references/examples.json"})
            samples = json.loads(guide["text"])
            key = (await call("create_task", {"body": {"type": "Task", "summary": "Guide sample",
                    "clientMutationId": "guides:create:0001"}}))["key"]
            image = await call("add_attachment", {"key": key, "filename": "sample.png",
                "base64_data": base64.b64encode(bytes.fromhex(
                    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                    "0000000b49444154789c636000020000050001a5f645400000000049454e44ae426082")).decode(),
                "content_type": "image/png", "client_mutation_id": "guides:image:0001"})
            values = {"USER_ID": "u02", "DISPLAY_NAME": "Example User", "TASK_TYPE": "Task",
                      "JIRA_URL": "https://example.com/browse/DL-1", "ISSUE_TITLE": "Reference ticket",
                      "CONFLUENCE_URL": "https://example.com/pages/123", "DOCUMENT_TITLE": "Reference document",
                      "ATTACHMENT_PATH": image["path"], "ATTACHMENT_FILENAME": image["filename"]}

            def content(kind):
                text = samples[kind]
                for name, value in values.items():
                    text = text.replace("__" + name + "__", escape(value, quote=True))
                # Read/edit HTML uses native inputs, whereas new editor output uses
                # TipTap task lists. Both forms must survive the same command path.
                return text + ('<p>체크 항목: <input type="checkbox" data-cb-id="native-done" checked>'
                               '완료 <input type="checkbox" id="native-open">미완료</p>')

            # Include a unique phrase shared by the body/comment structural assertions.
            body = content("descriptionHtml") + "<p>요청된 범위</p>"
            await call("update_description", {"key": key, "html": body,
                                               "client_mutation_id": "guides:body:0001"})
            for iteration in range(2):
                ticket = await call("get_ticket", {"key": key, "fresh": True})
                assert_rich_content(ticket["descriptionHtml"], editing=False)
                assert_rich_content(ticket["descriptionEditHtml"], editing=True)
                if iteration == 0:
                    await call("update_description", {"key": key,
                        "html": ticket["descriptionEditHtml"] + "<p>본문 추가 확인</p>",
                        "client_mutation_id": "guides:body-edit:0001"})
                else:
                    assert "본문 추가 확인" in ticket["descriptionHtml"]

            cid = str((await call("add_comment", {"key": key, "html": content("commentHtml"),
                                                  "client_mutation_id": "guides:comment:0001"}))["id"])
            for iteration in range(2):
                comments = (await call("get_comments", {"key": key}))["comments"]
                current = next(row for row in comments if str(row["id"]) == cid)
                assert_rich_content(current["html"], editing=False)
                source = await call("get_comment_source", {"key": key, "comment_id": cid})
                assert_rich_content(source["html"], editing=True)
                if iteration == 0:
                    await call("edit_comment", {"key": key, "comment_id": cid,
                        "html": source["html"] + "<p>댓글 추가 확인</p>",
                        "client_mutation_id": "guides:comment-edit:0001"})
                else:
                    assert "댓글 추가 확인" in current["html"]
            assert len(env.store.issues[key]["comments"]) == 1

            items = json.loads(json.dumps(samples["bulkItems"]).replace("__TASK_TYPE__", "Task"))
            validation = await call("bulk_validate", {"mode": "task", "items": items})
            assert validation["ok"], validation
            bulk = await call("bulk_create", {"mode": "task", "items": items,
                                               "client_mutation_id": "guides:bulk:0001"})
            assert bulk["ok"] and len(bulk["created"]) == 1
            result = await call("get_ticket", {"key": bulk["created"][0]["key"], "fresh": True})
            for token in ("<table", "<pre", "source_table", "https://example.com/reference"):
                assert token in result["descriptionHtml"]

    anyio.run(scenario)


def test_confluence_editor_html_page_comment_create_read_edit_roundtrip(build_env):
    from app.mock.confluence import install_confluence
    env = build_env()
    env.runtime.settings.confluence_base = "http://confluence.test"
    env.runtime.settings.search_confluence_spaces = ["DL"]
    install_confluence(env.provider._client.app)
    state = env.provider._client.app.state.confluence
    body = ('<h2>작업 목표</h2><ul data-type="taskList"><li data-type="taskItem" data-checked="false">검증</li></ul>'
            '<p><input type="checkbox" checked>확인</p><table><tr><td>테이블</td></tr></table>'
            '<pre><code class="language-sql">select 1;</code></pre><div class="callout callout-info"><p>안내</p></div>')
    async def scenario():
        async with Client(env.runtime.server) as sdk:
            async def call(name, args):
                result = await sdk.call_tool(name, args)
                assert not result.is_error, result.structured_content
                return result.structured_content['data']
            page = await call('create_confluence_page', {'space':'DL','title':'Shared input','body':body,
                'format':'editor_html','editor_options':{'numbered':True}, 'client_mutation_id':'shared:create:page'})
            page_id = page['id']
            stored = state.get(page_id)['body']['storage']['value']
            root = parse_storage(stored)
            assert [x.text for x in root.iter(AC+'task-status')] == ['incomplete','complete']
            assert root.find('h2').text == '1 작업 목표'
            assert len(root.findall('.//table')) == 1
            assert {x.get(AC+'name') for x in root.iter(AC+'structured-macro')} == {'code','info'}
            read = await call('read_confluence_document', {'page':page_id,'format':'storage'})
            assert read['content'] == stored
            comment = await call('create_confluence_comment', {'page_id':page_id,'body':body,
                'format':'editor_html','client_mutation_id':'shared:create:comment'})
            comment_id = comment['id']
            comment_before = state.get(comment_id)['body']['storage']['value']
            await call('read_confluence_comment', {'page_id':page_id,'comment_id':comment_id})
            # Existing storage editing is exact preservation; native macros never pass an HTML sanitizer.
            await call('update_confluence_page', {'page_id':page_id,'expected_version':1,
                'body':stored + '<p>추가 확인</p>', 'format':'storage','client_mutation_id':'shared:update:page'})
            await call('update_confluence_comment', {'page_id':page_id,'comment_id':comment_id,'expected_version':1,
                'body':comment_before + '<p>추가 확인</p>', 'format':'storage','client_mutation_id':'shared:update:comment'})
            for content_id, original in [(page_id, stored), (comment_id, comment_before)]:
                updated = state.get(content_id)
                assert updated['body']['storage']['value'] == original + '<p>추가 확인</p>'
                assert updated['version']['number'] == 2
            await call('read_confluence_document', {'page':page_id,'format':'storage'})
            await call('read_confluence_comment', {'page_id':page_id,'comment_id':comment_id})
            before = len(state.pages)
            failed = await sdk.call_tool('create_confluence_page', {'space':'DL','title':'Reject image',
                'body':'<img src="blob:missing">','format':'editor_html','client_mutation_id':'shared:reject:image'})
            assert failed.is_error and len(state.pages) == before
    anyio.run(scenario)

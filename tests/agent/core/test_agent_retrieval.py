"""agent/retrieval — 정적 색인 + 동적 증분 색인.

증분 색인의 주장은 하나다: **안 바뀐 건 다시 임베딩하지 않는다.** 그 주장은 "돌아간다"로
증명되지 않으므로, 여기서는 실제 임베딩 호출 횟수를 세거나 `skipped` 를 확인한다.

fake 임베딩으로 돈다 — 벡터의 의미 품질이 아니라 **색인 살림살이**를 시험하는 테스트다.
"""
import os
import sys

import pytest

os.environ.setdefault("JIRA_ENV", "mock")

pytest.importorskip("langchain_core", reason="requirements-agent.txt 미설치")
pytest.importorskip("faiss", reason="faiss-cpu 미설치")

from app.agent.retrieval import dynamic_index, manifest, static_index      # noqa: E402
from app.agent.retrieval.chunk import split_markdown, split_text           # noqa: E402


@pytest.fixture(autouse=True)
def fake_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "fake")
    # 색인은 .cache 아래에 쌓인다 — 테스트가 개발자의 실제 색인을 밟지 않게 옮긴다.
    import app.infra.settings as S
    monkeypatch.setattr(S, "CACHE_DIR", tmp_path)
    static_index._cached.update(sig=None, store=None)
    dynamic_index._cached.update(sig=None, store=None)
    yield


# ── 조각내기 ───────────────────────────────────────────────────────
def test_markdown_splits_on_headings_not_mid_table():
    md = "# 규칙\n\n## 첫 절\n\n내용 A\n\n## 둘째 절\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
    parts = split_markdown(md, source="x.md")
    assert len(parts) == 2
    assert "내용 A" in parts[0]["text"] and "| 1 | 2 |" in parts[1]["text"]


def test_each_chunk_carries_its_heading_path():
    """조각만 떼어 놓으면 '무엇에 대한 규칙인지'가 사라진다."""
    parts = split_markdown("# 티켓 규칙\n\n## Story Point\n\nStory 에만 매긴다.\n", source="01.md")
    assert parts[0]["text"].startswith("[01.md > 티켓 규칙 > Story Point]")


def test_long_section_is_split_with_overlap():
    body = "# T\n\n## 긴 절\n\n" + "\n\n".join(f"문단 {i} " + "가" * 200 for i in range(12))
    parts = split_markdown(body, source="x.md")
    assert len(parts) > 1
    assert all(len(p["text"]) <= 1600 for p in parts)


def test_plain_text_split_does_not_need_headings():
    assert split_text("본문만 있는 글", source="jira:DL-1")[0]["text"]


# ── 정적 색인 ──────────────────────────────────────────────────────
def test_static_index_builds_from_knowledge_dir():
    assert static_index.build() is not None
    st = static_index.stats()
    assert st["indexed"] and st["fresh"] is True
    assert "01-ticket-rules.md" in st["documents"]


def test_static_search_returns_usable_chunks():
    """검색이 **쓸 수 있는 모양의 청크**를 돌려주는가 — 순위가 아니라 배관을 본다.

    fake 임베딩은 의미 없는 벡터라 순위가 무작위다. "SP 규칙이 top-k 에 뜨는가"를 단언하면
    지식 문서를 하나 추가할 때마다 깨진다(07 가이드 추가로 실제로 깨졌다). 의미 품질은
    배터리(실 LLM)의 몫이다.
    """
    hits = static_index.search("Story Point 는 어디에 매길 수 있나", k=4)
    assert hits and len(hits) <= 4
    assert all(h.get("text") and h.get("source", "").endswith(".md") for h in hits)


def test_the_sp_rule_actually_gets_indexed():
    """규칙 본문이 색인 대상 청크에 실제로 들어가는가 — 검색 순위와 무관하게 보장돼야 한다."""
    from app.agent.retrieval.chunk import split_text
    path = static_index.knowledge_dir() / "01-ticket-rules.md"
    chunks = split_text(path.read_text(encoding="utf-8"), source=path.name)
    assert any("Story Point" in c["text"] for c in chunks), [c["text"][:40] for c in chunks]


def test_signature_changes_when_embedding_model_changes(monkeypatch):
    """★ 모델이 바뀌면 벡터 공간이 다르다 — 차원이 같으면 에러도 없이 조용히 틀린다."""
    before = static_index.signature()
    monkeypatch.setattr(static_index._cfg, "embed_model", lambda: "다른-모델")
    assert static_index.signature() != before


def test_signature_changes_when_a_rule_document_changes(monkeypatch, tmp_path):
    d = tmp_path / "kn"
    d.mkdir()
    (d / "a.md").write_text("# A\n\n## 절\n\n원본\n", encoding="utf-8")
    monkeypatch.setattr(static_index, "knowledge_dir", lambda: d)
    before = static_index.signature()
    (d / "a.md").write_text("# A\n\n## 절\n\n고쳤다\n", encoding="utf-8")
    assert static_index.signature() != before


def test_readme_is_not_indexed_as_a_rule():
    assert not any(n.lower() == "readme.md" for n in static_index.stats()["documents"])


# ── 동적 증분 색인 ─────────────────────────────────────────────────
DOC = {"doc_id": "jira:DL-1", "kind": "jira", "title": "CDC 도입", "url": "",
       "updated": "2026-08-01T10:00:00.000+0900",
       "text": "실시간 수집 파이프라인에 변경분 반영 방식을 도입한다. 소스 DB 영향도 확인 필요."}


def test_first_upsert_indexes_everything():
    r = dynamic_index.upsert([DOC])
    assert r["indexed"] == 1 and r["skipped"] == 0 and r["chunks"] >= 1


def test_reindexing_unchanged_docs_embeds_nothing(monkeypatch):
    """★ 증분 설계의 성적표. 여기가 0 이 아니면 이 구조는 아무 의미가 없다.

    반환값이 아니라 **실제 임베딩 호출**을 센다 — skipped 를 세 놓고 뒤에서 다시 임베딩하는
    코드도 반환값만 보는 테스트는 통과한다.
    """
    dynamic_index.upsert([DOC])

    from app.agent.fake import FakeEmbeddings
    calls = []
    real = FakeEmbeddings.embed_documents
    monkeypatch.setattr(FakeEmbeddings, "embed_documents",
                        lambda self, texts: (calls.append(len(texts)), real(self, texts))[1])

    r = dynamic_index.upsert([DOC])
    assert r["indexed"] == 0 and r["chunks"] == 0 and r["skipped"] == 1
    assert calls == [], f"안 바뀐 문서를 다시 임베딩했다: {calls}"


def test_changed_updated_triggers_reembedding():
    dynamic_index.upsert([DOC])
    r = dynamic_index.upsert([dict(DOC, updated="2026-08-05T09:00:00.000+0900")])
    assert r["indexed"] == 1


def test_same_updated_but_changed_body_still_reembeds():
    """2차 방어 — updated 가 안 움직였는데 내용이 바뀌는 경우가 실제로 있다."""
    dynamic_index.upsert([DOC])
    r = dynamic_index.upsert([dict(DOC, text=DOC["text"] + " 추가된 결정 사항.")])
    assert r["indexed"] == 1


def test_updating_a_doc_removes_its_old_version_from_search():
    """옛 조각을 안 지우면 '이미 취소된 방침'이 근거로 다시 나온다."""
    dynamic_index.upsert([dict(DOC, text="폴링 방식으로 간다")])
    dynamic_index.upsert([dict(DOC, updated="2026-08-05T09:00:00.000+0900",
                               text="폴링을 버리고 CDC 로 간다")])
    blob = " ".join(h["text"] for h in dynamic_index.search("방식", k=10))
    assert "폴링을 버리고" in blob
    assert "폴링 방식으로 간다" not in blob


def test_search_returns_one_row_per_document():
    dynamic_index.upsert([DOC, dict(DOC, doc_id="jira:DL-2", title="둘째")])
    ids = [h["doc_id"] for h in dynamic_index.search("파이프라인", k=5)]
    assert len(ids) == len(set(ids))


def test_changing_embedding_model_invalidates_the_whole_index(monkeypatch):
    dynamic_index.upsert([DOC])
    assert dynamic_index.stats()["documents"] == 1
    monkeypatch.setattr(dynamic_index._cfg, "embed_model", lambda: "다른-모델")
    dynamic_index._cached.update(sig=None, store=None)
    dynamic_index._load()                       # 서명 불일치를 만나면 통째로 버려야 한다
    assert manifest.stats()["documents"] == 0, "벡터만 버리고 장부를 남기면 조용히 고장난다"


def test_dynamic_stats_uses_public_faiss_id_mapping(monkeypatch):
    """Retrieval must not depend on the community adapter's private docstore._dict."""
    from types import SimpleNamespace

    store = SimpleNamespace(index_to_docstore_id={0: "jira:DL-1#0", 1: "jira:DL-1#1"})
    monkeypatch.setattr(dynamic_index, "_load", lambda: store)

    assert dynamic_index.stats()["vectors"] == 2


def test_empty_and_blank_docs_are_ignored():
    r = dynamic_index.upsert([{"doc_id": "", "text": "x"}, {"doc_id": "jira:X", "text": "  "}])
    assert r["indexed"] == 0


# ── 수확 (실시간 → 색인 → 의미검색) ────────────────────────────────
def test_harvest_indexes_live_results_and_returns_all_three_layers():
    from app.agent.retrieval.harvest import harvest
    from app.agent.tools import _ctx
    r = harvest(_ctx.client(), _ctx.settings(), "데이터", limit=4, deep=1)
    assert set(r) == {"live", "index", "semantic"}
    assert r["live"]["jira"], "12개월 world 인데 검색 결과가 없다"
    assert r["index"]["indexed"] >= 1


def test_harvest_second_run_skips_almost_everything():
    """같은 주제를 다시 파도 임베딩을 다시 하지 않는다 — 실사용에서 이게 비용을 결정한다."""
    from app.agent.retrieval.harvest import harvest
    from app.agent.tools import _ctx
    c, s = _ctx.client(), _ctx.settings()
    first = harvest(c, s, "수집", limit=4, deep=1)["index"]
    second = harvest(c, s, "수집", limit=4, deep=1)["index"]
    assert first["indexed"] > 0
    assert second["indexed"] == 0 and second["skipped"] == first["indexed"] + first["skipped"]


def test_ticket_doc_merges_comments_into_one_document():
    """코멘트를 따로 색인하면 무슨 티켓 이야기인지 모른 채 근거로 쓰인다."""
    from app.agent.retrieval.harvest import ticket_doc
    from app.agent.tools import _ctx
    c = _ctx.client()
    for it in ((c.search_issues("ORDER BY updated DESC", max_results=20)) or [])[:20]:
        key = it.get("key")
        if c.issue_comments(key, 5):
            d = ticket_doc(c, key)
            assert d["doc_id"] == f"jira:{key}" and "[코멘트" in d["text"]
            # 머리글만 있고 본문이 비면 안 된다 — 실제로 html 필드를 body 로 읽어 빈 코멘트를
            # 색인하던 버그가 이 약한 단언을 통과했었다.
            head, _, tail = d["text"].partition("[코멘트")
            assert len(tail.split("]", 1)[1].strip()) > 5, "코멘트 본문이 비어 있다"
            assert d["updated"], "updated 가 없으면 장부 1차 판정이 무력해진다"
            return
    pytest.skip("코멘트가 붙은 티켓을 못 찾음")


# ── 도구로 노출 ────────────────────────────────────────────────────
def test_rule_tool_answers_a_policy_question():
    from app.agent import tools as T
    hits = T.BY_NAME["search_rules"].invoke({"question": "담당자를 어떻게 고르나", "k": 3})
    assert hits and "rule" in hits[0]


def test_deep_search_tool_output_stays_small():
    import json
    from app.agent import tools as T
    r = T.BY_NAME["deep_search"].invoke({"topic": "파이프라인", "limit": 5})
    assert "keyword" in r and "similar" in r
    assert len(json.dumps(r, ensure_ascii=False)) < 12000

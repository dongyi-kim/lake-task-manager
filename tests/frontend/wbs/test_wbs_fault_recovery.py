"""WBS Epic-tree targeted retry contracts."""

from support.paths import REPO_ROOT


VIEW = REPO_ROOT / "app" / "static" / "components" / "views" / "WbsView.js"


def test_epic_tree_failure_uses_the_current_epic_key_and_is_retryable():
    src = VIEW.read_text(encoding="utf-8")

    assert "this.partial[e.ticket]" not in src
    assert "const miss = self.partial[p.epicKey];" in src
    assert "retryEpicTree(key, evict = true)" in src
    assert "api.evict(key);" in src
    assert "onRetry: miss ? () => self.retryEpicTree(p.epicKey) : null" in src
    assert 'retry.addEventListener("click"' in src


def test_epic_tree_retry_is_key_scoped_and_keeps_previous_rows_until_success():
    src = VIEW.read_text(encoding="utf-8")

    assert "const previous = Array.isArray(this.epicTree[key]) ? this.epicTree[key] : null;" in src
    assert "const requestId = this._treeReq[key]" in src
    assert "requestId === this._treeReq[key]" in src
    assert "this.epicTree[key] = previous || []; this.partial[key] = -1;" in src
    assert "else delete this.partial[key];" in src
    assert "Object.keys(this.partial).filter((key) => this.partial[key] === -1)" in src
    assert "this.retryEpicTree(key, false)" in src

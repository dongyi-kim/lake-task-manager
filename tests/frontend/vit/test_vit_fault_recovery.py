"""VIT request-generation, partial rendering, and retry contracts."""

from support.paths import REPO_ROOT


VIEW = REPO_ROOT / "app" / "static" / "components" / "views" / "VitView.js"


def test_module_requests_ignore_stale_generations_and_clear_recovered_errors():
    src = VIEW.read_text(encoding="utf-8")

    assert "const loadSeq = this._loadSeq = (this._loadSeq || 0) + 1;" in src
    assert "if (loadSeq !== this._loadSeq) return;" in src
    assert "const requestId = this._moduleSeq[module]" in src
    assert "loadSeq === this._loadSeq && requestId === this._moduleSeq[module]" in src
    assert "delete errors[module]; this.modErr = errors;" in src
    assert "else delete partial[module];" in src
    module_loader = src[src.index("loadModule(module"):src.index("retryModule(module")]
    assert "this.mods[m.module] = []" not in module_loader


def test_partial_module_rows_and_detail_failures_remain_visible_and_retryable():
    src = VIEW.read_text(encoding="utf-8")

    assert 'v-if="modPartial[m.module]"' in src
    assert 'v-if="mods[m.module] && mods[m.module].length" class="tbl"' in src
    assert "@click=\"retryModule(m.module)\"" in src
    assert "detailErr: {}" in src and "detailLoading: {}" in src
    assert "const requestId = this._detailSeq[key]" in src
    assert "delete errors[key]; this.detailErr = errors;" in src
    assert "@click=\"retryDetail(it)\"" in src
    assert 'v-if="detail[it.key]" class="dcols"' in src
    assert "this.detail[it.key] = { tree: [], comments: [], error:" not in src

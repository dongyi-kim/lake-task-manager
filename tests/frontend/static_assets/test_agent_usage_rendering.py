"""Executable UI contracts for honest MAXIS usage/debug text."""
import json
import shutil
import subprocess

import pytest

from frontend.static_assets.support import STATIC


def _render(expression):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for JavaScript helper tests")
    source = (STATIC / "components/agent/usageDisplay.js").read_text(encoding="utf-8")
    script = source.replace("export function ", "function ") + "\nconsole.log(JSON.stringify(" + expression + "));"
    result = subprocess.run([node, "--input-type=module", "-e", script],
                            check=True, capture_output=True, text=True, encoding="utf-8")
    return json.loads(result.stdout)


def test_failed_maxis_attempts_show_partial_known_tokens_not_free_cost():
    text = _render("""usageSummary({calls:2,remoteCalls:2,failedCalls:1,totalTokens:5654,
        unknownUsageCalls:1,usageComplete:false,costStatus:"unknown"})""")
    assert "MAXIS 요청 2회" in text and "실패 1회" in text
    assert "확인된 5,654 토큰" in text and "1회 미확인" in text
    assert "비용 미확인" in text and "$0" not in text


def test_zero_known_usage_does_not_hide_failed_calls_or_claim_zero_tokens():
    text = _render("""usageSummary({calls:1,remoteCalls:1,failedCalls:1,totalTokens:0,
        unknownUsageCalls:1,usageComplete:false})""")
    assert "MAXIS 요청 1회" in text and "토큰 미확인" in text
    assert "0 토큰" not in text


def test_local_known_zero_cost_remains_a_known_measurement():
    text = _render("usageSummary({calls:1,totalTokens:25,costUsd:0})")
    assert "LLM 1회" in text and "25 토큰" in text and "$0.0000" in text


def test_maxis_debug_uses_turn_runtime_and_never_invents_a_model():
    lines = _render("""runtimeDebugLines({runtime:{engine:"maxis",profile:"profile-one"},
        usage:{model:"maxis/dify"}})""")
    assert "- engine: maxis" in lines and "- profile: profile-one" in lines
    assert any("서버 Workflow" in line and "미공개" in line for line in lines)
    assert not any("(none)" in line for line in lines)


def test_debug_reports_mixed_engine_turn_and_local_observed_model():
    lines = _render("""runtimeDebugLines({runtime:{engine:"local",roleEngines:{a:"maxis",b:"local"}},
        usage:{model:"local-model",remoteCalls:1}})""")
    assert "- engine: mixed (local, maxis)" in lines
    assert any("local-model" in line and "미공개" in line for line in lines)

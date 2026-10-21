"""Public progress contract on the real, uninstrumented producer."""
import pytest

pytest.importorskip("langchain_core", reason="requirements-agent.txt 미설치")

from support.progress_services import (
    Services, assert_public_contract, progress_runtime, report_contract,
)


def test_public_report_bytes_calls_and_tool_contract(progress_runtime, report_contract):
    service = Services()
    result = progress_runtime.invoke(service, key=" t-1 ")
    assert_public_contract(progress_runtime, report_contract, result, service)

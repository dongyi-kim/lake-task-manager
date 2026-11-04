"""Real SDK discovery and preflight recovery; ambiguous writes stay protected."""

import anyio
from mcp import Client
import pytest

from app.auth.base import PermissionDenied, UpstreamBusy, UpstreamUnavailable


@pytest.mark.parametrize("fail_at", ["preflight", "preflight_busy", "post_response"])
def test_transition_preflight_failure_is_retryable_but_lost_post_keeps_unknown_receipt(build_env, fail_at):
    env = build_env()

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            created = await mcp.call_tool("create_task", {"body": {"type": "Task", "summary": "Transition recovery",
                "clientMutationId": "transition:recovery-create:001"}})
            key = created.structured_content["data"]["key"]
            options = await mcp.call_tool("list_options", {"kind": "transitions", "key": key})
            move = next(row for row in options.structured_content["data"]["options"] if row["toCategory"] == "inprogress")
            path = f"/rest/api/2/issue/{key}/transitions"
            original_get, original_post = env.provider.get_json, env.provider.post_json
            reads = 0

            def get(target, *args, **kwargs):
                nonlocal reads
                if target == path:
                    reads += 1
                    if reads == 2 and fail_at.startswith("preflight"):
                        error_type = UpstreamBusy if fail_at == "preflight_busy" else UpstreamUnavailable
                        raise error_type("authoritative metadata unavailable before POST")
                return original_get(target, *args, **kwargs)

            def post(target, *args, **kwargs):
                result = original_post(target, *args, **kwargs)
                if target == path and fail_at == "post_response":
                    raise UpstreamUnavailable("lost response after transition committed")
                return result

            env.provider.get_json, env.provider.post_json = get, post
            classified = []
            env.runtime.classify_error = lambda exc: classified.append(exc)
            arguments = {"key": key, "body": {"id": move["id"], "clientMutationId": "transition:recovery:001"}}
            failed = await mcp.call_tool("transition_ticket", arguments)
            assert failed.is_error
            error = failed.structured_content["error"]
            assert len(classified) == (0 if fail_at == "preflight_busy" else 1)
            if fail_at.startswith("preflight"):
                expected = "rate_limited" if fail_at == "preflight_busy" else "upstream_unavailable"
                assert error["code"] == expected and error["writeExecuted"] is False
                assert error["message"] and "다시 시도" in error["message"]
                assert error["status"] == (429 if fail_at == "preflight_busy" else 503)
                assert not any(method == "post" and target == path for method, target, _ in env.calls)
            else:
                assert error["code"] == "outcome_unknown" and error["repeatWrite"] is False
            for _ in range(2):
                recovered = await mcp.call_tool("transition_ticket", arguments)
                assert not recovered.is_error, recovered.structured_content
            assert sum(method == "post" and target == path for method, target, _ in env.calls) == 1

    anyio.run(scenario)


@pytest.mark.parametrize("payload,failed", [({"transitions": None}, True), ({"transitions": []}, False)])
def test_transition_discovery_distinguishes_incomplete_response_from_no_available_transition(build_env, payload, failed):
    env = build_env()
    original_get = env.provider.get_json

    def response(path, *args, **kwargs):
        return payload if path.endswith("/transitions") else original_get(path, *args, **kwargs)

    env.provider.get_json = response

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("list_options", {"kind": "transitions", "key": "DL-1"})
            assert result.is_error is failed
            if failed:
                assert result.structured_content["error"]["code"] == "upstream_unavailable"
            else:
                assert result.structured_content["data"]["options"] == []

    anyio.run(scenario)


@pytest.mark.parametrize("failure", [UpstreamUnavailable("network failure"), PermissionDenied("permission denied")])
def test_transition_discovery_failure_is_not_successful_empty_options(build_env, failure):
    env = build_env()
    original_get = env.provider.get_json

    def fail(path, *args, **kwargs):
        if path.endswith("/transitions"):
            raise failure
        return original_get(path, *args, **kwargs)

    env.provider.get_json = fail

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("list_options", {"kind": "transitions", "key": "DL-1"})
            assert result.is_error, result.structured_content
            assert result.structured_content["error"]["code"] == (
                "permission_denied" if isinstance(failure, PermissionDenied) else "upstream_unavailable")
            env.provider.get_json = original_get
            retried = await mcp.call_tool("list_options", {"kind": "transitions", "key": "DL-1"})
            assert not retried.is_error and retried.structured_content["data"]["options"]

    anyio.run(scenario)

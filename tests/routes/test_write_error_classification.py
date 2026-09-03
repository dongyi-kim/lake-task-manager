import asyncio
import io
import json
from types import SimpleNamespace

import pytest
from starlette.datastructures import UploadFile

from app.auth.base import PermissionDenied, SessionExpired, UpstreamUnavailable
from app.routes.ticket_commands import TaskBody, TransitionBody, build_ticket_command_router
from app.routes.tickets import CommentBody, build_ticket_router


def _endpoint(router, path):
    return next(route.endpoint for route in router.routes if route.path == path)


def _command_router(client):
    settings = SimpleNamespace(jira_base="", epic_link_field_id="customfield_10014")
    return build_ticket_command_router(
        get_client=lambda: client, settings=settings, may_edit=lambda _key: True,
        require_edit=lambda _key: None, session_user=lambda: {},
    )


@pytest.mark.parametrize("error", [SessionExpired("HTTP 401"), UpstreamUnavailable("timeout")])
def test_task_create_does_not_misclassify_auth_or_transport_as_bad_request(error):
    class Client:
        def task_types(self): return ["Task"]
        def desc_field_value(self, value): return value
        def create_child(self, *_args, **_kwargs): raise error

    endpoint = _endpoint(_command_router(Client()), "/api/task")
    with pytest.raises(type(error)):
        endpoint(TaskBody(type="Task", summary="보존할 입력"))


def test_comment_create_propagates_session_expiry_to_global_401_handler():
    class Client:
        def comment_field_value(self, value): return value
        def add_comment(self, *_args, **_kwargs): raise SessionExpired("idle session expired")

    endpoint = _endpoint(build_ticket_router(get_client=lambda: Client()),
                         "/api/ticket/{key}/comment")
    with pytest.raises(SessionExpired):
        endpoint("DL-1", CommentBody(html="초안", clientMutationId="ltm-comment:12345678"))


def test_post_transition_cascade_failure_does_not_turn_committed_write_into_failure():
    class Client:
        def comment_field_value(self, value): return value
        def do_transition(self, *_args, **_kwargs): return {"ok": True}
        def cascade_suggestion(self, *_args, **_kwargs):
            raise SessionExpired("session expired after transition committed")

    endpoint = _endpoint(_command_router(Client()), "/api/ticket/{key}/transition")
    response = endpoint("DL-1", TransitionBody(id="31"))
    assert response.status_code == 200
    assert json.loads(response.body) == {"ok": True, "cascade": None}


def test_transition_permission_denial_reaches_global_403_handler():
    class Client:
        def comment_field_value(self, value): return value
        def do_transition(self, *_args, **_kwargs):
            raise PermissionDenied("/rest/api/2/issue/DL-1/transitions")

    endpoint = _endpoint(_command_router(Client()), "/api/ticket/{key}/transition")

    with pytest.raises(PermissionDenied):
        endpoint("DL-1", TransitionBody(id="31", commentHtml="보존할 완료 근거"))


def test_transition_route_forwards_reconciliation_identity_and_mutation_id():
    calls = []

    class Client:
        def comment_field_value(self, value): return "wiki:" + value
        def do_transition(self, *args, **kwargs):
            calls.append((args, kwargs)); return {"ok": True}
        def cascade_suggestion(self, *_args, **_kwargs): return None

    endpoint = _endpoint(_command_router(Client()), "/api/ticket/{key}/transition")
    response = endpoint("DL-1", TransitionBody(
        id="31", targetStatusId="5", targetStatusName="Resolved",
        targetStatusCategory="done", commentHtml="완료 근거",
        clientMutationId="ltm-transition:12345678",
    ))

    assert response.status_code == 200
    assert calls == [(('DL-1', '31'), {
        "time_spent": None, "assignee": None, "resolution": None,
        "comment": "wiki:완료 근거", "target_status_id": "5",
        "target_status_name": "Resolved", "target_status_category": "done",
        "mutation_id": "ltm-transition:12345678",
    })]


def test_attachment_auth_failure_is_not_masked_by_cached_current_user():
    class Client:
        def upload_attachment(self, *_args, **_kwargs):
            raise SessionExpired("idle session expired")

        def current_user(self):
            raise AssertionError("cached identity must not be used as a session probe")

    endpoint = _endpoint(_command_router(Client()), "/api/ticket/{key}/attachment")
    upload = UploadFile(filename="draft.png", file=io.BytesIO(b"png"))

    with pytest.raises(SessionExpired):
        asyncio.run(endpoint("DL-1", upload))


def test_parent_candidate_auth_failure_is_not_rendered_as_empty_success():
    class Client:
        def parent_task_candidates(self, *_args, **_kwargs):
            raise SessionExpired("idle session expired")

    endpoint = _endpoint(_command_router(Client()), "/api/parent-task-candidates")
    with pytest.raises(SessionExpired):
        endpoint()

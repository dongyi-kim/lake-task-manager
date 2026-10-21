from app.agent.maxis import backend, shadow_eval


def test_backend_enqueues_shadow_with_public_deployment_dimensions(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        backend,
        "resolve_role",
        lambda *_a, **_k: backend.RoleResolution(route="shadow", profile="prod"),
    )
    monkeypatch.setattr(backend, "_profile_snapshot", lambda *_a, **_k: {
        "name": "prod",
        "fingerprint": "a" * 32,
        "knowledge_version": "knowledge-1",
        "workflows": {"agent_core": {"version": "workflow-1"}},
    })
    monkeypatch.setattr(
        backend, "role_payload", lambda *_a, **_k: {"guarded": "evidence-pack"},
    )

    def submit(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(shadow_eval, "submit", submit)
    local = {"summary": "authoritative local output"}

    accepted = backend.run_shadow(
        "work_architect",
        {"runtime_role_routes": {"work_architect": "shadow"}},
        ["private prompt"],
        {"type": "object"},
        output_kind="structured",
        local_output=local,
    )

    assert accepted is True
    assert captured["local_output"] == local
    dimensions = captured["dimensions"]
    assert dimensions == shadow_eval.ShadowDimensions(
        profile="prod",
        profile_fingerprint="a" * 32,
        workflow_key="agent_core",
        role_id="work_architect",
        workflow_version="workflow-1",
        knowledge_version="knowledge-1",
    )
    # Enqueueing never invokes the remote candidate on the authoritative call stack.
    assert callable(captured["invoke_remote"])


def test_shadow_candidate_uses_only_guarded_request_identity(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        backend,
        "resolve_role",
        lambda *_a, **_k: backend.RoleResolution(route="shadow", profile="prod"),
    )
    monkeypatch.setattr(backend, "_profile_snapshot", lambda *_a, **_k: {
        "name": "prod",
        "fingerprint": "a" * 32,
        "knowledge_version": "knowledge-1",
        "workflows": {"editor_author": {"version": "workflow-1"}},
    })
    monkeypatch.setattr(
        backend,
        "role_payload",
        lambda *_a, **_k: {"evidencePack": {"request_id": "guarded-request"}},
    )

    def invoke(*args, **kwargs):
        captured["invoke"] = (args, kwargs)
        return {"output": {}, "statePatch": {}, "metadata": {}}

    def submit(**kwargs):
        captured["submit"] = kwargs
        return True

    monkeypatch.setattr(backend, "invoke", invoke)
    monkeypatch.setattr(shadow_eval, "submit", submit)

    assert backend.run_shadow(
        "editor_author",
        {
            "thread_id": "raw-thread-must-not-enter-queue",
            "correlation_id": "raw-correlation-must-not-enter-queue",
        },
        ["private prompt"],
        {"type": "object"},
        output_kind="structured",
        local_output={"content_html": "<p>local</p>"},
    ) is True

    captured["submit"]["invoke_remote"]()
    args, kwargs = captured["invoke"]
    assert args[2:4] == ("", "")
    assert kwargs == {"allow_guarded_person_references": True}


def test_backend_does_not_enqueue_a_non_shadow_route(monkeypatch):
    monkeypatch.setattr(
        backend,
        "resolve_role",
        lambda *_a, **_k: backend.RoleResolution(route="local"),
    )
    monkeypatch.setattr(
        shadow_eval, "submit", lambda **_k: (_ for _ in ()).throw(
            AssertionError("must not enqueue")
        ),
    )

    assert backend.run_shadow(
        "work_architect", {}, [], {},
        output_kind="structured", local_output={"summary": "local"},
    ) is False
